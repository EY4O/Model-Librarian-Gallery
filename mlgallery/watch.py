"""The folder watcher: new generations appear in the library within seconds, without a rescan.

Windows tells us when something changes in a watched folder (watchdog). Changed paths are collected and,
once a file has stopped changing (a video can take a while to write), indexed on their own. A folder on a
drive that isn't there can't be watched: it's checked again every few minutes and rescanned when it's back.
"""
from __future__ import annotations

import os
import threading
import time

from . import scan

QUIET = 1.0           # seconds a file must stay the same size before it's indexed
OFFLINE_CHECK = 30    # how often a folder that isn't reachable is looked for again
GONE_CHECK = 5        # how often the folders that are online are checked still to be there (renamed, unplugged)
EXPECT_FOR = 30      # seconds a folder the Gallery itself renamed, moved or removed is known to have been (folders.py)

# folders the Gallery itself just renamed, moved or removed (its index follows them already): their events need no rescan
_expected: dict[str, float] = {}
_expected_lock = threading.Lock()


def expect(paths) -> None:
    """These folders are about to be renamed, moved or removed by the Gallery (and the folders inside them with them)."""
    until = time.time() + EXPECT_FOR
    with _expected_lock:
        for p in paths:
            _expected[os.path.normcase(os.path.normpath(p))] = until


def expected(path: str) -> bool:
    now = time.time()
    p = os.path.normcase(os.path.normpath(path))
    with _expected_lock:
        for k in [k for k, until in _expected.items() if until < now]:
            del _expected[k]
        return any(p == k or p.startswith(k + os.sep) for k in _expected)


class Watcher:
    def __init__(self, lib):
        self.lib = lib
        self._watches: dict[int, object] = {}   # folder id -> watchdog watch
        self._pending: dict[tuple[int, str], tuple[float, int]] = {}  # (folder, rel) -> (last event, last size)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._observer = None
        self.available = True
        self.paused = False  # (from the tray: no looking for new files until it's resumed, which looks through them all)

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        try:
            from watchdog.observers import Observer
        except ImportError:  # without watchdog: rescans still work, new files just wait for one
            self.available = False
            return
        self._observer = Observer()
        self._observer.daemon = True
        self._observer.start()
        for f in self.lib.folders():
            self.add(f)
        threading.Thread(target=self._loop, name="watcher", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        if self._observer:
            try:
                self._observer.stop()
            except Exception:
                pass

    def add(self, folder: dict) -> bool:
        if not self._observer or folder["id"] in self._watches or not os.path.isdir(folder["path"]):
            return False
        try:
            self._watches[folder["id"]] = self._observer.schedule(_Handler(self, folder), folder["path"], recursive=True)
            return True
        except OSError:
            return False

    def remove(self, folder_id: int) -> None:
        w = self._watches.pop(folder_id, None)
        if w and self._observer:
            try:
                self._observer.unschedule(w)
            except (KeyError, ValueError, OSError):
                pass
        with self._lock:
            self._pending = {k: v for k, v in self._pending.items() if k[0] != folder_id}

    def watching(self, folder_id: int) -> bool:
        return folder_id in self._watches

    # ---------------------------------------------------------------- events
    def touched(self, folder: dict, path: str) -> None:
        if self.paused:
            return
        rel = os.path.relpath(path, folder["path"]).replace("\\", "/")
        if rel.startswith("../") or not scan.kind_of(rel):
            return
        with self._lock:
            self._pending[(folder["id"], rel)] = (time.time(), -1)

    def dir_made(self, folder: dict, path: str) -> None:
        """A folder made (in Explorer, or in the sidebar): listed while it's empty, as a scan would."""
        rel = os.path.relpath(path, folder["path"]).replace("\\", "/")
        if rel.startswith("../") or rel == "." or any(scan.skip_dir(p, bool(self.lib.settings.get("skip_eagle"))) for p in rel.split("/")):
            return
        if scan.is_empty_dir(path):
            with self.lib.db.write() as c:
                c.execute("INSERT OR IGNORE INTO dirs (folder_id, rel) VALUES (?, ?)", (folder["id"], rel))

    def _loop(self) -> None:
        last_offline = last_gone = time.time()
        while not self._stop.wait(0.5):
            if self.paused:
                continue
            self._index_settled()
            if time.time() - last_gone > GONE_CHECK:
                last_gone = time.time()
                self._check_gone()
            if time.time() - last_offline > OFFLINE_CHECK:
                last_offline = time.time()
                self._check_offline()

    def _index_settled(self) -> None:
        now = time.time()
        ready: dict[int, list[str]] = {}
        with self._lock:
            items = list(self._pending.items())
        folders = {f["id"]: f for f in self.lib.folders()}
        for (fid, rel), (at, size) in items:
            if now - at < QUIET or fid not in folders:
                continue
            full = os.path.join(folders[fid]["path"], *rel.split("/"))
            try:
                cur = os.path.getsize(full)
            except OSError:
                cur = -2  # gone: indexed as missing
            with self._lock:
                if cur != size and cur != -2:  # still being written: check again after another quiet spell
                    self._pending[(fid, rel)] = (now, cur)
                    continue
                self._pending.pop((fid, rel), None)
            ready.setdefault(fid, []).append(rel)
        for fid, rels in ready.items():
            try:
                scan.index_files(self.lib.db, folders[fid], rels, skip_eagle=bool(self.lib.settings.get("skip_eagle")))
            except Exception:
                continue
            self.lib.thumbs.nudge()
            self.lib.meta.nudge()

    def _check_gone(self) -> None:
        """A watched folder that isn't there any more: a scan marks it offline (its files are kept)."""
        for f in self.lib.folders():
            if f["online"] and not os.path.isdir(f["path"]):
                self.lib.scan(f["id"])

    def _check_offline(self) -> None:
        for f in self.lib.folders():
            if not f["online"] and os.path.isdir(f["path"]):
                self.lib.scan(f["id"])
            if f["id"] not in self._watches:
                self.add(f)


class _Handler:
    """watchdog's callbacks, passed on as paths that changed."""

    def __init__(self, watcher: Watcher, folder: dict):
        self.w, self.folder = watcher, folder

    def dispatch(self, event) -> None:
        if self.w.paused:
            return
        if event.is_directory:
            src = os.fsdecode(getattr(event, "src_path", "") or "")
            if event.event_type in ("moved", "deleted") and not expected(src):
                self.w.lib.scan(self.folder["id"])  # a whole folder moved or went: a rescan sorts it out
            elif event.event_type == "created" or (event.event_type == "moved" and getattr(event, "dest_path", None)):
                try:  # (made, or moved here from another library folder)
                    self.w.dir_made(self.folder, os.fsdecode(event.dest_path) if event.event_type == "moved" else src)
                except Exception:
                    pass
            return
        for p in (getattr(event, "src_path", None), getattr(event, "dest_path", None)):
            if p:
                self.w.touched(self.folder, os.fsdecode(p))
