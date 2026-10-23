"""The library: your folders, the scans that index them, the thumbnails, and what's happening right now.

On start every folder is rescanned (quick: unchanged files are only stat'ed) and thumbnails carry on where
they stopped, so a session that was closed mid-way simply continues.
"""
from __future__ import annotations

import os
import threading
import time

import json

from . import metadata
from . import scan as scanner
from . import search
from .config import Settings
from .db import Database
from .thumbs import Thumbnails
from .watch import Watcher

SORTS = {
    "newest": "f.mtime DESC, f.id DESC",
    "oldest": "f.mtime ASC, f.id ASC",
    "name": "f.name COLLATE NOCASE ASC, f.id ASC",
    "largest": "f.size DESC, f.id DESC",
    # a shuffle that stays put while you scroll (the same seed gives the same order, page after page)
    # (two rounds of multiply-and-mod, so a different seed reorders everything, within 64-bit integers)
    "random": "((((f.id * 2654435761) % 4294967291) + ?) * 1597334677) % 4294967291, f.id",
    "viewed": "v.at DESC, f.id DESC",  # Recently viewed
}
ITEM = ("f.id, f.name, f.rel, f.kind, f.width, f.height, f.size, f.mtime, f.duration, f.thumb, f.folder_id, f.meta,"
        " COALESCE(m.rating, 0) AS rating, COALESCE(m.favorite, 0) AS favorite")
RECENT = 500  # how many files Recently viewed keeps
DAY = "date(f.mtime, 'unixepoch', 'localtime')"


class Library:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.settings = Settings(data_dir)
        self.db = Database(os.path.join(data_dir, "library.db"))
        self.thumbs = Thumbnails(self.db, os.path.join(data_dir, "thumbs"), self.settings)
        self.meta = metadata.MetaReader(self.db)
        self.watcher = Watcher(self)
        self._scan_lock = threading.Lock()
        self._queue: list[int] = []
        self._queue_cv = threading.Condition()
        self._stop = threading.Event()
        self.scanning: dict | None = None  # {folder, path, seen, added, ...} while a scan runs
        self.last_scan: dict | None = None

    # ---------------------------------------------------------------- lifecycle
    def start(self, rescan: bool = True) -> None:
        self.thumbs.start()
        self.meta.start()
        self.watcher.start()
        threading.Thread(target=self._scan_loop, name="scanner", daemon=True).start()
        if rescan:
            self.scan_all()

    def stop(self) -> None:
        self._stop.set()
        self.thumbs.stop()
        self.meta.stop()
        self.watcher.stop()
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
        folder = next(f for f in self.folders() if f["id"] == fid)
        self.watcher.add(folder)
        return folder

    def remove_folder(self, folder_id: int) -> bool:
        """Forget a folder and its files in the index. The folder and its files on disk are not touched."""
        with self._queue_cv:
            self._queue = [q for q in self._queue if q != folder_id]
        self.watcher.remove(int(folder_id))
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
                self.meta.nudge()

            try:
                counts = scanner.scan_folder(self.db, folder, skip_eagle=bool(self.settings.get("skip_eagle")),
                                             progress=progress, stop=self._stop.is_set)
            except Exception as ex:  # one folder failing doesn't stop the others
                counts = {"error": str(ex)}
            self.last_scan = {"folder": fid, "path": folder["path"], "at": time.time(), **counts}
            if not counts.get("offline") and not counts.get("error"):
                self.watcher.add(folder)  # back online (or new): watched from now on
            self.scanning = None
            self.thumbs.nudge()
            self.meta.nudge()

    # ---------------------------------------------------------------- what's there
    @staticmethod
    def _from(folder: int | None, kind: str | None, q: str = "", sort: str = "newest") -> tuple[str, list]:
        """FROM ... WHERE ... for a view: a folder or everything, a kind, a search, Recently viewed."""
        where, args = ["f.missing=0", "d.online=1"], []
        if folder:
            where.append("f.folder_id=?")
            args.append(int(folder))
        if kind in ("image", "video"):
            where.append("f.kind=?")
            args.append(kind)
        sw, sa = search.build(q)
        where += sw
        args += sa
        joins = " JOIN views v ON v.file_id=f.id" if sort == "viewed" else ""
        return (f"files f JOIN folders d ON d.id=f.folder_id LEFT JOIN marks m ON m.file_id=f.id{joins} WHERE {' AND '.join(where)}", args)

    def files(self, offset: int = 0, limit: int = 200, sort: str = "newest", folder: int | None = None, kind: str | None = None,
              seed: int = 0, q: str = "") -> dict:
        sort = sort if sort in SORTS else "newest"
        frm, args = self._from(folder, kind, q, sort)
        order_args = [int(seed) % 4294967291] if sort == "random" else []
        limit = max(1, min(1000, int(limit)))
        with self.db.read() as c:
            total = c.execute(f"SELECT COUNT(*) FROM {frm}", args).fetchone()[0]
            rows = c.execute(f"SELECT {ITEM} FROM {frm} ORDER BY {SORTS[sort]} LIMIT ? OFFSET ?",
                             [*args, *order_args, limit, max(0, int(offset))]).fetchall()
        return {"total": total, "offset": offset, "items": [dict(r) for r in rows]}

    def days(self, sort: str = "newest", folder: int | None = None, kind: str | None = None, q: str = "") -> list[dict]:
        """How many files each day holds, in the grid's order, so it can put a heading over each day
        without fetching every page. Only for the date sorts."""
        if sort not in ("newest", "oldest"):
            return []
        frm, args = self._from(folder, kind, q, sort)
        with self.db.read() as c:
            return [{"day": r[0], "n": r[1]} for r in c.execute(
                f"SELECT {DAY} AS day, COUNT(*) FROM {frm} GROUP BY day ORDER BY day {'DESC' if sort == 'newest' else 'ASC'}", args)]

    # ---------------------------------------------------------------- one file
    def info(self, file_id: int) -> dict | None:
        """Everything the details panel shows (the workflow itself only on request)."""
        fid = int(file_id)
        with self.db.read() as c:
            f = c.execute(f"SELECT {ITEM}, d.path AS folder_path, f.first_seen FROM files f JOIN folders d ON d.id=f.folder_id"
                          f" LEFT JOIN marks m ON m.file_id=f.id WHERE f.id=?", (fid,)).fetchone()
            if not f:
                return None
            g = c.execute("SELECT source, positive, negative, seed, steps, cfg, sampler, scheduler, width, height, denoise, clip_skip,"
                          " model, nodes, workflow IS NOT NULL AS has_workflow, info FROM generations WHERE file_id=?", (fid,)).fetchone()
            uses = [dict(r) for r in c.execute("SELECT kind, name, file, hash, strength, clip FROM uses WHERE file_id=? ORDER BY rowid", (fid,))]
            tags = [dict(r) for r in c.execute("SELECT t.name, ft.source FROM file_tags ft JOIN tags t ON t.id=ft.tag_id"
                                               " WHERE ft.file_id=? AND ft.removed=0 ORDER BY t.name COLLATE NOCASE", (fid,))]
            src = c.execute("SELECT source FROM marks WHERE file_id=?", (fid,)).fetchone()
        out = dict(f)
        out["path"] = os.path.join(f["folder_path"], f["rel"])
        gen = dict(g) if g else None
        if gen:
            gen["info"] = json.loads(gen["info"]) if gen["info"] else None
            gen["has_workflow"] = bool(gen["has_workflow"])
        out.update(generation=gen, uses=uses, tags=tags, marks_source=src["source"] if src else None)
        return out

    def workflow(self, file_id: int) -> dict | None:
        return metadata.workflow(self.db, int(file_id))

    def mark(self, file_id: int, rating=..., favorite=...) -> dict:
        """Your rating (0 clears it) and favourite. From now on they're yours: the Gallery Info node no longer sets them."""
        fid = int(file_id)
        with self.db.write() as c:
            if not c.execute("SELECT 1 FROM files WHERE id=?", (fid,)).fetchone():
                raise KeyError(fid)
            cur = c.execute("SELECT rating, favorite FROM marks WHERE file_id=?", (fid,)).fetchone()
            r = cur["rating"] if cur else None
            fav = cur["favorite"] if cur else 0
            if rating is not ...:
                r = int(rating) if rating and 1 <= int(rating) <= 5 else None
            if favorite is not ...:
                fav = int(bool(favorite))
            c.execute("INSERT OR REPLACE INTO marks (file_id, rating, favorite, source, updated) VALUES (?,?,?, 'user', ?)", (fid, r, fav, time.time()))
        return {"rating": r or 0, "favorite": fav}

    def set_tags(self, file_id: int, add=(), remove=()) -> list[dict]:
        fid = int(file_id)
        with self.db.write() as c:
            if not c.execute("SELECT 1 FROM files WHERE id=?", (fid,)).fetchone():
                raise KeyError(fid)
            for name in metadata._split(list(add)):
                tid = metadata.tag_id(c, name)
                c.execute("INSERT INTO file_tags (file_id, tag_id, source) VALUES (?, ?, 'user')"
                          " ON CONFLICT (file_id, tag_id) DO UPDATE SET removed=0", (fid, tid))
            for name in metadata._split(list(remove)):
                row = c.execute("SELECT id FROM tags WHERE name=? COLLATE NOCASE", (name,)).fetchone()
                if not row:
                    continue
                # a tag from the workflow is only hidden, so re-reading the file doesn't bring it back
                c.execute("UPDATE file_tags SET removed=1 WHERE file_id=? AND tag_id=? AND source='workflow'", (fid, row["id"]))
                c.execute("DELETE FROM file_tags WHERE file_id=? AND tag_id=? AND source='user'", (fid, row["id"]))
            c.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM file_tags)")
            metadata.refresh_search(c, fid)
        return self.info(fid)["tags"]

    def tags(self, q: str = "", limit: int = 20) -> list[dict]:
        with self.db.read() as c:
            return [dict(r) for r in c.execute(
                "SELECT t.name, COUNT(*) AS n FROM tags t JOIN file_tags ft ON ft.tag_id=t.id AND ft.removed=0"
                " WHERE t.name LIKE ? ESCAPE '\\' GROUP BY t.id ORDER BY n DESC, t.name COLLATE NOCASE LIMIT ?",
                (f"%{search._like(q)}%", max(1, min(200, int(limit)))))]

    def suggest(self, field: str, q: str = "", limit: int = 12) -> list[dict]:
        """Names to complete model:, lora:, tag:, sampler: with, most used first."""
        like, n = f"%{search._like(q)}%", max(1, min(100, int(limit)))
        if field == "tag":
            return self.tags(q, n)
        with self.db.read() as c:
            if field in ("model", "lora", "vae"):
                kinds = {"model": ("checkpoint", "unet"), "lora": ("lora",), "vae": ("vae",)}[field]
                return [dict(r) for r in c.execute(
                    f"SELECT name, COUNT(*) AS n FROM uses WHERE kind IN ({','.join('?' * len(kinds))}) AND name LIKE ? ESCAPE '\\'"
                    " GROUP BY name COLLATE NOCASE ORDER BY n DESC LIMIT ?", (*kinds, like, n))]
            if field in ("sampler", "scheduler"):
                return [dict(r) for r in c.execute(
                    f"SELECT {field} AS name, COUNT(*) AS n FROM generations WHERE {field} LIKE ? ESCAPE '\\'"
                    f" GROUP BY {field} ORDER BY n DESC LIMIT ?", (like, n))]
        return []

    def viewed(self, file_id: int) -> None:
        with self.db.write() as c:
            if c.execute("SELECT 1 FROM files WHERE id=?", (int(file_id),)).fetchone():
                c.execute("INSERT OR REPLACE INTO views (file_id, at) VALUES (?, ?)", (int(file_id), time.time()))
                c.execute("DELETE FROM views WHERE file_id NOT IN (SELECT file_id FROM views ORDER BY at DESC LIMIT ?)", (RECENT,))

    def file_path(self, file_id: int) -> str | None:
        with self.db.read() as c:
            r = c.execute("SELECT f.rel, d.path FROM files f JOIN folders d ON d.id=f.folder_id WHERE f.id=? AND f.missing=0", (int(file_id),)).fetchone()
        return os.path.join(r["path"], r["rel"]) if r else None

    def status(self) -> dict:
        with self.db.read() as c:
            total = c.execute("SELECT COUNT(*) FROM files WHERE missing=0").fetchone()[0]
        return {"files": total, "scanning": self.scanning, "last_scan": self.last_scan,
                "queued": len(self._queue), "thumbs_pending": self.thumbs.pending(), "thumbs_failed": self.thumbs.failed,
                "meta_pending": self.meta.pending()}
