"""New downloads: the pictures and videos in your Downloads folder (and others you add in Settings), offered for filing.

Nothing there is indexed: it's listed (newest first) with who made it and where it's from, as far as the file, its
name and where Windows says it was downloaded from tell (credits.py), and whether the library has it already.
Filing moves it into a library folder - by default the one its artist's other pictures are in - and indexes it
there, with its artist and source; History can undo that. A download you dismiss isn't offered again (unless it
changes); one that's already in the library can go to the Recycle Bin instead.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time

from . import credits, fileops, scan

LISTED = 2000          # downloads listed at most, newest first
SETTLE = 2.0           # seconds a download must have been left alone (a browser still writing it isn't offered)
FOLDERID_DOWNLOADS = "{374DE290-123F-4565-9164-39C4925E467B}"


def downloads_folder() -> str | None:
    """Windows' Downloads folder, wherever you moved it."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            from .winshell import _guid
            path = ctypes.c_wchar_p()
            shell32 = ctypes.windll.shell32
            shell32.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
            guid = _guid(FOLDERID_DOWNLOADS)
            if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(path)) == 0:
                got = path.value
                ctypes.windll.ole32.CoTaskMemFree(path)
                if got and os.path.isdir(got):
                    return got
        except Exception:
            pass
    home = os.path.join(os.path.expanduser("~"), "Downloads")
    return home if os.path.isdir(home) else None


def key_of(path: str) -> str:
    return hashlib.sha1(os.path.normcase(os.path.abspath(path)).encode("utf-8")).hexdigest()[:20]


def safe_folder(name: str) -> str:
    """A name Windows takes for a folder (an artist's, for Artists\\<name>)."""
    v = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name or "").strip().rstrip(". ")
    return re.sub(r"\s+", " ", v)[:80] or "Unknown"


def _inside(path: str, root: str) -> bool:
    a, b = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(root))
    return a == b or a.startswith(b.rstrip("\\/") + os.sep)


class Inbox:
    def __init__(self, lib):
        self.lib = lib
        self._lock = threading.Lock()
        self._listing: tuple | None = None  # (what it was made from, the entries)
        self._found: dict[tuple, dict] = {}  # (path, size, mtime) -> what was found about it (fingerprint, credits)

    # ---------------------------------------------------------------- where
    def folders(self) -> list[dict]:
        """The folders whose new pictures are offered: {path, downloads (Windows' own), ok (reachable, not a library folder)}."""
        s = self.lib.settings
        lib_folders = [f["path"] for f in self.lib.folders()]
        out, seen = [], set()
        dl = downloads_folder() if s.get("inbox_downloads") else None
        for path, is_dl in ([(dl, True)] if dl else []) + [(p, False) for p in s.get("inbox_folders") or [] if isinstance(p, str)]:
            k = os.path.normcase(os.path.abspath(path))
            if k in seen:
                continue
            seen.add(k)
            in_lib = any(_inside(path, r) or _inside(r, path) for r in lib_folders)
            out.append({"path": path, "downloads": is_dl, "ok": os.path.isdir(path) and not in_lib,
                        "why": "It's a library folder (or holds one): its pictures are in the library already." if in_lib else
                               "" if os.path.isdir(path) else "It isn't there (or not reachable right now)."})
        return out

    def add_folder(self, path: str) -> list[dict]:
        path = os.path.abspath(os.path.expanduser(str(path or "").strip().strip('"')))
        if not os.path.isdir(path):
            raise ValueError("That folder doesn't exist (or isn't reachable right now).")
        if any(_inside(path, f["path"]) or _inside(f["path"], path) for f in self.lib.folders()):
            raise ValueError("That's a library folder (or holds one): its pictures are in the library already.")
        have = [p for p in self.lib.settings.get("inbox_folders") or [] if isinstance(p, str)]
        if not any(os.path.normcase(p) == os.path.normcase(path) for p in have):
            self.lib.settings.update({"inbox_folders": have + [path]})
        self._listing = None
        return self.folders()

    def remove_folder(self, path: str) -> list[dict]:
        have = [p for p in self.lib.settings.get("inbox_folders") or [] if isinstance(p, str)]
        self.lib.settings.update({"inbox_folders": [p for p in have if os.path.normcase(p) != os.path.normcase(str(path or ""))]})
        self._listing = None
        return self.folders()

    # ---------------------------------------------------------------- what's there
    def _dismissed(self) -> dict:
        with self.lib.db.read() as c:
            return {os.path.normcase(r[0]): (r[1], r[2]) for r in c.execute("SELECT path, size, mtime FROM inbox_dismissed")}

    def entries(self) -> list[dict]:
        """The downloads on offer: {key, path, name, size, mtime, kind}, newest first (the folders' top level only)."""
        folders = [f["path"] for f in self.folders() if f["ok"]]
        stamp = []
        for p in folders:
            try:
                stamp.append((p, os.stat(p).st_mtime_ns))  # a folder's time changes when a file comes or goes in it
            except OSError:
                stamp.append((p, None))
        made_from = (tuple(stamp), int(time.time() // 10))  # (and every 10 s: one still being written settles; filing, dismissing... start afresh)
        with self._lock:
            if self._listing and self._listing[0] == made_from:
                return self._listing[1]
        gone = self._dismissed()
        now = time.time()
        out = []
        for folder in folders:
            try:
                with os.scandir(folder) as it:
                    for e in it:
                        name = e.name
                        kind = scan.kind_of(name)
                        if not kind or name.startswith((".", "~$")) or not e.is_file(follow_symlinks=False):
                            continue
                        st = e.stat(follow_symlinks=False)
                        if not st.st_size or now - st.st_mtime < SETTLE:
                            continue
                        d = gone.get(os.path.normcase(e.path))
                        if d and d[0] == st.st_size and abs(d[1] - st.st_mtime) < 1:
                            continue
                        out.append({"key": key_of(e.path), "path": e.path, "name": name, "size": st.st_size, "mtime": st.st_mtime,
                                    "kind": kind, "folder": folder})
            except OSError:
                continue
        out.sort(key=lambda x: -x["mtime"])
        out = out[:LISTED]
        with self._lock:
            self._listing = (made_from, out)
        return out

    def count(self) -> int:
        return len(self.entries())

    def _about(self, e: dict) -> dict:
        """What's known about one download: its fingerprint, and who made it / where it's from."""
        k = (e["path"], e["size"], e["mtime"])
        got = self._found.get(k)
        if got is None:
            try:
                fp = scan.fingerprint(e["path"], e["size"])
            except OSError:
                fp = None
            got = {"fp": fp, "found": credits.found(e["path"], e["name"], e["name"], e["kind"])}
            if len(self._found) > 5000:
                self._found.clear()
            self._found[k] = got
        return got

    def items(self, offset: int = 0, limit: int = 60) -> dict:
        """A page of downloads with what's known about each: the artist and source found, the copy the library has
        already (if it has), and where to file it."""
        es = self.entries()
        page = es[max(0, offset):max(0, offset) + max(1, min(LISTED, limit))]
        db = self.lib.db
        out = []
        with db.read() as c:
            names = credits.name_map(c)
            last = self._last(c)
            for e in page:
                a = self._about(e)
                w = credits.winner([{"src": s, "artist": v.get("artist"), "site": v.get("site"), "post": v.get("post"), "url": v.get("url")}
                                    for s, v in a["found"].items()])
                dup = c.execute("SELECT f.id, f.rel, f.missing, d.path FROM files f JOIN folders d ON d.id=f.folder_id"
                                " WHERE f.fp=? AND f.size=? AND f.missing IN (0, 2, 3) ORDER BY f.missing LIMIT 1", (a["fp"], e["size"])).fetchone() if a["fp"] else None
                aid = names.get(credits.norm(w["artist"])) if w["artist"] else None
                out.append({**{k: e[k] for k in ("key", "name", "size", "mtime", "kind", "folder")},
                            "artist": w["artist"], "artist_from": w["artist_from"], "artist_id": aid,
                            "site": w["site"], "site_name": credits.SITES.get(w["site"] or "", w["site"]), "url": w["url"],
                            "have": {"id": dup["id"], "path": os.path.join(dup["path"], *dup["rel"].split("/")), "trash": dup["missing"] == 2} if dup else None,
                            "suggest": self._suggest(c, aid, w["artist"], last)})
        return {"total": len(es), "offset": offset, "items": out, "folders": self.folders()}

    # ---------------------------------------------------------------- where it goes
    def _last(self, c) -> dict | None:
        r = c.execute("SELECT value FROM library_meta WHERE key='inbox_last'").fetchone()
        try:
            last = json.loads(r[0]) if r else None
        except ValueError:
            last = None
        ok = last and c.execute("SELECT 1 FROM folders WHERE id=?", (last.get("folder"),)).fetchone()
        return last if ok else None

    def _suggest(self, c, aid: int | None, artist: str | None, last: dict | None) -> dict | None:
        """The folder to file a download in: where the artist's other pictures are; for an artist new here, a folder
        of their own (Artists\\<name>) in the library folder you filed into last; else that folder as it was."""
        if aid:
            r = c.execute("SELECT f.folder_id, CASE WHEN instr(f.rel, '/') THEN substr(f.rel, 1, length(f.rel) - length(f.name) - 1) ELSE '' END sub,"
                          " COUNT(*) n FROM files f JOIN folders d ON d.id=f.folder_id WHERE f.artist_id=? AND f.missing=0 AND d.online=1"
                          " GROUP BY f.folder_id, sub ORDER BY n DESC, sub = '', length(sub) LIMIT 1", (aid,)).fetchone()
            if r:
                return {"folder": r[0], "sub": r[1], "why": "where their other pictures are"}
        base = last["folder"] if last else (c.execute("SELECT id FROM folders WHERE online=1 ORDER BY id LIMIT 1").fetchone() or [None])[0]
        if not base:
            return None
        if artist:
            return {"folder": base, "sub": f"Artists/{safe_folder(artist)}", "why": "a folder of their own"}
        sub = last["sub"] if last else ""
        if credits.from_folder(f"{sub}/x"):  # the last one went to its artist's folder: not where someone else's goes
            sub = ""
        return {"folder": base, "sub": sub, "why": "where you filed last" if last and sub == last["sub"] else ""}

    # ---------------------------------------------------------------- filing
    def _pick(self, keys) -> list[dict]:
        want = {str(k) for k in (keys or [])}
        return [e for e in self.entries() if e["key"] in want]

    def file(self, keys, folder_id: int, sub: str = "", artist=None) -> dict:
        """Move these downloads into a library folder and index them there, with what was found about each (and your
        artist for them, if you typed one: '' for none)."""
        db = self.lib.db
        picked = self._pick(keys)
        if not picked:
            raise fileops.OpError("Those downloads aren't there any more.")
        sub = fileops._clean_sub(sub)
        with db.read() as c:
            d = c.execute("SELECT id, path, online FROM folders WHERE id=?", (int(folder_id),)).fetchone()
        if not d:
            raise fileops.OpError("That folder isn't in the library.")
        if not d["online"] or not os.path.isdir(d["path"]):
            raise fileops.OpError("That folder isn't reachable right now (a drive that isn't plugged in?).")
        sub = fileops._real_sub(d["path"], sub)
        dest = os.path.join(d["path"], *sub.split("/")) if sub else d["path"]
        moved, failed, taken = [], [], set()
        with db.read() as c:
            held = fileops._held(c, d["id"], sub)
            for e in picked:
                about = self._about(e)  # (before it moves: on another drive, where it was downloaded from doesn't go with it)
                name = fileops._free_name(dest, e["name"], taken, held)
                rel = f"{sub}/{name}" if sub else name
                try:
                    fileops._move_file(e["path"], os.path.join(dest, name))
                except (OSError, fileops.OpError) as ex:
                    failed.append({"key": e["key"], "name": e["name"], "error": str(ex)})
                    continue
                moved.append({"from": e["path"], "to_folder": d["id"], "to_rel": rel, "found": about["found"], "fp": about["fp"], "size": e["size"]})
        if not moved:
            raise fileops.OpError(failed[0]["error"] if failed else "Nothing was filed.")
        folder = {"id": d["id"], "path": d["path"]}
        # a download that's the same as a file the index has lost track of (deleted, or on a drive that isn't there) takes
        # over that file's record, ratings and all: undoing it must give the record back, not delete it
        with db.read() as c:
            newest = c.execute("SELECT COALESCE(MAX(id), 0) FROM files").fetchone()[0]
            before = {}
            for m in moved:
                for r in c.execute("SELECT id, folder_id, rel, missing FROM files WHERE fp=? AND size=?", (m["fp"], m["size"])) if m["fp"] else ():
                    before[r["id"]] = dict(r)
        for _ in range(3):  # the folder watcher may be indexing the same new files right now: then they're there already
            try:
                scan.index_files(db, folder, [m["to_rel"] for m in moved], skip_eagle=bool(self.lib.settings.get("skip_eagle")))
                break
            except Exception:
                time.sleep(0.3)
        with db.write() as c:
            done = []
            for m in moved:
                r = c.execute("SELECT id FROM files WHERE folder_id=? AND rel=?", (m["to_folder"], m["to_rel"])).fetchone()
                if not r:
                    continue
                m["id"] = r[0]
                if r[0] <= newest and r[0] in before:
                    m["was"] = before[r[0]]
                credits.store(c, r[0], m.pop("found"))  # shown at once (the file, its name, its download; its folder's read next)
                if artist is not None:
                    credits.set_user(c, r[0], artist=artist)
                done.append(m)
            credits.resolve(c, [m["id"] for m in done])
            c.execute("INSERT INTO library_meta (key, value) VALUES ('inbox_last', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (json.dumps({"folder": d["id"], "sub": sub}),))
            n = len(done)
            op = fileops.record(c, "file", f"Filed {n} download{'s' if n != 1 else ''} in {os.path.basename(dest) or dest}",
                                [{k: m[k] for k in ("id", "from", "to_folder", "to_rel", "was") if k in m} for m in done]) if done else None
            fileops._recount(c)
        self._listing = None
        self.lib.thumbs.nudge()
        self.lib.meta.nudge()
        self.lib.credits.nudge()
        db.user_change()
        return {"op": op, "filed": len(done), "failed": failed, "dest": dest}

    def dismiss(self, keys) -> int:
        picked = self._pick(keys)
        with self.lib.db.write() as c:
            c.executemany("INSERT OR REPLACE INTO inbox_dismissed (path, size, mtime) VALUES (?,?,?)", [(e["path"], e["size"], e["mtime"]) for e in picked])
        self._listing = None
        return len(picked)

    def undismiss(self) -> int:
        with self.lib.db.write() as c:
            n = c.execute("DELETE FROM inbox_dismissed").rowcount
        self._listing = None
        return n

    def recycle(self, keys) -> dict:
        """These downloads to the Recycle Bin (one the library has already, say)."""
        from send2trash import send2trash
        gone, failed = 0, []
        for e in self._pick(keys):
            try:
                send2trash(e["path"])
                gone += 1
            except OSError as ex:
                failed.append({"key": e["key"], "name": e["name"], "error": str(ex)})
        self._listing = None
        return {"recycled": gone, "failed": failed}

    def path_of(self, key: str) -> dict | None:
        return next((e for e in self.entries() if e["key"] == key), None)

    def thumb(self, key: str, size: int = 320) -> str | None:
        """A thumbnail of a download (kept in the data folder's thumbs\\downloads)."""
        from . import thumbs
        e = self.path_of(key)
        if not e:
            return None
        h = hashlib.sha1(f"{e['path']}|{e['size']}|{e['mtime']}|{size}".encode("utf-8")).hexdigest()
        out = os.path.join(self.lib.data_dir, "thumbs", "downloads", h[:2], h + ".webp")
        if os.path.isfile(out):
            return out
        try:
            if e["kind"] == "video":
                ff = thumbs.find_ffmpeg(self.lib.settings.get("ffmpeg"))
                if not ff:
                    return None
                data = thumbs.video_thumb(ff, e["path"], size)
            else:
                data = thumbs.image_thumb(e["path"], size)
        except Exception:
            return None
        from .config import write_atomic
        write_atomic(out, data)
        return out


def undo(db, data) -> dict:
    """A filing undone: each download back where it was (under a free name, if that's taken now), out of the index."""
    with db.read() as c:
        roots = {r["id"]: r["path"] for r in c.execute("SELECT id, path FROM folders")}
    back, failed = 0, []
    for it in reversed(data):
        with db.read() as c:
            cur = c.execute("SELECT folder_id, rel, missing FROM files WHERE id=?", (it["id"],)).fetchone()
        if not cur or cur["folder_id"] != it["to_folder"] or cur["rel"] != it["to_rel"] or cur["missing"] not in fileops.PRESENT:
            failed.append(it["id"])  # moved, deleted or in the Trash since: left alone
            continue
        src = fileops._full(roots[it["to_folder"]], it["to_rel"])
        home, name = os.path.split(it["from"])
        dst = os.path.join(home, fileops._free_name(home, name, set()))
        try:
            fileops._move_file(src, dst)
        except (OSError, fileops.OpError):
            failed.append(it["id"])
            continue
        with db.write() as c:
            was = it.get("was")
            if not was:
                c.execute("DELETE FROM files WHERE id=?", (it["id"],))
            else:  # the record it took over goes back where it was, missing as it was
                name = was["rel"].split("/")[-1]
                try:
                    c.execute("UPDATE files SET folder_id=?, rel=?, name=?, ext=?, missing=?, credited=0 WHERE id=?",
                              (was["folder_id"], was["rel"], name, os.path.splitext(name)[1].lower(), was["missing"] or 1, it["id"]))
                except Exception:  # its old name is someone else's now: it stays here, missing
                    c.execute("UPDATE files SET missing=1 WHERE id=?", (it["id"],))
                from .metadata import refresh_search
                refresh_search(c, it["id"])
        back += 1
    with db.write() as c:
        credits.prune(c)
    return {"undone": back, "failed": failed}
