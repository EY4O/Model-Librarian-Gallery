"""The library: your folders, the scans that index them, the thumbnails, and what's happening right now.

On start every folder is rescanned (quick: unchanged files are only stat'ed) and thumbnails carry on where
they stopped, so a session that was closed mid-way simply continues.
"""
from __future__ import annotations

import os
import threading
import time

from . import scan as scanner
from .config import Settings
from .db import Database
from .thumbs import Thumbnails

SORTS = {
    "newest": "f.mtime DESC, f.id DESC",
    "oldest": "f.mtime ASC, f.id ASC",
    "name": "f.name COLLATE NOCASE ASC, f.id ASC",
    "largest": "f.size DESC, f.id DESC",
    # a shuffle that stays put while you scroll (the same seed gives the same order, page after page)
    # (two rounds of multiply-and-mod, so a different seed reorders everything, within 64-bit integers)
    "random": "((((f.id * 2654435761) % 4294967291) + ?) * 1597334677) % 4294967291, f.id",
}
DAY = "date(f.mtime, 'unixepoch', 'localtime')"


class Library:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.settings = Settings(data_dir)
        self.db = Database(os.path.join(data_dir, "library.db"))
        self.thumbs = Thumbnails(self.db, os.path.join(data_dir, "thumbs"), self.settings)
        self._scan_lock = threading.Lock()
        self._queue: list[int] = []
        self._queue_cv = threading.Condition()
        self._stop = threading.Event()
        self.scanning: dict | None = None  # {folder, path, seen, added, ...} while a scan runs
        self.last_scan: dict | None = None

    # ---------------------------------------------------------------- lifecycle
    def start(self, rescan: bool = True) -> None:
        self.thumbs.start()
        threading.Thread(target=self._scan_loop, name="scanner", daemon=True).start()
        if rescan:
            self.scan_all()

    def stop(self) -> None:
        self._stop.set()
        self.thumbs.stop()
        with self._queue_cv:
            self._queue_cv.notify_all()

    # ---------------------------------------------------------------- folders
    def folders(self) -> list[dict]:
        with self.db.read() as c:
            return [dict(r) for r in c.execute("SELECT id, path, added, last_scan, online, file_count FROM folders ORDER BY path COLLATE NOCASE")]

    def add_folder(self, path: str) -> dict:
        path = os.path.abspath(os.path.expanduser(str(path or "").strip().strip('"')))
        if not os.path.isdir(path):
            raise ValueError("That folder doesn't exist (or isn't reachable right now).")
        if self.settings.get("skip_eagle") and any(p.lower().endswith(".library") for p in path.replace("\\", "/").split("/")):
            raise ValueError("That's inside an Eagle library, which Eagle manages: the Gallery leaves those alone.")
        clash = scanner.overlapping([f["path"] for f in self.folders()], path)
        if clash:
            raise ValueError(f"It overlaps a folder you've added ({clash}): its files would be indexed twice.")
        with self.db.write() as c:
            fid = c.execute("INSERT INTO folders (path, added) VALUES (?, ?)", (path, time.time())).lastrowid
        self.scan(fid)
        return next(f for f in self.folders() if f["id"] == fid)

    def remove_folder(self, folder_id: int) -> bool:
        """Forget a folder and its files in the index. The folder and its files on disk are not touched."""
        with self._queue_cv:
            self._queue = [q for q in self._queue if q != folder_id]
        with self.db.write() as c:
            return c.execute("DELETE FROM folders WHERE id=?", (int(folder_id),)).rowcount > 0

    # ---------------------------------------------------------------- scanning
    def scan(self, folder_id: int) -> None:
        with self._queue_cv:
            if folder_id not in self._queue:
                self._queue.append(int(folder_id))
            self._queue_cv.notify()

    def scan_all(self) -> None:
        for f in self.folders():
            self.scan(f["id"])

    def _scan_loop(self) -> None:
        while not self._stop.is_set():
            with self._queue_cv:
                while not self._queue and not self._stop.is_set():
                    self._queue_cv.wait()
                if self._stop.is_set():
                    return
                fid = self._queue.pop(0)
            folder = next((f for f in self.folders() if f["id"] == fid), None)
            if not folder:
                continue
            self.scanning = {"folder": fid, "path": folder["path"], "seen": 0, "added": 0}

            def progress(counts, fid=fid, path=folder["path"]):
                self.scanning = {"folder": fid, "path": path, **counts}
                self.thumbs.nudge()

            try:
                counts = scanner.scan_folder(self.db, folder, skip_eagle=bool(self.settings.get("skip_eagle")),
                                             progress=progress, stop=self._stop.is_set)
            except Exception as ex:  # one folder failing doesn't stop the others
                counts = {"error": str(ex)}
            self.last_scan = {"folder": fid, "path": folder["path"], "at": time.time(), **counts}
            self.scanning = None
            self.thumbs.nudge()

    # ---------------------------------------------------------------- what's there
    @staticmethod
    def _where(folder: int | None, kind: str | None) -> tuple[str, list]:
        where, args = ["f.missing=0", "d.online=1"], []
        if folder:
            where.append("f.folder_id=?")
            args.append(int(folder))
        if kind in ("image", "video"):
            where.append("f.kind=?")
            args.append(kind)
        return " AND ".join(where), args

    def files(self, offset: int = 0, limit: int = 200, sort: str = "newest", folder: int | None = None, kind: str | None = None,
              seed: int = 0) -> dict:
        w, args = self._where(folder, kind)
        sort = sort if sort in SORTS else "newest"
        order_args = [int(seed) % 4294967291] if sort == "random" else []
        limit = max(1, min(1000, int(limit)))
        with self.db.read() as c:
            total = c.execute(f"SELECT COUNT(*) FROM files f JOIN folders d ON d.id=f.folder_id WHERE {w}", args).fetchone()[0]
            rows = c.execute(f"SELECT f.id, f.name, f.rel, f.kind, f.width, f.height, f.size, f.mtime, f.duration, f.thumb, f.folder_id"
                             f" FROM files f JOIN folders d ON d.id=f.folder_id WHERE {w} ORDER BY {SORTS[sort]} LIMIT ? OFFSET ?",
                             [*args, *order_args, limit, max(0, int(offset))]).fetchall()
        return {"total": total, "offset": offset, "items": [dict(r) for r in rows]}

    def days(self, sort: str = "newest", folder: int | None = None, kind: str | None = None) -> list[dict]:
        """How many files each day holds, in the grid's order, so it can put a heading over each day
        without fetching every page. Only for the date sorts."""
        if sort not in ("newest", "oldest"):
            return []
        w, args = self._where(folder, kind)
        with self.db.read() as c:
            return [{"day": r[0], "n": r[1]} for r in c.execute(
                f"SELECT {DAY} AS day, COUNT(*) FROM files f JOIN folders d ON d.id=f.folder_id WHERE {w}"
                f" GROUP BY day ORDER BY day {'DESC' if sort == 'newest' else 'ASC'}", args)]

    def file_path(self, file_id: int) -> str | None:
        with self.db.read() as c:
            r = c.execute("SELECT f.rel, d.path FROM files f JOIN folders d ON d.id=f.folder_id WHERE f.id=? AND f.missing=0", (int(file_id),)).fetchone()
        return os.path.join(r["path"], r["rel"]) if r else None

    def status(self) -> dict:
        with self.db.read() as c:
            total = c.execute("SELECT COUNT(*) FROM files WHERE missing=0").fetchone()[0]
        return {"files": total, "scanning": self.scanning, "last_scan": self.last_scan,
                "queued": len(self._queue), "thumbs_pending": self.thumbs.pending(), "thumbs_failed": self.thumbs.failed}
