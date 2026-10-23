"""The library: your folders, the scans that index them, the thumbnails, and what's happening right now.

On start every folder is rescanned (quick: unchanged files are only stat'ed) and thumbnails carry on where
they stopped, so a session that was closed mid-way simply continues.
"""
from __future__ import annotations

import os
import threading
import time

import json

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


def stem_of(name: str) -> str:
    from .mllink import stem
    return stem(name)


class Library:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.settings = Settings(data_dir)
        self.db = Database(os.path.join(data_dir, "library.db"))
        self.thumbs = Thumbnails(self.db, os.path.join(data_dir, "thumbs"), self.settings)
        self.meta = metadata.MetaReader(self.db)
        self.watcher = Watcher(self)
        self.models = ModelLibrary(self.settings)
        self._scan_lock = threading.Lock()
        self._queue: list[int] = []
        self._queue_cv = threading.Condition()
        self._stop = threading.Event()
        self.scanning: dict | None = None  # {folder, path, seen, added, ...} while a scan runs
        self.last_scan: dict | None = None

    # ---------------------------------------------------------------- lifecycle
    def start(self, rescan: bool = True) -> None:
        threading.Thread(target=metadata.backfill_prompt_tags, args=(self.db,), name="prompt-tags", daemon=True).start()
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
        with self.db.read() as c:
            out["characters"] = [dict(r) for r in c.execute(
                "SELECT ch.id, ch.name, fc.state, fc.source FROM file_characters fc JOIN characters ch ON ch.id=fc.character_id"
                " WHERE fc.file_id=? AND fc.state <> 'rejected' ORDER BY ch.name COLLATE NOCASE", (fid,))]
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
            chars.match_file(c, fid)  # a tag rule may match now (or no longer)
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

    # ---------------------------------------------------------------- characters
    def characters(self) -> dict:
        with self.db.read() as c:
            rows = c.execute(
                "SELECT ch.id, ch.name, ch.aliases, ch.cover,"
                " (SELECT COUNT(*) FROM file_characters fc JOIN files f ON f.id=fc.file_id WHERE fc.character_id=ch.id AND fc.state IN ('auto','confirmed') AND f.missing=0) n,"
                " (SELECT COUNT(*) FROM file_characters fc JOIN files f ON f.id=fc.file_id WHERE fc.character_id=ch.id AND fc.state='review' AND f.missing=0) review,"
                " (SELECT fc.file_id FROM file_characters fc JOIN files f ON f.id=fc.file_id WHERE fc.character_id=ch.id AND fc.state IN ('auto','confirmed') AND f.missing=0"
                "  ORDER BY fc.state='confirmed' DESC, f.mtime DESC LIMIT 1) latest"
                " FROM characters ch ORDER BY ch.name COLLATE NOCASE").fetchall()
            sugg = chars.suggestions(c)
        out = []
        for r in rows:
            d = dict(r)
            d["aliases"] = json.loads(d["aliases"] or "[]")
            d["cover"] = d["cover"] or d.pop("latest")
            d.pop("latest", None)
            out.append(d)
        return {"characters": out, "review": sum(d["review"] for d in out), "suggestions": sugg}

    def character_save(self, cid: int | None, name: str, aliases=(), rules=None, cover=...) -> int:
        """Create (cid None) or change a character; its files are matched again."""
        name = str(name or "").strip()[:100]
        if not name:
            raise ValueError("A character needs a name.")
        aliases = [a for a in metadata._split(list(aliases or [])) if a.lower() != name.lower()][:16]
        with self.db.write() as c:
            clash = c.execute("SELECT id FROM characters WHERE name=? COLLATE NOCASE", (name,)).fetchone()
            if clash and clash[0] != cid:
                raise ValueError(f"There's already a character called {name}.")
            if cid is None:
                cid = c.execute("INSERT INTO characters (name, aliases, created) VALUES (?,?,?)", (name, json.dumps(aliases), time.time())).lastrowid
            else:
                if not c.execute("SELECT 1 FROM characters WHERE id=?", (cid,)).fetchone():
                    raise KeyError(cid)
                c.execute("UPDATE characters SET name=?, aliases=? WHERE id=?", (name, json.dumps(aliases), cid))
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
        return cid

    def character_delete(self, cid: int) -> bool:
        with self.db.write() as c:
            return c.execute("DELETE FROM characters WHERE id=?", (int(cid),)).rowcount > 0

    def character(self, cid: int, days: int | None = None) -> dict | None:
        """A character's page: its rules (with how they do), the LoRAs and strengths, what it's made with, over time."""
        cid = int(cid)
        with self.db.read() as c:
            ch = c.execute("SELECT id, name, aliases, cover FROM characters WHERE id=?", (cid,)).fetchone()
            if not ch:
                return None
            out = dict(ch)
            out["aliases"] = json.loads(out["aliases"] or "[]")
            files = "FROM file_characters fc JOIN files f ON f.id=fc.file_id JOIN folders d ON d.id=f.folder_id WHERE fc.character_id=? AND f.missing=0 AND d.online=1"
            counts = dict(c.execute(f"SELECT fc.state, COUNT(*) {files} GROUP BY fc.state", (cid,)).fetchall())
            out["counts"] = {k: counts.get(k, 0) for k in ("auto", "confirmed", "review", "rejected")}
            out["n"] = out["counts"]["auto"] + out["counts"]["confirmed"]
            if not out["cover"]:
                r = c.execute(f"SELECT f.id {files} AND fc.state IN ('auto','confirmed') ORDER BY fc.state='confirmed' DESC, f.mtime DESC LIMIT 1", (cid,)).fetchone()
                out["cover"] = r[0] if r else None
            first_last = c.execute(f"SELECT MIN(f.mtime), MAX(f.mtime) {files} AND fc.state IN ('auto','confirmed')", (cid,)).fetchone()
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
            out["timeline"] = [dict(r) for r in c.execute(
                f"SELECT strftime('%Y-%m', f.mtime, 'unixepoch', 'localtime') AS month, COUNT(*) n {counted} GROUP BY month ORDER BY month", (cid,))]
        return out

    def character_review(self, cid: int, file_ids, verdict: str) -> None:
        if verdict not in ("confirm", "reject"):
            raise ValueError("verdict is confirm or reject")
        with self.db.write() as c:
            if not c.execute("SELECT 1 FROM characters WHERE id=?", (int(cid),)).fetchone():
                raise KeyError(cid)
            chars.decide(c, int(cid), [int(f) for f in file_ids][:5000], verdict)

    def character_reset(self, cid: int, file_id: int) -> None:
        with self.db.write() as c:
            chars.undecide(c, int(cid), int(file_id))

    def review_queue(self, cid: int | None = None, limit: int = 200) -> list[dict]:
        """Files waiting for a yes or no, with the character and the rule kind that suggested it."""
        with self.db.read() as c:
            rows = c.execute(
                "SELECT fc.file_id, fc.character_id, ch.name AS character, fc.source, f.name, f.kind, f.thumb, f.width, f.height, f.mtime"
                " FROM file_characters fc JOIN characters ch ON ch.id=fc.character_id JOIN files f ON f.id=fc.file_id JOIN folders d ON d.id=f.folder_id"
                " WHERE fc.state='review' AND f.missing=0 AND d.online=1" + (" AND fc.character_id=?" if cid else "") +
                " ORDER BY ch.name COLLATE NOCASE, f.mtime DESC LIMIT ?", ((int(cid), limit) if cid else (limit,))).fetchall()
        return [dict(r) for r in rows]

    def trigger_words(self, lora: str) -> list[str]:
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
        if not words:
            with self.db.read() as c:
                withl = [r[0] for r in c.execute("SELECT DISTINCT file_id FROM uses WHERE kind='lora' AND name=? COLLATE NOCASE", (stem_of(lora),))]
                if withl:
                    rows = c.execute(f"SELECT tag, COUNT(*) n FROM prompt_tags WHERE file_id IN ({','.join('?' * len(withl))}) GROUP BY tag ORDER BY n DESC LIMIT 40", withl).fetchall()
                    for tag, n in rows:
                        total = c.execute("SELECT COUNT(DISTINCT file_id) FROM prompt_tags WHERE tag=?", (tag,)).fetchone()[0]
                        if n >= max(2, len(withl) // 2) and n / total >= 0.6:  # comes with this LoRA, rarely without it
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
