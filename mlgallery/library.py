"""The library: your folders, the scans that index them, the thumbnails, and what's happening right now.

On start every folder is rescanned (quick: unchanged files are only stat'ed) and thumbnails carry on where
they stopped, so a session that was closed mid-way simply continues.
"""
from __future__ import annotations

import os
import re
import threading
import time

import json

from . import bulk, charfiles, charstore, charsync, colors, credits, dates, downloads, families, fileops, looks
from . import characters as chars
from . import metadata
from . import scan as scanner
from . import search
from .config import Settings
from .db import Database
from .thumbs import Thumbnails
from .mllink import ModelLibrary
from .watch import Watcher

SORTS = {
    "newest": "f.shot DESC, f.id DESC",
    "oldest": "f.shot ASC, f.id ASC",
    "name": "f.name COLLATE NOCASE ASC, f.id ASC",
    "name_desc": "f.name COLLATE NOCASE DESC, f.id DESC",
    "largest": "f.size DESC, f.id DESC",
    "smallest": "f.size ASC, f.id ASC",
    # a shuffle that stays put while you scroll (the same seed gives the same order, page after page)
    # (two rounds of multiply-and-mod, so a different seed reorders everything, within 64-bit integers)
    "random": "((((f.id * 2654435761) % 4294967291) + ?) * 1597334677) % 4294967291, f.id",
    "viewed": "v.at DESC, f.id DESC",  # Recently viewed
    "trashed": "f.trashed DESC, f.id DESC",  # the Trash, last deleted first
}
BACKUPS = 7  # daily copies of the index kept
KINDS = ("ai", "photos", "illustrations")  # AI generations, photos, illustrations (downloaded or your own): one or more
WHO = ("character", "person")  # an entry in Characters / People: someone made up, or someone in your photos
# what each file is, from what's been read of it: generation data, else a date taken (camera, phone), else neither
REVIEW_RUN = 120  # seconds: answers in To review this close together are one History step


def natural(text: str) -> list:
    """A sort key that reads the numbers in a name as numbers: "tag 2" before "tag 19"."""
    return [int(p) if p.isdigit() else p.casefold() for p in re.split(r"(\d+)", text)]


def _review_summary(name: str, items: list) -> str:
    last = {it["file_id"]: it["verdict"] for it in items}  # (a file answered twice counts once, as its last answer)
    yes = sum(1 for v in last.values() if v == "confirm")
    n = len(last)
    return f"Reviewed {n:,} file{'s' if n != 1 else ''} for {name}: {yes} yes, {n - yes} no"


FILE_IS = {"ai": "f.meta = 1", "photo": "f.meta <> 1 AND f.taken IS NOT NULL", "illustration": "f.meta <> 1 AND f.taken IS NULL"}


def dupes_key(ids) -> str:
    """A duplicates group as one text: its file ids, sorted (a group of other files is another group)."""
    return ",".join(str(i) for i in sorted({int(i) for i in ids}))


TRASHED_MOTION = "SELECT motion FROM files WHERE missing=2 AND motion IS NOT NULL"  # Live Photo videos in the Trash with their photo
ITEM = ("f.id, f.name, f.rel, f.ext, f.kind, f.width, f.height, f.size, f.mtime, f.taken, f.shot, f.motion, f.duration, f.thumb, f.folder_id, f.meta,"
        " COALESCE(m.rating, 0) AS rating, COALESCE(m.favorite, 0) AS favorite")
RECENT = 500  # how many files Recently viewed keeps
DAY = "date(f.shot, 'unixepoch', 'localtime')"  # the day it was taken, else modified


def similar(items: list[tuple[int, str, str]], within: int = 3, bucket_cap: int = 400) -> list[list[int]]:
    """Groups of files whose picture fingerprints differ in at most `within` of 64 bits (a picture saved again:
    another format, size or metadata), and whose bytes differ (exact copies are their own kind). Fingerprints
    are split in 4 blocks: two that close share a block exactly, so only those are compared."""
    parent: dict[int, int] = {}

    def root(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    vals = {i: (fp, int(h, 16)) for i, fp, h in items}
    for block in range(4):
        buckets: dict[int, list[int]] = {}
        for i, (fp, h) in vals.items():
            buckets.setdefault((h >> (16 * block)) & 0xFFFF, []).append(i)
        for ids in buckets.values():
            if len(ids) < 2 or len(ids) > bucket_cap:  # a huge bucket is flat pictures (all black, all white): not useful
                continue
            for x in range(len(ids)):
                for y in range(x + 1, len(ids)):
                    a, b = ids[x], ids[y]
                    if vals[a][0] != vals[b][0] and bin(vals[a][1] ^ vals[b][1]).count("1") <= within:
                        parent[root(a)] = root(b)
    groups: dict[int, list[int]] = {}
    for i in parent:
        groups.setdefault(root(i), []).append(i)
    return [sorted(g) for g in groups.values() if len(g) > 1]


def stem_of(name: str) -> str:
    from .mllink import stem
    return stem(name)


class Library:
    def __init__(self, data_dir: str, home: str | None = None, root: str | None = None):
        """data_dir: the index, thumbnails and backups. home: this PC's own folder (settings), the data folder
        itself unless the library is one that travels. root: that library's own folder (see libraries.py)."""
        self.data_dir = data_dir
        self.home = home or data_dir
        self.root = root
        self.note = None      # said in Settings, e.g. the last library wasn't reachable
        self.bringing = None  # the library marks are being brought from, meanwhile
        self.reopen = ["--library", root] if root else ["--data", data_dir]  # how to start a Gallery on it again
        os.makedirs(data_dir, exist_ok=True)
        self.settings = Settings(self.home)
        self.db = Database(os.path.join(data_dir, "library.db"))
        self._tidy_album_names()
        self.thumbs = Thumbnails(self.db, os.path.join(data_dir, "thumbs"), self.settings)
        self.meta = metadata.MetaReader(self.db)
        self.dater = dates.Dater(self.db, self.settings)
        self.credits = credits.CreditReader(self.db, wanted=lambda: "illustrations" in (self.kinds or []))
        self.inbox = downloads.Inbox(self)
        self.watcher = Watcher(self)
        self.models = ModelLibrary(self.settings)
        self.char_wake = threading.Event()  # a character changed here: the sync with Model Librarian's list runs now
        self.char_sync_error: str | None = None  # why the last sync with it didn't happen (its character page says)
        self._scan_lock = threading.Lock()
        self._queue: list[int] = []
        self._queue_cv = threading.Condition()
        self._stop = threading.Event()
        self.scanning: dict | None = None  # {folder, path, seen, added, ...} while a scan runs
        self.last_scan: dict | None = None

    @property
    def name(self) -> str | None:
        """A library's own name (None for this PC's library)."""
        if not self.root:
            return None
        from . import libraries
        return libraries.info(self.root)["name"]

    # ---------------------------------------------------------------- lifecycle
    def start(self, rescan: bool = True) -> None:
        threading.Thread(target=metadata.backfill_prompt_tags, args=(self.db,), name="prompt-tags", daemon=True).start()
        threading.Thread(target=self.thumbs.backfill_fingerprints, name="fingerprints", daemon=True).start()
        threading.Thread(target=self._backups, name="backups", daemon=True).start()
        threading.Thread(target=self._warm, name="warm", daemon=True).start()
        self.thumbs.start()
        self.meta.start()
        self.dater.start()
        self.credits.start()
        self.watcher.start()
        threading.Thread(target=self._scan_loop, name="scanner", daemon=True).start()
        threading.Thread(target=self.inbox.notify_loop, args=(self._stop,), name="inbox-notify", daemon=True).start()
        threading.Thread(target=charfiles.loop, args=(self, self._stop), name="characters-in", daemon=True).start()  # (from the Chrome extension; the sync with Model Librarian)
        if rescan:
            self.scan_all()

    def stop(self) -> None:
        self._stop.set()
        self.char_wake.set()  # (the characters loop ends)
        self.thumbs.stop()
        self.meta.stop()
        self.dater.stop()
        self.credits.stop()
        self.watcher.stop()
        with self._queue_cv:
            self._queue_cv.notify_all()

    # ---------------------------------------------------------------- folders
    def folders(self) -> list[dict]:
        with self.db.read() as c:
            return [dict(r) for r in c.execute("SELECT id, path, added, last_scan, online, file_count, icon, color FROM folders ORDER BY path COLLATE NOCASE")]

    def add_folder(self, path: str) -> dict:
        path = str(path or "").strip().strip('"').strip()
        if not path:
            raise ValueError("Type or paste the folder's full path, like D:\\ComfyUI\\output.")
        path = os.path.abspath(os.path.expanduser(path))
        if os.path.isfile(path):
            raise ValueError("That's a file: add the folder it's in.")
        if not os.path.isdir(path):
            raise ValueError("That folder doesn't exist (or isn't reachable right now).")
        same = next((f for f in self.folders() if os.path.normcase(os.path.normpath(f["path"])) == os.path.normcase(os.path.normpath(path))), None)
        if same:
            raise ValueError("That folder is in the library already.")
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

    def dirs(self, folder_id: int) -> list[dict]:
        """A library folder's subfolders that hold files (in them or deeper), and the empty ones: {rel, n}, n counting
        the ones deeper too."""
        def run():
            n: dict[str, int] = {}
            with self.db.read() as c:
                for (rel,) in c.execute("SELECT rel FROM files WHERE folder_id=? AND missing=0", (int(folder_id),)):
                    parts = rel.split("/")[:-1]
                    for i in range(1, len(parts) + 1):
                        k = "/".join(parts[:i])
                        n[k] = n.get(k, 0) + 1
                known = {k.casefold() for k in n}
                for (rel,) in c.execute("SELECT rel FROM dirs WHERE folder_id=?", (int(folder_id),)):
                    parts = rel.split("/")
                    for i in range(1, len(parts) + 1):  # (with the folders it's in, though nothing else is)
                        k = "/".join(parts[:i])
                        if k.casefold() not in known:
                            known.add(k.casefold())
                            n[k] = 0
            return [{"rel": k, "n": v} for k, v in sorted(n.items(), key=lambda kv: kv[0].casefold())]
        return self.db.cached(("dirs", int(folder_id)), run)

    def pause_watching(self, on: bool) -> None:
        """Stop looking for new and changed files (the tray's Pause watching folders), or start again: then every
        folder is looked through, for what came meanwhile."""
        was, self.watcher.paused = self.watcher.paused, bool(on)
        if was and not on:
            self.scan_all()

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
    def _from(self, folder: int | None, kind: str | None, q: str = "", sort: str = "newest") -> tuple[str, list]:
        """FROM ... WHERE ... for a view: a folder or everything, a kind, a search, Recently viewed, the Trash.
        Every condition is on files itself or an indexed lookup, so counting and paging stay quick."""
        # (a Live Photo in the Trash is one item there: its video, in the Trash with it, isn't listed)
        where, args = [f"f.missing=2 AND f.id NOT IN ({TRASHED_MOTION})" if sort == "trashed" else "f.missing=0"], []
        if folder:
            where.append("f.folder_id=?")
            args.append(int(folder))
        offline = self.db.cached(("offline",), self._offline)
        if offline:
            where.append(f"f.folder_id NOT IN ({','.join('?' * len(offline))})")
            args += offline
        if kind in ("image", "video"):
            where.append("f.kind=?")
            args.append(kind)
        sw, sa = search.build(q, self.db)
        where += sw
        args += sa
        joins = " JOIN views v ON v.file_id=f.id" if sort == "viewed" else ""
        return (f"files f{joins} WHERE {' AND '.join(where)}", args)

    def _offline(self) -> list[int]:
        with self.db.read() as c:
            return [r[0] for r in c.execute("SELECT id FROM folders WHERE online=0")]

    def count(self, frm: str, args: list) -> int:
        def run():
            with self.db.read() as c:
                return c.execute(f"SELECT COUNT(*) FROM {frm}", args).fetchone()[0]
        return self.db.cached(("count", frm, tuple(args)), run)

    def files(self, offset: int = 0, limit: int = 200, sort: str = "newest", folder: int | None = None, kind: str | None = None,
              seed: int = 0, q: str = "") -> dict:
        sort = sort if sort in SORTS else "newest"
        frm, args = self._from(folder, kind, q, sort)
        order_args = [int(seed) % 4294967291] if sort == "random" else []
        limit = max(1, min(1000, int(limit)))
        total = self.count(frm, args)
        with self.db.read() as c:
            # the page's ids straight from the index, then their details (the marks for those few only)
            ids = [r[0] for r in c.execute(f"SELECT f.id FROM {frm} ORDER BY {SORTS[sort]} LIMIT ? OFFSET ?",
                                           [*args, *order_args, limit, max(0, int(offset))])]
            marks = ",".join("?" * len(ids))
            rows = c.execute(f"SELECT {ITEM} FROM files f LEFT JOIN marks m ON m.file_id=f.id WHERE f.id IN ({marks})", ids).fetchall() if ids else []
            # what made each one, for the tile: its model (and that model's family colour) and main LoRA
            models = dict(c.execute(f"SELECT file_id, model FROM generations WHERE file_id IN ({marks})", ids).fetchall()) if ids else {}
            loras: dict[int, list] = {}
            for r in (c.execute(f"SELECT file_id, name, strength FROM uses WHERE kind='lora' AND file_id IN ({marks}) ORDER BY rowid", ids) if ids else []):
                loras.setdefault(r[0], []).append((r[1], r[2]))
            # its main colour (colors.py), what the tile shows while the thumbnail is on its way
            tones = dict(c.execute(f"SELECT file_id, hex FROM colors WHERE rank=0 AND file_id IN ({marks})", ids).fetchall()) if ids else {}
        at = {r["id"]: dict(r) for r in rows}
        resolve = self.db.cached(("family-resolver",), lambda: families.Resolver(self.models))
        for i, it in at.items():
            m = models.get(i)
            if m:
                fam = resolve.family(m)
                it.update(model=m, family=fam, color=families.color(fam) if fam else None)
            if loras.get(i):
                (name, strength), n = loras[i][0], len(loras[i])
                it.update(lora=name, lora_strength=strength, loras=n)
            if tones.get(i):
                it["tone"] = tones[i]
        with self.db.read() as c:
            newest = c.execute("SELECT MAX(id) FROM files").fetchone()[0] or 0  # what "Select all" covers: files up to this one
        return {"total": total, "offset": offset, "items": [at[i] for i in ids if i in at], "max_id": newest}

    def shapes(self, sort: str = "newest", folder: int | None = None, kind: str | None = None, q: str = "", seed: int = 0) -> list[int]:
        """Each file's height for a width of 100 (33 to 300; 100 when not known), in the grid's order: what the
        Waterfall layout needs to place every tile without fetching every page."""
        sort = sort if sort in SORTS else "newest"
        frm, args = self._from(folder, kind, q, sort)
        order_args = [int(seed) % 4294967291] if sort == "random" else []

        def run():
            with self.db.read() as c:
                return [min(300, max(33, round(r[1] * 100 / r[0]))) if r[0] and r[1] else 100
                        for r in c.execute(f"SELECT f.width, f.height FROM {frm} ORDER BY {SORTS[sort]}", [*args, *order_args])]
        return self.db.cached(("shapes", sort, frm, tuple(args), tuple(order_args)), run)

    def set_look(self, kind: str, item_id: int, icon=..., color=...) -> None:
        """A folder's or album's icon and colour in the sidebar. icon: svg:<name>, n:<0-99> or e:<an emoji>; color: #rrggbb; None for the default."""
        import re
        table = {"folder": "folders", "album": "albums"}[kind]
        sets, args = [], []
        if icon is not ...:
            if icon is not None and not (isinstance(icon, str) and re.fullmatch(r"svg:[a-z0-9-]{1,30}|n:\d{1,2}|e:\S{1,8}", icon)):
                raise ValueError("That isn't an icon.")
            sets.append("icon=?")
            args.append(icon)
        if color is not ...:
            if color is not None and not (isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color)):
                raise ValueError("A colour is like #ef5350.")
            sets.append("color=?")
            args.append(color.lower() if color else None)
        if not sets:
            return
        with self.db.write() as c:
            if not c.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id=?", (*args, int(item_id))).rowcount:
                raise KeyError(item_id)

    def days(self, sort: str = "newest", folder: int | None = None, kind: str | None = None, q: str = "") -> list[dict]:
        """How many files each day holds, in the grid's order, so it can put a heading over each day
        without fetching every page. Only for the date sorts."""
        if sort not in ("newest", "oldest"):
            return []
        frm, args = self._from(folder, kind, q, sort)

        def run():
            with self.db.read() as c:
                return [{"day": r[0], "n": r[1]} for r in c.execute(
                    f"SELECT {DAY} AS day, COUNT(*) FROM {frm} GROUP BY day ORDER BY day {'DESC' if sort == 'newest' else 'ASC'}", args)]
        return self.db.cached(("days", sort, frm, tuple(args)), run)

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
                                               " WHERE ft.file_id=? AND ft.removed=0", (fid,))]
            tags.sort(key=lambda t: natural(t["name"]))  # ("tag 2" before "tag 19")
            src = c.execute("SELECT source FROM marks WHERE file_id=?", (fid,)).fetchone()
        out = dict(f)
        out["path"] = os.path.join(f["folder_path"], *f["rel"].split("/"))
        gen = dict(g) if g else None
        if gen:
            gen["info"] = json.loads(gen["info"]) if gen["info"] else None
            gen["has_workflow"] = bool(gen["has_workflow"])
        with self.db.read() as c:
            n = c.execute("SELECT note, source FROM file_notes WHERE file_id=?", (fid,)).fetchone()
            out["note"] = dict(n) if n else None
            out["colors"] = colors.of_file(c, fid)
            out["albums"] = [dict(r) for r in c.execute(
                "SELECT al.id, al.name, af.source FROM album_files af JOIN albums al ON al.id=af.album_id WHERE af.file_id=? AND af.removed=0"
                " ORDER BY al.name COLLATE NOCASE", (fid,))]
            out["characters"] = [dict(r) for r in c.execute(
                "SELECT ch.id, ch.name, fc.state, fc.source FROM file_characters fc JOIN characters ch ON ch.id=fc.character_id"
                " WHERE fc.file_id=? AND fc.state <> 'rejected' ORDER BY ch.name COLLATE NOCASE", (fid,))]
            out["credit"] = credits.of_file(c, fid)
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
                c.execute("UPDATE file_tags SET removed=1 WHERE file_id=? AND tag_id=? AND source IN ('workflow', 'site')", (fid, row["id"]))
                c.execute("DELETE FROM file_tags WHERE file_id=? AND tag_id=? AND source='user'", (fid, row["id"]))
            c.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM file_tags)")
            metadata.refresh_search(c, fid)
            chars.match_file(c, fid)  # a tag rule may match now (or no longer)
        return self.info(fid)["tags"]

    def tags(self, q: str = "", limit: int = 20) -> list[dict]:
        with self.db.read() as c:
            return [dict(r) for r in c.execute(
                "SELECT t.name, COUNT(*) AS n FROM tags t JOIN file_tags ft ON ft.tag_id=t.id AND ft.removed=0"
                " WHERE t.name LIKE ? ESCAPE '\\' GROUP BY t.id ORDER BY n DESC, t.name COLLATE NOCASE LIMIT ?",
                (f"%{search._like(q)}%", max(1, min(200, int(limit)))))]

    def suggest(self, field: str, q: str = "", limit: int = 12) -> list[dict]:
        """Names to complete model:, lora:, tag:, sampler:, color:, artist:, site: with, most used first."""
        like, n = f"%{search._like(q)}%", max(1, min(100, int(limit)))
        if field == "tag":
            return self.tags(q, n)
        if field in ("color", "colour"):  # the colour families, with their swatch
            return [{"name": k, "swatch": v} for k, v in colors.FAMILIES.items() if k.startswith(q.strip().lower())][:n]
        with self.db.read() as c:
            if field in ("model", "lora", "vae"):
                kinds = {"model": ("checkpoint", "unet"), "lora": ("lora",), "vae": ("vae",)}[field]
                return [dict(r) for r in c.execute(
                    f"SELECT name, COUNT(*) AS n FROM uses WHERE kind IN ({','.join('?' * len(kinds))}) AND name LIKE ? ESCAPE '\\'"
                    " GROUP BY name COLLATE NOCASE ORDER BY n DESC LIMIT ?", (*kinds, like, n))]
            if field == "artist":
                return [dict(r) for r in c.execute(
                    "SELECT a.name, COUNT(f.id) AS n FROM artists a LEFT JOIN files f ON f.artist_id=a.id AND f.missing=0"
                    " WHERE a.name LIKE ? ESCAPE '\\' GROUP BY a.id HAVING n > 0 OR a.auto = 0 ORDER BY n DESC, a.name COLLATE NOCASE LIMIT ?", (like, n))]
            if field == "site":  # (the source each picture's details show: what site: finds)
                return [{"name": r[0], "n": r[1]} for r in c.execute(
                    "SELECT site, COUNT(*) n FROM files WHERE site IS NOT NULL AND missing=0 AND site LIKE ? ESCAPE '\\'"
                    " GROUP BY site ORDER BY n DESC LIMIT ?", (like, n))]
            if field in ("sampler", "scheduler"):
                return [dict(r) for r in c.execute(
                    f"SELECT {field} AS name, COUNT(*) AS n FROM generations WHERE {field} LIKE ? ESCAPE '\\'"
                    f" GROUP BY {field} ORDER BY n DESC LIMIT ?", (like, n))]
        return []

    # ---------------------------------------------------------------- characters
    def characters(self) -> dict:
        return self.db.cached_lazy(("characters-list",), self._characters)[0]

    def _characters(self) -> dict:
        with self.db.read() as c:
            counts = {r[0]: (r[1], r[2]) for r in c.execute(
                "SELECT fc.character_id, SUM(fc.state IN ('auto','confirmed')), SUM(fc.state='review') FROM file_characters fc"
                " JOIN files f ON f.id=fc.file_id WHERE f.missing=0 GROUP BY fc.character_id")}
            rows = []
            for ch in c.execute("SELECT id, name, aliases, cover, who, shared FROM characters ORDER BY name COLLATE NOCASE").fetchall():
                latest = None
                if not ch["cover"]:
                    r = c.execute("SELECT fc.file_id FROM file_characters fc JOIN files f ON f.id=fc.file_id WHERE fc.character_id=? AND fc.state IN ('auto','confirmed')"
                                  " AND f.missing=0 ORDER BY fc.state='confirmed' DESC, f.shot DESC LIMIT 1", (ch["id"],)).fetchone()
                    latest = r[0] if r else None
                n, review = counts.get(ch["id"], (0, 0))
                rows.append({"id": ch["id"], "name": ch["name"], "aliases": ch["aliases"], "cover": ch["cover"], "who": ch["who"], "n": n or 0,
                             "review": review or 0, "latest": latest, "picture": bool((json.loads(ch["shared"] or "{}") or {}).get("image"))})
            sugg = chars.suggestions(c)
        out = []
        for r in rows:
            d = dict(r)
            d["aliases"] = json.loads(d["aliases"] or "[]")
            d["cover"] = d["cover"] or d.pop("latest")
            d.pop("latest", None)
            out.append(d)
        return {"characters": out, "review": sum(d["review"] for d in out), "suggestions": sugg}

    def character_save(self, cid: int | None, name: str, aliases=(), rules=None, cover=..., who=None) -> int:
        """Create (cid None) or change a character (or a person: who); its files are matched again. A new one with no
        who given is a person in a library that's only for photos, else a character."""
        if who is not None and who not in WHO:
            raise ValueError("Someone is a character or a person.")
        if cid is None and who is None:
            who = "person" if self.kinds == ["photos"] else "character"
        if not isinstance(name or "", str):
            raise ValueError("A character's name is text.")
        name = (name or "").strip()[:100]
        if not name:
            raise ValueError("A character needs a name.")
        aliases = [a for a in metadata._split(list(aliases or [])) if a.lower() != name.lower()][:16]
        with self.db.write() as c:
            clash = c.execute("SELECT id FROM characters WHERE name=? COLLATE NOCASE", (name,)).fetchone()
            if clash and clash[0] != cid:
                raise ValueError(f"There's already a character called {name}.")
            was = c.execute("SELECT name, aliases, shared, who FROM characters WHERE id=?", (cid,)).fetchone() if cid is not None else None
            wake = False
            if was and was["shared"]:  # shared with Model Librarian's list
                sh = json.loads(was["shared"])
                if who == "person" and was["who"] != "person":  # a person now: out of the list (people aren't in it)
                    if sh.get("id"):
                        c.execute("INSERT OR REPLACE INTO characters_deleted (shared_id, at) VALUES (?,?)", (sh["id"], time.time()))
                    c.execute("UPDATE characters SET shared=NULL WHERE id=?", (cid,))
                    wake = True
                else:  # when the name or aliases changed, for the sync
                    parts = (["name"] if was["name"] != name else []) + (["aliases"] if json.loads(was["aliases"] or "[]") != aliases else [])
                    if parts:
                        charstore.stamp(sh, parts, time.time())
                        c.execute("UPDATE characters SET shared=? WHERE id=?", (json.dumps(sh), cid))
                        wake = True
            if cid is None:
                cid = c.execute("INSERT INTO characters (name, aliases, created, who) VALUES (?,?,?,?)", (name, json.dumps(aliases), time.time(), who)).lastrowid
            else:
                if not c.execute("SELECT 1 FROM characters WHERE id=?", (cid,)).fetchone():
                    raise KeyError(cid)
                c.execute("UPDATE characters SET name=?, aliases=? WHERE id=?", (name, json.dumps(aliases), cid))
                if who is not None:
                    c.execute("UPDATE characters SET who=? WHERE id=?", (who, cid))
            if cover is not ...:
                c.execute("UPDATE characters SET cover=? WHERE id=?", (int(cover) if cover else None, cid))
            if rules is not None:
                old = {(r["kind"], r["value"].lower()): r for r in c.execute("SELECT * FROM character_rules WHERE character_id=?", (cid,))}
                c.execute("DELETE FROM character_rules WHERE character_id=?", (cid,))
                seen = set()
                for r in rules[:64]:
                    kind = r.get("kind")
                    value = str(r.get("value") or "").strip()[:200]
                    if kind not in ("lora", "word", "tag") or not value or (kind, value.lower()) in seen:
                        continue
                    seen.add((kind, value.lower()))
                    strength = r.get("strength") or ("weak" if kind == "tag" else "strong")
                    prev = old.get((kind, value.lower()))
                    muted = int(bool(r.get("muted", prev["muted"] if prev else 0)))
                    c.execute("INSERT INTO character_rules (character_id, kind, value, strength, muted) VALUES (?,?,?,?,?)",
                              (cid, kind, value, "weak" if strength == "weak" else "strong", muted))
            chars.rematch(c, cid)
        if wake:  # (after the change is in: the sync reads it as it is now)
            self.char_wake.set()
        return cid

    def character_delete(self, cid: int) -> bool:
        """Deleted, and (shared with Model Librarian's list) out of the list at the next sync too."""
        with self.db.write() as c:
            row = c.execute("SELECT shared FROM characters WHERE id=?", (int(cid),)).fetchone()
            sid = (json.loads(row["shared"] or "{}") or {}).get("id") if row else None
            if sid:
                c.execute("INSERT OR REPLACE INTO characters_deleted (shared_id, at) VALUES (?,?)", (sid, time.time()))
            gone = c.execute("DELETE FROM characters WHERE id=?", (int(cid),)).rowcount > 0
        if sid:
            self.char_wake.set()
        return gone

    # ---------------------------------------------------------------- characters shared with Model Librarian (charsync.py)
    def sync_characters(self) -> dict:
        """Kept in step with Model Librarian's list: a library for AI generations or illustrations (not one only for photos)."""
        kinds = self.kinds or ["ai"]
        if "ai" not in kinds and "illustrations" not in kinds:
            return {"list": False, "gallery": 0}
        return charsync.sync(self, self.models.data_dir())

    def apply_character_sync(self, acts) -> int:
        """The Gallery's part of a sync: characters saved, made, deleted; deletions the list knows of now forgotten."""
        n = 0
        for a in acts:
            try:
                if a[0] == "forget":
                    with self.db.write() as c:
                        c.execute("DELETE FROM characters_deleted WHERE shared_id=?", (a[1],))
                elif a[0] == "delete":
                    with self.db.write() as c:
                        c.execute("DELETE FROM characters WHERE id=?", (int(a[1]),))
                else:
                    name, aliases, shared, add, before = (a[2], a[3], a[4], a[5], a[6])
                    cid = self.character_save(None, name, aliases, [], who="character") if a[0] == "create" else int(a[1])
                    with self.db.write() as c:
                        # only if it hasn't changed since the sync read it (an edit meanwhile wins: the next sync takes it)
                        if a[0] == "save" and not c.execute("SELECT 1 FROM characters WHERE id=? AND shared IS ?", (cid, before)).fetchone():
                            continue
                        clash = c.execute("SELECT id FROM characters WHERE name=? COLLATE NOCASE AND id<>?", (name, cid)).fetchone()
                        c.execute("UPDATE characters SET aliases=?, shared=? WHERE id=?", (json.dumps(aliases[:16]), json.dumps(shared), cid))
                        if not clash:
                            c.execute("UPDATE characters SET name=? WHERE id=?", (name[:100], cid))
                        rules = [dict(r) for r in c.execute("SELECT kind, value, strength, muted FROM character_rules WHERE character_id=?", (cid,))]
                    new = [{"kind": k, "value": v, "strength": "strong"} for k, v in add
                           if not any(r["kind"] == k and r["value"].lower() == v.lower() for r in rules)]
                    if new:  # (matched again with them)
                        cur = self.character(cid)
                        self.character_save(cid, cur["name"], cur["aliases"], rules + new)
                n += 1
            except Exception:
                continue  # (the next sync tries again)
        if n:
            self.db.user_change()  # (the Characters list counted afresh: what came from the list shows at once)
        return n

    def character_from_post(self, cid: int | None, d: dict) -> dict:
        """A character from a saved booru picture's note (d: {tag, name, series, appearance, outfit}, as + Character would
        file it): made (cid None), or added to yours (cid). Its booru tag becomes a rule (pictures saved with it are
        theirs) and its tag when it has none; its series when it has none; the post's look joins its own, the outfit
        under a name of its own. -> {character: {id, name}, created}."""
        from .charsync import display
        tag = re.sub(r"\s+", "_", str(d.get("tag") or "").strip())[: charstore.CAPS["tag"]]
        if not tag:
            raise ValueError("That character has no tag.")
        rule = {"kind": "word", "value": display(tag), "strength": "strong"}
        if cid is None:  # (one made a moment ago from the same tag - Add twice - is the one: not a second)
            with self.db.read() as c:
                cid = next((r[0] for r in c.execute("SELECT character_id, value FROM character_rules WHERE kind='word'")
                            if chars.tag_key(r[1]) == chars.tag_key(rule["value"])), None)
        created = cid is None
        if created:
            name = str(d.get("name") or "").strip()[:100] or display(re.sub(r"\s*\(.*\)$", "", tag)).title()
            with self.db.read() as c:
                taken = [r[0] for r in c.execute("SELECT name FROM characters")]
            cid = self.character_save(None, charstore._unique(name, taken), [], [rule], who="character")
        else:
            cur = self.character(int(cid))
            if not cur:
                raise KeyError(cid)
            if cur["who"] == "person":
                raise ValueError("That's one of your people, not a character.")
            rules = [{k: r[k] for k in ("kind", "value", "strength", "muted")} for r in cur["rules"]]
            aliases = list(cur["aliases"])
            if cur["shared"]["tag"] and charstore.norm(cur["shared"]["tag"]) != charstore.norm(tag):
                also = display(tag)  # (a tag of its own: the post's, as another name, is how its saved pictures know it)
                if not any(chars.same_name(also, n) for n in [cur["name"], *aliases]):
                    aliases.append(also)
            new = [rule] if not any(r["kind"] == "word" and chars.tag_key(r["value"]) == chars.tag_key(rule["value"]) for r in rules) else []
            if new or aliases != cur["aliases"]:
                self.character_save(int(cid), cur["name"], aliases, rules + new)
            cid = int(cid)
        sh = self.character(cid)["shared"]
        patch = {}
        if not sh["tag"]:
            patch["tag"] = tag
        if not sh["series"] and d.get("series"):
            patch["series"] = d["series"]
        more = [t for t in d.get("appearance") or [] if chars.norm(t) not in {chars.norm(x) for x in sh["appearance"]}]
        if more:
            patch["appearance"] = (sh["appearance"] + more)[: charstore.CAPS["tags"]]
        if d.get("outfit") and len(sh["outfits"]) < charstore.CAPS["outfits"] and list(d["outfit"]) not in sh["outfits"].values():
            patch["outfits"] = {**sh["outfits"], charstore._unique("default", sh["outfits"]): list(d["outfit"])}
        if patch:
            self.character_presets(cid, patch)
        self.db.user_change()  # (the Characters list counted afresh)
        ch = self.character(cid)
        return {"character": {"id": cid, "name": ch["name"]}, "created": created}

    def character_presets(self, cid: int, patch: dict) -> dict:
        """Its series, booru tag, appearance and outfits, as Character Select writes them (shared with Model Librarian)."""
        cid = int(cid)
        lora = ...
        if "lora" in patch:  # (a LoRA Model Librarian has, by its file in the loras folder; its trigger words as trained)
            lo = patch["lora"]
            if lo is None or (isinstance(lo, dict) and not lo.get("name")):
                lora = None
            elif isinstance(lo, dict):
                want = str(lo["name"]).replace("\\", "/").lower()
                have = next((m["file"] for m in self.character_loras() if m["file"].lower() == want), None)
                if not have:
                    raise ValueError("That LoRA isn't in Model Librarian's loras folder.")
                trig = lo.get("trigger") if isinstance(lo.get("trigger"), list) else self.trigger_words(have, guess=False)
                words = []
                for w in trig:
                    w = str(w).strip()[: charstore.CAPS["tag"]]
                    if w and w.lower() not in {x.lower() for x in words}:
                        words.append(w)
                lora = {"name": have, "trigger": words[:12]}
            else:
                raise ValueError("lora is {name, trigger} or null")
        with self.db.write() as c:
            row = c.execute("SELECT shared FROM characters WHERE id=?", (cid,)).fetchone()
            if not row:
                raise KeyError(cid)
            sh = json.loads(row["shared"] or "{}") or {}
            parts = []

            def put(part, value):
                if value != sh.get(part, [] if part == "appearance" else {} if part == "outfits" else None if part == "lora" else ""):
                    sh[part] = value
                    parts.append(part)
            if "series" in patch:
                put("series", charstore._text(patch["series"], charstore.CAPS["text"]))
            if "tag" in patch:
                put("tag", re.sub(r"\s+", "_", str(patch["tag"] or "").strip())[: charstore.CAPS["tag"]])
            if "appearance" in patch:
                if not isinstance(patch["appearance"], list):
                    raise ValueError("appearance is a list of tags")
                put("appearance", charstore._tags(patch["appearance"]))
            if lora is not ...:
                put("lora", lora)
            if "outfits" in patch:
                if not isinstance(patch["outfits"], dict):
                    raise ValueError("outfits is {name: [tags]}")
                outfits = {}
                for k, v in list(patch["outfits"].items())[: charstore.CAPS["outfits"]]:
                    k = charstore._text(k, charstore.CAPS["text"])
                    if k and isinstance(v, list):
                        outfits[k] = charstore._tags(v)
                put("outfits", outfits)
            if parts:
                charstore.stamp(sh, parts, time.time())
                c.execute("UPDATE characters SET shared=? WHERE id=?", (json.dumps(sh), cid))
            if "tag" in parts:  # (its tag is one of its names: a rule that's its name matches pictures' tags, and in any order)
                chars.rematch(c, cid)
        if parts:
            self.char_wake.set()
        return self.character(cid)

    def character_loras(self) -> list[dict]:
        """The LoRAs Model Librarian has: {name, file} (file: in the loras folder, with /)."""
        return [{"name": m["name"], "file": m["file"]} for m in self.models.models()["models"] if m["kind"] == "lora"]

    def character_learn(self, cid: int) -> dict:
        """Tags common in the prompts of the images it's in (counted: recognised or confirmed), as appearance and outfit
        suggestions for its Prompt card; the ones it has already left out. -> {n, tags: [{tag, kind, share}]}"""
        cid = int(cid)
        with self.db.read() as c:
            row = c.execute("SELECT name, shared FROM characters WHERE id=?", (cid,)).fetchone()
            if not row:
                raise KeyError(cid)
            files = "SELECT fc.file_id FROM file_characters fc JOIN files f ON f.id=fc.file_id WHERE fc.character_id=? AND fc.state IN ('auto','confirmed') AND f.missing=0"
            n = c.execute(f"SELECT COUNT(*) FROM ({files})", (cid,)).fetchone()[0]
            counts = dict(c.execute(f"SELECT tag, COUNT(DISTINCT file_id) FROM prompt_tags WHERE file_id IN ({files}) GROUP BY tag", (cid,)).fetchall()) if n >= looks.MIN_FILES else {}
        sh = json.loads(row["shared"] or "{}") or {}
        known = [row["name"], sh.get("tag"), sh.get("series"), *(sh.get("appearance") or []), *[t for v in (sh.get("outfits") or {}).values() for t in v],
                 *((sh.get("lora") or {}).get("trigger") or [])]
        return {"n": n, "min": looks.MIN_FILES, "tags": looks.suggest(counts, n, known)}

    def character_picture(self, cid: int) -> str | None:
        """The character's picture in Model Librarian's list (its Danbooru picture, or a portrait picked here)."""
        with self.db.read() as c:
            row = c.execute("SELECT shared FROM characters WHERE id=?", (int(cid),)).fetchone()
        rel = (json.loads(row["shared"] or "{}") or {}).get("image") if row else None
        d = self.models.data_dir()
        if not d or not rel or not re.fullmatch(charstore.PICTURES + r"/[a-z0-9_]+\.(webp|jpg|png)", str(rel)):
            return None
        p = os.path.join(d, *str(rel).split("/"))
        return p if os.path.isfile(p) else None

    def character(self, cid: int, days: int | None = None) -> dict | None:
        """A character's page: its rules (with how they do), the LoRAs and strengths, what it's made with, over time."""
        cid = int(cid)
        with self.db.read() as c:
            ch = c.execute("SELECT id, name, aliases, cover, who, shared FROM characters WHERE id=?", (cid,)).fetchone()
            if not ch:
                return None
            out = dict(ch)
            out["aliases"] = json.loads(out["aliases"] or "[]")
            sh = json.loads(out.pop("shared") or "{}") or {}
            out["shared"] = {"linked": bool(sh.get("id")), "series": sh.get("series") or "", "tag": sh.get("tag") or "", "appearance": sh.get("appearance") or [],
                             "outfits": sh.get("outfits") or {}, "lora": sh.get("lora"), "source": sh.get("source") or {}, "picture": bool(sh.get("image")),
                             "ml": bool(self.models.data_dir()), "sync_error": self.char_sync_error}
            files = "FROM file_characters fc JOIN files f ON f.id=fc.file_id JOIN folders d ON d.id=f.folder_id WHERE fc.character_id=? AND f.missing=0 AND d.online=1"
            counts = dict(c.execute(f"SELECT fc.state, COUNT(*) {files} GROUP BY fc.state", (cid,)).fetchall())
            out["counts"] = {k: counts.get(k, 0) for k in ("auto", "confirmed", "review", "rejected")}
            out["n"] = out["counts"]["auto"] + out["counts"]["confirmed"]
            if not out["cover"]:
                r = c.execute(f"SELECT f.id {files} AND fc.state IN ('auto','confirmed') ORDER BY fc.state='confirmed' DESC, f.shot DESC LIMIT 1", (cid,)).fetchone()
                out["cover"] = r[0] if r else None
            first_last = c.execute(f"SELECT MIN(f.shot), MAX(f.shot) {files} AND fc.state IN ('auto','confirmed')", (cid,)).fetchone()
            out["first"], out["last"] = first_last[0], first_last[1]
            out["rules"] = chars.rule_stats(c, cid)
            counted = f"{files} AND fc.state IN ('auto','confirmed')"
            out["loras"] = [dict(r) for r in c.execute(
                f"SELECT u.name, COUNT(DISTINCT u.file_id) n, AVG(u.strength) avg, MIN(u.strength) lo, MAX(u.strength) hi {counted.replace('FROM file_characters fc', 'FROM file_characters fc JOIN uses u ON u.file_id=fc.file_id', 1)}"
                f" AND u.kind='lora' GROUP BY u.name COLLATE NOCASE ORDER BY n DESC LIMIT 8", (cid,))]
            # the strengths of the character's own LoRA (the ones its rules name), for the histogram
            own = [chars.norm(r["value"]) for r in out["rules"] if r["kind"] == "lora"]
            out["strengths"] = [r[1] for r in c.execute(
                f"SELECT u.name, u.strength {counted.replace('FROM file_characters fc', 'FROM file_characters fc JOIN uses u ON u.file_id=fc.file_id', 1)}"
                f" AND u.kind='lora' AND u.strength IS NOT NULL", (cid,)) if any(v in chars.norm(r[0]) for v in own)]
            out["models"] = [dict(r) for r in c.execute(
                f"SELECT u.name, COUNT(DISTINCT u.file_id) n {counted.replace('FROM file_characters fc', 'FROM file_characters fc JOIN uses u ON u.file_id=fc.file_id', 1)}"
                f" AND u.kind IN ('checkpoint','unet') GROUP BY u.name COLLATE NOCASE ORDER BY n DESC LIMIT 6", (cid,))]
            resolve = self.db.cached(("family-resolver",), lambda: families.Resolver(self.models))
            for m in out["models"]:
                m["family"] = resolve.family(m["name"])
                m["color"] = families.color(m["family"]) if m["family"] else None
            out["timeline"] = [dict(r) for r in c.execute(
                f"SELECT strftime('%Y-%m', f.shot, 'unixepoch', 'localtime') AS month, COUNT(*) n {counted} GROUP BY month ORDER BY month", (cid,))]
        return out

    def character_review(self, cid: int, file_ids, verdict: str) -> dict:
        """Yes or no for files in To review. A History step; answers for one character a little after one another join
        the same step ("Reviewed 14 files for Aurelia"), and Undo takes them all back - a rule that muted itself too."""
        if verdict not in ("confirm", "reject"):
            raise ValueError("verdict is confirm or reject")
        cid = int(cid)
        ids = list(dict.fromkeys(int(f) for f in file_ids))[:5000]
        with self.db.write() as c:
            ch = c.execute("SELECT name FROM characters WHERE id=?", (cid,)).fetchone()
            if not ch:
                raise KeyError(cid)
            was = {r["file_id"]: dict(state=r["state"], source=r["source"], at=r["at"]) for r in c.execute(
                f"SELECT file_id, state, source, at FROM file_characters WHERE character_id=? AND file_id IN ({','.join('?' * len(ids))})", (cid, *ids))} if ids else {}
            quiet = {r[0] for r in c.execute("SELECT id FROM character_rules WHERE character_id=? AND muted=1", (cid,))}
            chars.decide(c, cid, ids, verdict)
            muted = sorted({r[0] for r in c.execute("SELECT id FROM character_rules WHERE character_id=? AND muted=1", (cid,))} - quiet)
            items = [{"file_id": f, "was": was.get(f), "verdict": verdict, "muted": muted if k == len(ids) - 1 else []} for k, f in enumerate(ids)]
            last = c.execute("SELECT id, at, kind, undone, data FROM ops ORDER BY id DESC LIMIT 1").fetchone()
            run = json.loads(last["data"]) if last and last["kind"] == "review" and not last["undone"] and time.time() - last["at"] < REVIEW_RUN else None
            if run and run.get("character") == cid:
                run["items"] += items
                op = last["id"]
                c.execute("UPDATE ops SET at=?, summary=?, data=? WHERE id=?", (time.time(), _review_summary(ch[0], run["items"]), json.dumps(run), op))
            else:
                op = fileops.record(c, "review", _review_summary(ch[0], items), {"character": cid, "items": items})
        return {"op": op}

    def review_take_back(self, cid: int) -> dict | None:
        """Z on To review: the last answer for this character taken back (out of its History step). -> its file, or None."""
        with self.db.write() as c:
            last = run = None
            for r in c.execute("SELECT id, at, data FROM ops WHERE kind='review' AND undone IS NULL AND at > ? ORDER BY id DESC LIMIT 20",
                               (time.time() - REVIEW_RUN * 15,)):  # (a run of answers from this sitting, a half hour say)
                d = json.loads(r["data"])
                if d.get("character") == int(cid) and d.get("items"):
                    last, run = r, d
                    break
            if not run:
                return None
            it = run["items"].pop()
            bulk.review_restore(c, int(cid), [it])
            if run["items"]:
                name = c.execute("SELECT name FROM characters WHERE id=?", (int(cid),)).fetchone()
                c.execute("UPDATE ops SET summary=?, data=? WHERE id=?", (_review_summary(name[0] if name else "", run["items"]), json.dumps(run), last["id"]))
            else:
                c.execute("DELETE FROM ops WHERE id=?", (last["id"],))  # (nothing left in it)
        self.db.user_change()
        return {"file_id": it["file_id"]}

    def character_reset(self, cid: int, file_id: int) -> None:
        with self.db.write() as c:
            chars.undecide(c, int(cid), int(file_id))

    def review_queue(self, cid: int | None = None, limit: int = 60) -> list[dict]:
        """Files waiting for a yes or no, with the character and the rule kind that suggested it: up to `limit` of each
        character's (newest first), so one with many doesn't keep the others from showing."""
        with self.db.read() as c:
            rows = c.execute(
                "SELECT * FROM (SELECT fc.file_id, fc.character_id, ch.name AS character, fc.source, f.name, f.kind, f.thumb, f.width, f.height, f.mtime,"
                " ROW_NUMBER() OVER (PARTITION BY fc.character_id ORDER BY f.shot DESC) k"
                " FROM file_characters fc JOIN characters ch ON ch.id=fc.character_id JOIN files f ON f.id=fc.file_id JOIN folders d ON d.id=f.folder_id"
                " WHERE fc.state='review' AND f.missing=0 AND d.online=1" + (" AND fc.character_id=?" if cid else "") +
                ") WHERE k <= ? ORDER BY character COLLATE NOCASE, k", ((int(cid), limit) if cid else (limit,))).fetchall()
        return [{k: r[k] for k in r.keys() if k != "k"} for r in rows]

    def review_counts(self) -> dict[int, int]:
        """How many files wait for a yes or no, per character (all of them, not only the ones shown)."""
        with self.db.read() as c:
            return {r[0]: r[1] for r in c.execute(
                "SELECT fc.character_id, COUNT(*) FROM file_characters fc JOIN files f ON f.id=fc.file_id JOIN folders d ON d.id=f.folder_id"
                " WHERE fc.state='review' AND f.missing=0 AND d.online=1 GROUP BY fc.character_id")}

    def trigger_words(self, lora: str, guess: bool = True) -> list[str]:
        """Words to recognise a LoRA's character by: its trained words (from the sidecar files next to the LoRA,
        when Model Librarian's library is there), else the prompt parts that come with it most."""
        words = []
        have = self.models.models()
        if have["root"]:
            from .mllink import stem
            target = next((m for m in have["models"] if m["kind"] == "lora" and m["name"].lower() == stem(lora).lower()), None)
            if target:
                base = os.path.join(have["root"], "loras", *target["file"].split("/"))
                base = base[: -len(os.path.splitext(base)[1])]
                for side, keys in ((".metadata.json", ("civitai", "trainedWords")), (".civitai.info", ("trainedWords",)), (".cm-info.json", ("TrainedWords",))):
                    try:
                        with open(base + side, encoding="utf-8") as f:
                            d = json.load(f)
                        for k in keys:
                            d = d.get(k) if isinstance(d, dict) else None
                        for w in d or []:
                            for part in str(w).split(","):
                                if part.strip() and part.strip().lower() not in [x.lower() for x in words]:
                                    words.append(part.strip())
                    except (OSError, ValueError, AttributeError):
                        continue
        if not words and guess:
            with self.db.read() as c:
                withl = "SELECT DISTINCT file_id FROM uses WHERE kind='lora' AND name=? COLLATE NOCASE"
                with_n = c.execute(f"SELECT COUNT(*) FROM ({withl})", (stem_of(lora),)).fetchone()[0]
                if with_n:
                    rows = c.execute(f"SELECT tag, COUNT(*) n FROM prompt_tags WHERE file_id IN ({withl}) GROUP BY tag ORDER BY n DESC LIMIT 40", (stem_of(lora),)).fetchall()
                    for tag, n in rows:
                        total = c.execute("SELECT COUNT(DISTINCT file_id) FROM prompt_tags WHERE tag=?", (tag,)).fetchone()[0]
                        if n >= max(2, with_n // 2) and n / total >= 0.6:  # comes with this LoRA, rarely without it
                            words.append(tag)
        return words[:12]

    def set_file_character(self, file_id: int, cid: int, verdict: str | None) -> list[dict]:
        """From the details panel: confirm, reject, or forget (None) a character for one file."""
        with self.db.write() as c:
            if verdict:
                chars.decide(c, int(cid), [int(file_id)], verdict)
            else:
                chars.undecide(c, int(cid), int(file_id))
        return self.info(file_id)["characters"]

    # ---------------------------------------------------------------- artists
    def artists(self) -> dict:
        return self.db.cached_lazy(("artists-list",), self._artists)[0]

    def _artists(self) -> dict:
        with self.db.read() as c:
            counts = dict(c.execute("SELECT artist_id, COUNT(*) FROM files WHERE artist_id IS NOT NULL AND missing=0 GROUP BY artist_id").fetchall())
            sites: dict[int, list] = {}
            for aid, site, n in c.execute(
                    "SELECT artist_id, site, COUNT(*) n FROM files WHERE artist_id IS NOT NULL AND missing=0 AND site IS NOT NULL"
                    " GROUP BY artist_id, site ORDER BY n DESC"):
                sites.setdefault(aid, []).append(site)
            latest = dict(c.execute("SELECT artist_id, id FROM (SELECT artist_id, id, ROW_NUMBER() OVER (PARTITION BY artist_id ORDER BY shot DESC, id DESC) k"
                                    " FROM files WHERE artist_id IS NOT NULL AND missing=0) WHERE k=1").fetchall())
            out = []
            for a in c.execute("SELECT id, name, aliases, links, cover, auto FROM artists ORDER BY name COLLATE NOCASE").fetchall():
                n = counts.get(a["id"], 0)
                if not n and a["auto"]:
                    continue  # the Gallery's own, with its pictures in the Trash only
                cover = a["cover"] or latest.get(a["id"])
                out.append({"id": a["id"], "name": a["name"], "aliases": json.loads(a["aliases"] or "[]"), "links": json.loads(a["links"] or "[]"),
                            "cover": cover, "n": n, "sites": [credits.SITES.get(s, s) for s in sites.get(a["id"], [])[:3]]})
        return {"artists": out}

    def artist(self, aid: int) -> dict | None:
        """An artist's page: their other names and pages, where their pictures are from and where they're kept."""
        aid = int(aid)
        with self.db.read() as c:
            a = c.execute("SELECT id, name, aliases, links, cover, auto FROM artists WHERE id=?", (aid,)).fetchone()
            if not a:
                return None
            out = dict(a)
            out["aliases"], out["links"] = json.loads(a["aliases"] or "[]"), json.loads(a["links"] or "[]")
            live = "FROM files f JOIN folders d ON d.id=f.folder_id WHERE f.artist_id=? AND f.missing=0 AND d.online=1"
            out["n"] = c.execute(f"SELECT COUNT(*) {live}", (aid,)).fetchone()[0]
            if not out["cover"]:
                r = c.execute(f"SELECT f.id {live} ORDER BY f.shot DESC LIMIT 1", (aid,)).fetchone()
                out["cover"] = r[0] if r else None
            out["first"], out["last"] = c.execute(f"SELECT MIN(f.shot), MAX(f.shot) {live}", (aid,)).fetchone()
            out["sites"] = [{"site": r[0], "name": credits.SITES.get(r[0], r[0]), "n": r[1]} for r in c.execute(
                f"SELECT f.site, COUNT(*) n {live} AND f.site IS NOT NULL GROUP BY f.site ORDER BY n DESC", (aid,))]
            # the folders their pictures are kept in, most first (where a new one of theirs is filed)
            out["folders"] = [{"folder": r[0], "sub": r[1], "path": os.path.join(r[2], *r[1].split("/")) if r[1] else r[2], "n": r[3]} for r in c.execute(
                f"SELECT f.folder_id, CASE WHEN instr(f.rel, '/') THEN substr(f.rel, 1, length(f.rel) - length(f.name) - 1) ELSE '' END sub, d.path, COUNT(*) n {live}"
                " GROUP BY f.folder_id, sub ORDER BY n DESC, sub = '', length(sub) LIMIT 6", (aid,))]
            out["timeline"] = [dict(r) for r in c.execute(
                f"SELECT strftime('%Y-%m', f.shot, 'unixepoch', 'localtime') AS month, COUNT(*) n {live} GROUP BY month ORDER BY month", (aid,))]
        return out

    def artist_save(self, aid: int | None, name: str, aliases=(), links=(), cover=...) -> int:
        """Make (aid None) or change an artist. Another name that's already an artist's merges that artist into this one."""
        if not isinstance(name or "", str):
            raise ValueError("An artist's name is text.")
        name = (name or "").strip()[:credits.NAME_CAP]
        if not name:
            raise ValueError("An artist needs a name.")
        aliases = [a for a in metadata._split(list(aliases or [])) if chars.norm(a) != chars.norm(name)][:32]
        typed = [str(x).strip() for x in list(links or [])[:16] if str(x).strip()]
        if any(not credits.clean_url(x) for x in typed):
            raise ValueError("A link is a web address, like https://www.pixiv.net/users/12345.")
        links = list(dict.fromkeys(credits.clean_url(x) for x in typed))
        with self.db.write() as c:
            clash = c.execute("SELECT id, auto FROM artists WHERE name=? COLLATE NOCASE", (name,)).fetchone()
            hidden = clash and clash["auto"] and not c.execute("SELECT 1 FROM files WHERE artist_id=? AND missing=0", (clash["id"],)).fetchone()
            if clash and aid is None and hidden:  # (the Gallery's own, not in the list: yours now)
                aid = clash["id"]
            elif clash and clash["id"] != aid:
                raise ValueError(f"There's already an artist called {name}: give this one that as another name to make them one.")
            if aid is None:
                aid = c.execute("INSERT INTO artists (name, created, auto) VALUES (?,?,0)", (name, time.time())).lastrowid
            elif not c.execute("UPDATE artists SET name=?, auto=0 WHERE id=?", (name, int(aid))).rowcount:
                raise KeyError(aid)
            # another name that's someone else's: that artist (the same person, on another site) becomes this one
            spelled = {chars.norm(x) for x in [name, *aliases]}
            others = [r["id"] for r in c.execute("SELECT id, name, aliases FROM artists WHERE id <> ?", (aid,)).fetchall()
                      if spelled & {chars.norm(x) for x in [r["name"], *json.loads(r["aliases"] or "[]")]}]
            for other in others:
                o = c.execute("SELECT name, aliases, links, cover FROM artists WHERE id=?", (other,)).fetchone()
                aliases += [x for x in [o["name"], *json.loads(o["aliases"] or "[]")] if chars.norm(x) not in {chars.norm(y) for y in [name, *aliases]}]
                links += [u for u in json.loads(o["links"] or "[]") if u not in links]
                c.execute("UPDATE files SET artist_id=? WHERE artist_id=?", (aid, other))
                if o["cover"] and cover is ...:
                    c.execute("UPDATE artists SET cover=COALESCE(cover, ?) WHERE id=?", (o["cover"], aid))
                c.execute("DELETE FROM artists WHERE id=?", (other,))
            c.execute("UPDATE artists SET aliases=?, links=? WHERE id=?", (json.dumps(aliases[:32]), json.dumps(links[:16]), aid))
            if cover is not ...:
                c.execute("UPDATE artists SET cover=? WHERE id=?", (int(cover) if cover else None, aid))
            # the files whose credits name one of these (say, found before the artist had this other name)
            spelled = {chars.norm(x) for x in [name, *aliases]}
            loose = "lower(trim(replace(replace(artist, '_', ' '), '-', ' ')))"  # (norm, but for runs of spaces: checked below)
            ids = sorted({r[0] for r in c.execute(f"SELECT file_id, artist FROM credits WHERE artist <> '' AND {loose} IN ({','.join('?' * len(spelled))})",
                                                  sorted(spelled)) if chars.norm(r[1]) in spelled}) if aliases else []
            credits.resolve(c, ids)
            credits.prune(c)
        self.db.user_change()
        return aid

    def artist_delete(self, aid: int) -> bool:
        """The artist goes from the list; their pictures keep what was found in them (and get that artist back, made anew)."""
        with self.db.write() as c:
            ids = [r[0] for r in c.execute("SELECT id FROM files WHERE artist_id=?", (int(aid),))]
            if not c.execute("DELETE FROM artists WHERE id=?", (int(aid),)).rowcount:
                return False
            for part in chars._chunks(ids):  # what you typed for them goes too (it would bring the artist straight back)
                marks = ",".join("?" * len(part))
                c.execute(f"UPDATE credits SET artist=NULL WHERE src='user' AND file_id IN ({marks})", part)
                c.execute(f"DELETE FROM credits WHERE src='user' AND file_id IN ({marks}) AND artist IS NULL AND url IS NULL AND site IS NULL", part)
            credits.resolve(c, ids)
            credits.prune(c)
        self.db.user_change()
        return True

    def set_credit(self, file_id: int, artist=..., url=...) -> dict:
        """Your artist ('' none, None what was found) and source for one file."""
        fid = int(file_id)
        with self.db.write() as c:
            if not c.execute("SELECT 1 FROM files WHERE id=?", (fid,)).fetchone():
                raise KeyError(fid)
            credits.set_user(c, fid, artist, url)
            credits.resolve(c, [fid])
            credits.prune(c)
            out = credits.of_file(c, fid)
        self.db.user_change()
        return out

    # ---------------------------------------------------------------- swipe review
    def sort_queue(self, sort: str = "newest", folder: int | None = None, kind: str | None = None, q: str = "", seed: int = 0,
                   limit: int = 40) -> dict:
        """The pictures of a view not reviewed yet, in its order (the next few), and how many there are, and were."""
        sort = sort if sort in SORTS and sort not in ("trashed",) else "newest"
        frm, args = self._from(folder, kind, q, sort)
        order_args = [int(seed) % 4294967291] if sort == "random" else []
        with self.db.read() as c:
            left = c.execute(f"SELECT COUNT(*) FROM {frm} AND f.id NOT IN (SELECT file_id FROM reviewed)", args).fetchone()[0]
            done = c.execute(f"SELECT COUNT(*) FROM {frm} AND f.id IN (SELECT file_id FROM reviewed)", args).fetchone()[0]
            ids = [r[0] for r in c.execute(f"SELECT f.id FROM {frm} AND f.id NOT IN (SELECT file_id FROM reviewed) ORDER BY {SORTS[sort]} LIMIT ?",
                                           [*args, *order_args, max(1, min(200, int(limit)))])]
            rows = {r["id"]: dict(r) for r in c.execute(f"SELECT {ITEM} FROM files f LEFT JOIN marks m ON m.file_id=f.id WHERE f.id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
        return {"left": left, "reviewed": done, "items": [rows[i] for i in ids if i in rows]}

    def sort_decide(self, file_id: int, verdict: str, value=None) -> dict:
        """keep (it stays), fav (kept, a favourite), rate (kept, rated value) -> what it was, to undo it."""
        if verdict not in ("keep", "fav", "rate"):
            raise ValueError("keep, fav or rate")
        fid = int(file_id)
        with self.db.read() as c:
            m = c.execute("SELECT rating, favorite, source, updated FROM marks WHERE file_id=?", (fid,)).fetchone()
        was = dict(m) if m else None
        if verdict == "fav":
            self.mark(fid, favorite=True)
        elif verdict == "rate":
            self.mark(fid, rating=int(value or 0))
        with self.db.write() as c:
            if not c.execute("SELECT 1 FROM files WHERE id=?", (fid,)).fetchone():
                raise KeyError(fid)
            c.execute("INSERT OR REPLACE INTO reviewed (file_id, at) VALUES (?, ?)", (fid, time.time()))
        return {"was": was}

    def sort_undo(self, file_id: int, was) -> None:
        """A keep taken back: not reviewed any more, its rating and favourite as they were."""
        fid = int(file_id)
        with self.db.write() as c:
            c.execute("DELETE FROM reviewed WHERE file_id=?", (fid,))
            if was is None:
                c.execute("DELETE FROM marks WHERE file_id=?", (fid,))
            elif isinstance(was, dict):
                try:
                    rating = max(0, min(5, int(was.get("rating") or 0)))  # (it comes back from the page: kept to what a rating can be)
                except (TypeError, ValueError):
                    rating = 0
                source = was.get("source") if was.get("source") in ("user", "workflow", "eagle") else "user"
                c.execute("INSERT OR REPLACE INTO marks (file_id, rating, favorite, source, updated) VALUES (?,?,?,?,?)",
                          (fid, rating, int(bool(was.get("favorite"))), source, time.time()))

    def sort_reset(self, sort: str = "newest", folder: int | None = None, kind: str | None = None, q: str = "") -> int:
        """Review a view again from the start: its pictures aren't reviewed any more."""
        frm, args = self._from(folder, kind, q, sort if sort in SORTS else "newest")
        with self.db.write() as c:
            return c.execute(f"DELETE FROM reviewed WHERE file_id IN (SELECT f.id FROM {frm})", args).rowcount

    def viewed(self, file_id: int) -> None:
        with self.db.write() as c:
            if c.execute("SELECT 1 FROM files WHERE id=?", (int(file_id),)).fetchone():
                c.execute("INSERT OR REPLACE INTO views (file_id, at) VALUES (?, ?)", (int(file_id), time.time()))
                c.execute("DELETE FROM views WHERE file_id NOT IN (SELECT file_id FROM views ORDER BY at DESC LIMIT ?)", (RECENT,))

    def file_path(self, file_id: int) -> str | None:
        """Where the file is now (in the Trash: its place in .mlg-trash)."""
        with self.db.read() as c:
            r = c.execute("SELECT f.rel, f.trash_rel, f.missing, d.path FROM files f JOIN folders d ON d.id=f.folder_id WHERE f.id=? AND f.missing IN (0, 2, 3)",
                          (int(file_id),)).fetchone()
        if not r:
            return None
        return os.path.join(r["path"], *(r["trash_rel"] if r["missing"] == 2 else r["rel"]).split("/"))

    # ---------------------------------------------------------------- organising
    def resolve(self, sel) -> list[int]:
        """A selection: a list of ids, or a whole view ({"view": {sort, folder, kind, q}}) -> ids."""
        if isinstance(sel, dict) and isinstance(sel.get("view"), dict):
            v = sel["view"]
            sort = v.get("sort") if v.get("sort") in SORTS else "newest"
            frm, args = self._from(int(v["folder"]) if str(v.get("folder", "")).isdigit() else None, v.get("kind") or None, str(v.get("q") or "")[:2000], sort)
            order_args = [int(v.get("seed") or 0) % 4294967291] if sort == "random" else []
            if str(v.get("upto", "")).isdigit():
                frm += " AND f.id <= ?"
                args = [*args, int(v["upto"])]
            with self.db.read() as c:
                return [r[0] for r in c.execute(f"SELECT f.id FROM {frm} ORDER BY {SORTS[sort]} LIMIT ?", [*args, *order_args, fileops.MAX_ITEMS])]
        if isinstance(sel, list):
            return [int(i) for i in sel[:fileops.MAX_ITEMS]]
        raise fileops.OpError("A selection is a list of ids or a view.")

    def albums(self) -> list[dict]:
        with self.db.read() as c:
            out = [dict(r) for r in c.execute(
                "SELECT al.id, al.name, al.cover, al.icon, al.color, al.query, (SELECT COUNT(*) FROM album_files af JOIN files f ON f.id=af.file_id WHERE af.album_id=al.id AND af.removed=0 AND f.missing=0) n,"
                " (SELECT af.file_id FROM album_files af JOIN files f ON f.id=af.file_id WHERE af.album_id=al.id AND af.removed=0 AND f.missing=0 ORDER BY f.shot DESC LIMIT 1) latest"
                " FROM albums al ORDER BY al.name COLLATE NOCASE")]
        for a in out:  # a smart album holds what its search finds (counted ahead, like Statistics: the last count at once,
            if a["query"] is not None:  # and stale: True while it's counted again, for the sidebar to ask once more)
                (a["n"], a["latest"]), fresh = self.db.cached_lazy(("smart", a["query"]), lambda q=a["query"]: self._smart_count(q))
                a["stale"] = not fresh
        return out

    def album_groups(self, albums: list[dict]) -> list[dict]:
        """Every album name that has albums inside it - an album, or only their parent ("Trips" of "Trips / Coast") -
        with what it and they hold, each file once: {name, n, latest, stale}."""
        names: dict[str, str] = {}
        for a in albums:
            parts = search.album_name(a["name"]).split(" / ")
            for i in range(1, len(parts)):
                p = " / ".join(parts[:i])
                names.setdefault(p.casefold(), p)
        out = []
        for key, p in sorted(names.items()):
            (n, latest), fresh = self.db.cached_lazy(("album-tree", key), lambda p=p: self._smart_count(f'album:"{p}"'))
            out.append({"name": p, "n": n, "latest": latest, "stale": not fresh})
        return out

    def _tidy_album_names(self) -> None:
        """Albums named before nesting ("AC/DC", a name with "): written the one way now ("AC / DC"), unless that's taken."""
        with self.db.read() as c:
            rows = c.execute("SELECT id, name FROM albums").fetchall()
        odd = [(r[0], search.album_name(r[1])) for r in rows if search.album_name(r[1]) != r[1]]
        if not odd:
            return
        with self.db.write() as c:
            for aid, name in odd:
                if name and not c.execute("SELECT 1 FROM albums WHERE name=? COLLATE NOCASE AND id<>?", (name, aid)).fetchone():
                    c.execute("UPDATE albums SET name=? WHERE id=?", (name, aid))

    def album_move(self, path: str, to: str) -> dict:
        """An album and the albums inside it (or, for a name that's only their parent, those) to another name:
        "Trips / Coast" to "Coast" brings it to the top; to "Summer / Coast", into Summer."""
        old, new = search.album_name(path), search.album_name(to, cut=False)
        if not old or not new:
            raise ValueError("An album needs a name.")
        if new.casefold().startswith(old.casefold() + " / "):
            raise ValueError("An album can't go inside itself.")
        depth = len(old.split(" / "))
        with self.db.write() as c:
            rows = [(r[0], search.album_name(r[1])) for r in c.execute("SELECT id, name FROM albums")]
            moving = [(i, n) for i, n in rows if n.casefold() == old.casefold() or n.casefold().startswith(old.casefold() + " / ")]
            if not moving:
                raise KeyError(path)
            renamed = [(i, " / ".join([new, *n.split(" / ")[depth:]])) for i, n in moving]
            ids = {i for i, _ in moving}
            taken = {n.casefold() for i, n in rows if i not in ids}
            names = [n.casefold() for _, n in renamed]
            for _, n in renamed:
                if n.casefold() in taken or names.count(n.casefold()) > 1:
                    raise ValueError(f"There's already an album called {n}.")
                if len(n) > 100:
                    raise ValueError(f"{n} is too long a name (100 letters at most).")
            for i, n in renamed:
                c.execute("UPDATE albums SET name=? WHERE id=?", (n, i))
            changed_queries = []
            # smart albums whose search names one of them: the new name in it
            def renamed_filter(m):  # (an album, or a name that's only their parent)
                name = search.album_name(m.group(2) if m.group(2) is not None else m.group(3))
                if name.casefold() != old.casefold() and not name.casefold().startswith(old.casefold() + " / "):
                    return m.group(0)
                return f'{m.group(1)}"{" / ".join([new, *name.split(" / ")[depth:]])}"'
            for aid, q in c.execute("SELECT id, query FROM albums WHERE query IS NOT NULL").fetchall():
                q2 = search.ALBUM_FILTER.sub(renamed_filter, q)
                if q2 != q:
                    c.execute("UPDATE albums SET query=? WHERE id=?", (q2, aid))
                    changed_queries.append([aid, q, q2])
            same_place = old.rsplit(" / ", 1)[0] == new.rsplit(" / ", 1)[0] if " / " in old and " / " in new else " / " not in old and " / " not in new
            what = f"Renamed the album {old} to {new.split(' / ')[-1]}" if same_place else f"Moved the album {old} to {new}"
            op = fileops.record(c, "album_move", what + (f" (with the {len(renamed) - 1} inside it)" if len(renamed) > 1 else ""),
                                {"names": [[i, n, m] for (i, n), (_, m) in zip(moving, renamed)], "queries": changed_queries})
        return {"moved": len(renamed), "name": new, "op": op}

    def album_mirrors(self) -> list[dict]:
        """Albums holding exactly the pictures of one folder (with the folders inside it), whatever either is called -
        Eagle's folders brought in as both, say: {id, name, n, folder_id, folder, rel}, rel the subfolder ('' for the folder)."""
        with self.db.read() as c:
            roots = {r[0]: r[1] for r in c.execute("SELECT id, path FROM folders")}
            names = {r[0]: r[1] for r in c.execute("SELECT id, name FROM albums")}
            held: dict[int, list] = {}
            for aid, fid, rel in c.execute("SELECT af.album_id, f.folder_id, f.rel FROM album_files af JOIN albums al ON al.id=af.album_id"
                                           " JOIN files f ON f.id=af.file_id WHERE af.removed=0 AND f.missing=0 AND al.query IS NULL"):
                held.setdefault(aid, []).append((fid, rel))
            out = []
            for aid, files in held.items():
                if len({fid for fid, _ in files}) != 1:
                    continue  # (pictures from two library folders: no one folder holds them all)
                fid = files[0][0]
                common = files[0][1].split("/")[:-1]
                for _, rel in files[1:]:  # the deepest folder holding them all
                    d = rel.split("/")[:-1]
                    k = 0
                    while k < min(len(common), len(d)) and common[k].casefold() == d[k].casefold():
                        k += 1
                    common = common[:k]
                sub = "/".join(common)
                n = (c.execute("SELECT COUNT(*) FROM files WHERE folder_id=? AND missing=0 AND rel LIKE ? ESCAPE '\\'", (fid, f"{search._like(sub)}/%"))
                     if sub else c.execute("SELECT COUNT(*) FROM files WHERE folder_id=? AND missing=0", (fid,))).fetchone()[0]
                if n == len(files):  # it holds all of them, so the same pictures
                    root = roots.get(fid, "")
                    out.append({"id": aid, "name": names[aid], "n": n, "folder_id": fid, "rel": sub,
                                "folder": os.path.join(os.path.basename(root.rstrip("\\/")) or root, *common)})
        return sorted(out, key=lambda a: a["name"].casefold())

    def albums_delete(self, ids, summary: str | None = None) -> dict:
        """Albums gone (their files stay where they are) as one History step: Undo brings them back with their files."""
        ids = sorted({int(i) for i in ids})
        with self.db.write() as c:
            gone = []
            for aid in ids:
                a = c.execute("SELECT id, name, cover, created, icon, color, query FROM albums WHERE id=?", (aid,)).fetchone()
                if a:
                    files = [dict(r) for r in c.execute("SELECT file_id, source, removed, added FROM album_files WHERE album_id=?", (aid,))]
                    gone.append({**dict(a), "files": files})
            if not gone:
                return {"deleted": 0, "op": None}
            c.execute(f"DELETE FROM album_files WHERE album_id IN ({','.join('?' * len(gone))})", [g["id"] for g in gone])
            c.execute(f"DELETE FROM albums WHERE id IN ({','.join('?' * len(gone))})", [g["id"] for g in gone])
            n = len(gone)
            op = fileops.record(c, "album_delete", summary or (f"Deleted the album {gone[0]['name']}" if n == 1 else f"Deleted {n} albums"), {"albums": gone})
        return {"deleted": n, "op": op}

    def _smart_count(self, q: str) -> tuple[int, int | None]:
        try:
            got = self.files(limit=1, q=q)
            return got["total"], (got["items"][0]["id"] if got["items"] else None)
        except Exception:  # a search that can't be made (old, or odd): empty, not an error for the whole list
            return 0, None

    def album_save(self, aid: int | None, name: str, cover=..., query=...) -> dict:
        """Make (aid None) or change an album; query: a smart album's search (None: an album of files you put in it)."""
        if not isinstance(name or "", str):
            raise ValueError("An album's name is text.")
        if query is not ... and query is not None:
            query = str(query).strip()[:2000]
            if not query:
                raise ValueError("A smart album needs a search.")
        name = search.album_name(name, cut=False)  # (a / in it puts it inside another: "Trips / Coast" is in Trips)
        if not name:
            raise ValueError("An album needs a name.")
        moved = None
        if aid is not None:  # renamed: the albums inside it go with it
            with self.db.read() as c:
                was = c.execute("SELECT name FROM albums WHERE id=?", (int(aid),)).fetchone()
            moved = self.album_move(was[0], name) if was and search.album_name(was[0]) != name else None
        with self.db.write() as c:
            clash = next((r[0] for r in c.execute("SELECT id, name FROM albums") if search.album_name(r[1]).casefold() == name.casefold()), None)
            if clash and clash != aid:  # (Été and été too, which SQLite's NOCASE doesn't fold)
                raise ValueError(f"There's already an album called {name}.")
            if aid is None:
                aid = c.execute("INSERT INTO albums (name, created) VALUES (?,?)", (name, time.time())).lastrowid
            elif not c.execute("UPDATE albums SET name=? WHERE id=?", (name, int(aid))).rowcount:
                raise KeyError(aid)
            if cover is not ...:
                c.execute("UPDATE albums SET cover=? WHERE id=?", (int(cover) if cover else None, aid))
            if query is not ...:
                c.execute("UPDATE albums SET query=? WHERE id=?", (query, aid))
                if query is not None:
                    c.execute("DELETE FROM album_files WHERE album_id=?", (aid,))  # what it holds is what the search finds
        out = next(a for a in self.albums() if a["id"] == aid)
        return {**out, "op": moved["op"]} if moved else out  # (a rename: its History step, for Undo)

    def album_delete(self, aid: int) -> dict | None:
        """The album goes; its files stay where they are (a History step: Undo brings it back). None: no such album."""
        r = self.albums_delete([aid])
        return r if r["deleted"] else None

    def duplicates(self, limit: int = 300) -> dict:
        out, fresh = self.db.cached_lazy(("duplicates", limit), lambda: self._duplicates(limit))
        return {**out, "stale": not fresh}

    def _duplicates(self, limit: int = 300) -> dict:
        """Files that are the same: exact copies (the same bytes), and the same picture saved twice (another format
        or metadata). Each group lists its files, the one to keep first (with generation data, then the biggest, then the oldest)."""
        cols = ("f.id, f.name, f.rel, f.size, f.mtime, f.width, f.height, f.kind, f.duration, f.meta, f.folder_id, d.path AS folder_path,"
                " COALESCE(m.rating, 0) rating, COALESCE(m.favorite, 0) favorite")
        live = "f.missing=0 AND d.online=1"
        with self.db.read() as c:
            exact = c.execute(f"SELECT f.fp, f.size FROM files f JOIN folders d ON d.id=f.folder_id WHERE {live} AND f.fp IS NOT NULL"
                              " GROUP BY f.fp, f.size HAVING COUNT(*) > 1 LIMIT ?", (limit,)).fetchall()
            pics = c.execute(f"SELECT f.id, f.fp, f.phash FROM files f JOIN folders d ON d.id=f.folder_id WHERE {live}"
                             " AND f.phash IS NOT NULL AND f.phash NOT IN ('0000000000000000', 'ffffffffffffffff')").fetchall()
            groups = []
            for g in exact:
                rows = c.execute(f"SELECT {cols} FROM files f JOIN folders d ON d.id=f.folder_id LEFT JOIN marks m ON m.file_id=f.id"
                                 f" WHERE {live} AND f.fp=? AND f.size=?", (g["fp"], g["size"])).fetchall()
                groups.append({"kind": "copies", "files": [dict(r) for r in rows]})
            for ids in similar([(r["id"], r["fp"], r["phash"]) for r in pics])[:limit]:
                rows = c.execute(f"SELECT {cols} FROM files f JOIN folders d ON d.id=f.folder_id LEFT JOIN marks m ON m.file_id=f.id"
                                 f" WHERE f.id IN ({','.join('?' * len(ids))})", ids).fetchall()
                groups.append({"kind": "same picture", "files": [dict(r) for r in rows]})
            kept = {r[0] for r in c.execute("SELECT files FROM dupes_kept")}
        offered = []
        for g in groups:
            g["key"] = dupes_key(f["id"] for f in g["files"])
            if g["key"] in kept:
                continue  # you kept all of them (it's offered again if another copy turns up, or one goes)
            g["files"].sort(key=lambda f: (-(f["rating"] or 0), -(f["favorite"] or 0), f["meta"] != 1, -(f["size"] or 0), f["mtime"], f["id"]))  # then the one found first
            g["wasted"] = sum(f["size"] or 0 for f in g["files"][1:])
            offered.append(g)
        offered.sort(key=lambda g: -g["wasted"])
        return {"groups": offered, "wasted": sum(g["wasted"] for g in offered), "kept": len(kept & {g["key"] for g in groups})}

    def duplicates_keep(self, ids) -> dict:
        """Keep both (or all): the group isn't offered again while it's these files. One History step."""
        key = dupes_key(ids)
        if key.count(",") < 1:
            raise ValueError("A group is two files or more.")
        with self.db.write() as c:
            c.execute("INSERT OR REPLACE INTO dupes_kept (files, at) VALUES (?, ?)", (key, time.time()))
            n = key.count(",") + 1
            op = fileops.record(c, "dupes_kept", f"Kept {'both' if n == 2 else f'all {n}'} of a duplicate group", {"keys": [key]})
        return {"op": op}

    def duplicates_unkeep(self) -> dict:
        """Offer the groups you kept again (one History step: Undo keeps them again)."""
        with self.db.write() as c:
            keys = [r[0] for r in c.execute("SELECT files FROM dupes_kept")]
            c.execute("DELETE FROM dupes_kept")
            op = fileops.record(c, "dupes_unkept", f"Offered {len(keys)} kept duplicate groups again", {"keys": keys}) if keys else None
        return {"op": op, "count": len(keys)}

    def _warm(self) -> None:
        """When indexing has gone quiet, count up Statistics and the like ahead of time, so they open at once."""
        from . import stats
        done = -1
        while not self._stop.wait(20):
            if self.scanning or self._queue or self.db.version == done:
                continue
            try:
                if self.thumbs.pending() or self.meta.pending():
                    continue
                stats.overview(self, 30, self.models, fresh=True)
                self.characters()
                self.duplicates()
                done = self.db.version
            except Exception:
                continue

    def _backups(self) -> None:
        """A copy of the index once a day (the last 7 kept), while the Gallery runs."""
        while not self._stop.is_set():
            try:
                self.backup()
            except Exception:
                pass
            self._stop.wait(6 * 3600)

    def backup(self, force: bool = False) -> str | None:
        import sqlite3
        folder = os.path.join(self.data_dir, "backups")
        os.makedirs(folder, exist_ok=True)
        today = os.path.join(folder, time.strftime("library-%Y-%m-%d.db"))
        if os.path.exists(today) and not force:
            return None
        tmp = today + ".part"
        src = self.db.connect()
        try:
            dst = sqlite3.connect(tmp)
            with dst:
                src.backup(dst)
            dst.close()
        finally:
            src.close()
        os.replace(tmp, today)
        old = sorted(f for f in os.listdir(folder) if f.startswith("library-") and f.endswith(".db"))
        for f in old[:-BACKUPS]:
            try:
                os.remove(os.path.join(folder, f))
            except OSError:
                pass
        return today

    # ---------------------------------------------------------------- what the library is for
    @property
    def kinds(self) -> list[str] | None:
        """What it's for, one or more of KINDS; None until you've said (the Gallery asks once). It decides what the
        screens show: what made each file and model statistics (ai), Live Photos and People (photos)..."""
        with self.db.read() as c:
            r = c.execute("SELECT value FROM library_meta WHERE key='kind'").fetchone()
        got = [k for k in KINDS if r and k in r[0].split(",")]
        return got or None

    def set_kinds(self, kinds) -> list[str]:
        kinds = [kinds] if isinstance(kinds, str) else list(kinds or [])
        if not kinds or any(k not in KINDS for k in kinds):
            raise ValueError(f"A library is for one or more of: {', '.join(KINDS)}.")
        kinds = [k for k in KINDS if k in kinds]
        with self.db.write() as c:
            c.execute("INSERT INTO library_meta (key, value) VALUES ('kind', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (",".join(kinds),))
            scanner.pair_live(c)  # Live Photos are a photo library's: paired, or apart again
            c.execute("UPDATE folders SET file_count=(SELECT COUNT(*) FROM files f WHERE f.folder_id=folders.id AND f.missing=0)")
        self.credits.nudge()  # for illustrations: who made each one is looked for
        return kinds

    set_kind = set_kinds  # one kind, or several

    def status(self) -> dict:
        kinds = self.kinds
        try:
            inbox = self.inbox.count() if "illustrations" in (kinds or []) else 0
        except Exception:
            inbox = 0
        with self.db.read() as c:
            total = c.execute("SELECT COUNT(*) FROM files WHERE missing=0").fetchone()[0]
            trash = c.execute(f"SELECT COUNT(*) FROM files f WHERE f.missing=2 AND f.id NOT IN ({TRASHED_MOTION})").fetchone()[0]
            offline = c.execute("SELECT COUNT(*) FROM folders WHERE online=0").fetchone()[0]
        return {"files": total, "offline": offline, "scanning": self.scanning, "last_scan": self.last_scan,
                "queued": len(self._queue), "thumbs_pending": self.thumbs.pending(), "thumbs_failed": self.thumbs.failed,
                "meta_pending": self.meta.pending(), "trash": trash, "kinds": kinds, "inbox": inbox,
                "dates_pending": self.dater.pending(), "dates_moved": self.dater.moved, "credits_pending": self.credits.pending()}
