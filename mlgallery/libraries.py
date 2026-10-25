"""Libraries: a folder that carries its own index, so it can be copied to a drive and opened on any PC.

A library is an ordinary folder of your files with a hidden `.mlgallery` folder inside it: the index
(library.db), the thumbnails and the daily backups. Your files stay ordinary files in ordinary folders.
Open it from any drive letter or place: the folders inside it are re-anchored to where it is now (`rebase`).
Folders added from outside it keep their full path, so on another PC they show as not reachable.

This PC's own library (the index in %LOCALAPPDATA%\\Model Librarian Gallery, no folder of its own) is still
there, as before. What belongs to a PC rather than to a library stays in that folder too (`home_dir`): the
settings (ffmpeg, ComfyUI's address...), the update check, the window's browser data and the recent libraries.

A new library can bring the ratings, tags, albums, characters and notes of the library it was made from, for
the same files (matched by fingerprint): `bring_marks`, one step in History.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid

from . import APP_NAME
from .config import default_data_dir, write_atomic

DATA = ".mlgallery"
INFO = "library.json"
RECENT = 8


def home_dir() -> str:
    """This PC's own folder: settings, the recent libraries, the update check (MLG_HOME for the tests)."""
    env = os.environ.get("MLG_HOME")
    if env:
        return env
    return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), APP_NAME)


def data_dir(root: str) -> str:
    return os.path.join(root, DATA)


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(p)))


def same(a: str, b: str) -> bool:
    return _norm(a) == _norm(b)


def inside(path: str, root: str) -> bool:
    """path is root, or in it (whole folder names: D:\\Gen isn't inside D:\\Generations' parent D:\\Ge)."""
    p, r = _norm(path), _norm(root)
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


def is_library(root: str | None) -> bool:
    return bool(root) and os.path.isfile(os.path.join(root, DATA, INFO))


def find(path: str) -> str | None:
    """The library a folder is (its own folder, or the .mlgallery inside it), or None."""
    path = os.path.abspath(str(path or "").strip().strip('"'))
    if os.path.basename(path.rstrip("\\/")).lower() == DATA:
        path = os.path.dirname(path.rstrip("\\/"))
    return path if is_library(path) else None


def enclosing(path: str) -> str | None:
    """The library a folder is inside (not counting itself)."""
    p = os.path.dirname(os.path.abspath(path).rstrip("\\/"))
    while True:
        if is_library(p):
            return p
        up = os.path.dirname(p)
        if up == p:
            return None
        p = up


def info(root: str) -> dict:
    try:
        with open(os.path.join(root, DATA, INFO), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d = d if isinstance(d, dict) else {}
    d.setdefault("name", os.path.basename(os.path.abspath(root).rstrip("\\/")) or root)
    return d


def update_info(folder: str, /, **changes) -> dict:
    """Change library.json (None removes a key); `root` is one of its keys, so the folder is positional."""
    d = info(folder)
    for k, v in changes.items():
        if v is None:
            d.pop(k, None)
        else:
            d[k] = v
    write_atomic(os.path.join(folder, DATA, INFO), json.dumps(d, indent=1).encode("utf-8"))
    return d


def _hide(path: str) -> None:
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(path, 0x2)  # FILE_ATTRIBUTE_HIDDEN
        except Exception:
            pass


def create(root: str, name: str | None = None, bring_from: dict | None = None) -> dict:
    """Make a folder a library -> {root, name, existing}. A folder that's one already is simply returned."""
    root = os.path.abspath(os.path.expanduser(str(root or "").strip().strip('"')))
    if not str(root).strip() or not os.path.isdir(root):
        raise ValueError("That folder doesn't exist (or isn't reachable right now).")
    if is_library(root):
        return {"root": root, "name": info(root)["name"], "existing": True}
    if os.path.basename(root.rstrip("\\/")).lower() == DATA:
        raise ValueError("That's the Gallery's own data folder: pick the folder of your files.")
    if any(p.lower().endswith(".library") for p in root.replace("\\", "/").split("/")):
        raise ValueError("That's inside an Eagle library, which Eagle manages: pick a folder of your own.")
    outer = enclosing(root)
    if outer:
        raise ValueError(f"That folder is inside the library {outer}: open that one instead.")
    d = data_dir(root)
    os.makedirs(d, exist_ok=True)
    _hide(d)
    meta = {"v": 1, "id": uuid.uuid4().hex, "name": (str(name or "").strip()[:100] or os.path.basename(root.rstrip("\\/")) or root), "created": time.time(), "root": root}
    if bring_from:
        meta["bring_from"] = bring_from
    write_atomic(os.path.join(d, INFO), json.dumps(meta, indent=1).encode("utf-8"))
    return {"root": root, "name": meta["name"], "existing": False}


def rebase(db, root: str) -> int:
    """Re-anchor the folders inside the library to where it is now (another drive letter, another place).
    -> how many folders moved. Only paths change: files, marks and History refer to folders by id."""
    old = info(root).get("root") or root
    moved = 0
    if _norm(old) != _norm(root):
        with db.write() as c:
            rows = c.execute("SELECT id, path FROM folders").fetchall()
            taken = {_norm(f["path"]) for f in rows}
            for f in rows:
                if inside(f["path"], old):
                    rel = os.path.relpath(os.path.abspath(f["path"]), os.path.abspath(old))
                    new = os.path.normpath(root if rel == "." else os.path.join(root, rel))
                    if _norm(new) in taken:
                        continue  # one of its folders already is that path: this one stays where it was (shown as not reachable)
                    taken.discard(_norm(f["path"]))
                    taken.add(_norm(new))
                    c.execute("UPDATE folders SET path=? WHERE id=?", (new, f["id"]))
                    moved += 1
    if info(root).get("root") != root:
        update_info(root, root=root)
    return moved


def prepare(lib) -> None:
    """A library just opened, before it starts: its folders follow it to where it is now, and a new one
    indexes its own folder (once: removing that folder afterwards sticks)."""
    if not lib.root:
        return
    if not info(lib.root).get("id"):  # made by 1.2/1.3: an id, so the recent list knows it wherever it's opened from
        update_info(lib.root, id=uuid.uuid4().hex)
    rebase(lib.db, lib.root)
    if not info(lib.root).get("opened"):
        with lib.db.write() as c:
            if not c.execute("SELECT 1 FROM folders LIMIT 1").fetchone():
                c.execute("INSERT INTO folders (path, added) VALUES (?, ?)", (os.path.normpath(lib.root), time.time()))
        update_info(lib.root, opened=time.time())


# ---------------------------------------------------------------- recent libraries (this PC)
def _recent_path() -> str:
    return os.path.join(home_dir(), "libraries.json")


def _load() -> dict:
    try:
        with open(_recent_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    write_atomic(_recent_path(), json.dumps(d, indent=1).encode("utf-8"))


def _entries(d: dict) -> list[dict]:
    """The recent list as {root, id}: a path alone (1.2/1.3) has no id."""
    out = []
    for r in d.get("recent") or []:
        if isinstance(r, str):
            out.append({"root": r, "id": None})
        elif isinstance(r, dict) and isinstance(r.get("root"), str):
            out.append({"root": r["root"], "id": r["id"] if isinstance(r.get("id"), str) else None})
    return out


def remember(root: str | None) -> None:
    """The library open now (None: this PC's library), first in the recent list and opened next time. The same
    library seen before somewhere it isn't any more (another drive letter, a folder it was moved from) is dropped:
    it's listed once, where it is now. A copy that's still there (a backup, say) stays listed as well."""
    d = _load()
    rec = _entries(d)
    if root:
        lid = info(root).get("id")
        rec = [r for r in rec if _norm(r["root"]) != _norm(root) and not (lid and r["id"] == lid and not is_library(r["root"]))]
        rec.insert(0, {"root": root, "id": lid})
    d.update(last=root, recent=rec[:RECENT])
    _save(d)


def forget(root: str) -> None:
    d = _load()
    d["recent"] = [r for r in _entries(d) if _norm(r["root"]) != _norm(root)]
    if d.get("last") and _norm(d["last"]) == _norm(root):
        d["last"] = None
    _save(d)


def recent() -> list[dict]:
    out = []
    for r in _entries(_load()):
        root, ok = r["root"], is_library(r["root"])
        out.append({"root": root, "name": info(root)["name"] if ok else os.path.basename(root.rstrip("\\/")) or root, "reachable": ok})
    return out


def choose(library: str | None = None, data: str | None = None, app_root: str | None = None, local: bool = False) -> dict:
    """Which library to open -> {data, home, root, note, remember}.

    --library > --data (a data folder of its own, settings in it: the tests, a throwaway) > MLG_DATA > --local >
    the library the portable Gallery sits in > the last library opened (when it's reachable) > this PC's library."""
    if library:
        root = find(library)
        if not root:
            raise ValueError(f"{library} isn't a library: it has no {DATA} folder. Make it one from Settings, New library.")
        return {"data": data_dir(root), "home": home_dir(), "root": root, "note": None, "remember": True}
    explicit = data or os.environ.get("MLG_DATA")
    if explicit:
        return {"data": explicit, "home": explicit, "root": None, "note": None, "remember": False}
    local_lib = {"data": default_data_dir(), "home": home_dir(), "root": None, "note": None, "remember": True}
    if local:
        return local_lib
    if app_root and is_library(os.path.dirname(os.path.abspath(app_root))):
        root = os.path.dirname(os.path.abspath(app_root))
        return {"data": data_dir(root), "home": home_dir(), "root": root, "note": None, "remember": True}
    last = _load().get("last")
    if isinstance(last, str) and last:
        if is_library(last):
            return {"data": data_dir(last), "home": home_dir(), "root": last, "note": None, "remember": True}
        local_lib["note"] = f"The last library ({last}) isn't reachable, so this PC's library is open. Plug in its drive, then open it from Settings."
        local_lib["remember"] = False  # it stays the one to open next time
    return local_lib


class Lock:
    """One Gallery per data folder (a second one would fight the first over the index): a file kept open and
    locked while it runs. The launcher checks the same file before it swaps in an update."""

    def __init__(self, folder: str, name: str = "gallery.lock"):
        self.path = os.path.join(folder, name)
        self.f = None

    def acquire(self) -> bool:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.f = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.f.close()
            self.f = None
            return False

    def release(self) -> None:
        if self.f:
            self.f.close()
            self.f = None


def in_use(data: str) -> bool:
    """Is a Gallery running with this data folder?"""
    if not os.path.isdir(data):
        return False
    lock = Lock(data)
    if lock.acquire():
        lock.release()
        return False
    return True


def launch_command(target: list[str], browser: bool = False) -> tuple[list[str], str]:
    """How to start another Gallery with `target` arguments -> (command, working folder): the portable
    folder's launcher (it also swaps in a waiting update), or this Python with the checkout's mlgallery."""
    import sys
    from . import update
    pkg = update.portable_root()
    if pkg:
        cmd, cwd = [os.path.join(pkg, f"{APP_NAME}.exe"), "--wait", *target], pkg
    else:
        exe = sys.executable
        pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if not browser and os.path.isfile(pyw):
            exe = pyw
        cmd, cwd = [exe, "-m", "mlgallery", *target], os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return cmd + (["--browser"] if browser else []), cwd


# ---------------------------------------------------------------- bringing marks from another library
def _source(path: str) -> sqlite3.Connection:
    if not os.path.isfile(path):
        raise ValueError("The library to bring marks from isn't there any more.")
    import urllib.parse
    p = os.path.abspath(path)
    if p.startswith("\\\\"):  # \\server\share: opened as a plain path, and only asked, never told (query_only)
        c = sqlite3.connect(p)
        c.execute("PRAGMA query_only = 1")
    else:  # read-only; encoded, so a # or space in a folder name is part of the name
        c = sqlite3.connect("file:/" + urllib.parse.quote(p.replace(os.sep, "/"), safe="/:") + "?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def bring_marks(db, source_db: str, name: str) -> dict:
    """Give this library's files the ratings, favourites, tags, albums, characters and notes the same files
    (by fingerprint) have in another library. Yours here win; one History step, undoable (`undo_bring`)."""
    from . import bulk, fileops, metadata
    from . import characters as chars
    src = _source(source_db)
    try:
        by_fp: dict[str, list[int]] = {}
        with db.read() as c:
            for r in c.execute("SELECT id, fp FROM files WHERE missing=0 AND fp IS NOT NULL"):
                by_fp.setdefault(r["fp"], []).append(r["id"])
        pairs = [(r["id"], by_fp[r["fp"]]) for r in src.execute("SELECT id, fp FROM files WHERE missing!=2 AND fp IS NOT NULL") if r["fp"] in by_fp]
        sid_to = dict(pairs)
        fp_of = {r["id"]: r["fp"] for r in src.execute("SELECT id, fp FROM files WHERE fp IS NOT NULL")}
        q = lambda sql, ids: [r for ch in _chunks(list(ids)) for r in src.execute(sql % ",".join("?" * len(ch)), ch)]  # noqa: E731
        s_marks = q("SELECT file_id, rating, favorite, source FROM marks WHERE source != 'workflow' AND file_id IN (%s)", sid_to)
        s_tags = q("SELECT ft.file_id, t.name, ft.source, ft.removed FROM file_tags ft JOIN tags t ON t.id=ft.tag_id"
                   " WHERE (ft.source != 'workflow' OR ft.removed = 1) AND ft.file_id IN (%s)", sid_to)
        s_albums = q("SELECT af.file_id, a.name, a.icon, a.color, a.cover, af.source, af.removed FROM album_files af JOIN albums a ON a.id=af.album_id"
                     " WHERE (af.source != 'workflow' OR af.removed = 1) AND af.file_id IN (%s)", sid_to)
        s_notes = q("SELECT file_id, note, source FROM file_notes WHERE file_id IN (%s)", sid_to)
        s_chars = src.execute("SELECT id, name, aliases, cover, created FROM characters").fetchall()
        s_rules = src.execute("SELECT character_id, kind, value, strength, muted FROM character_rules").fetchall()
        s_decided = q("SELECT file_id, character_id, state, source FROM file_characters WHERE state IN ('confirmed', 'rejected') AND file_id IN (%s)", sid_to)
        s_folders = src.execute("SELECT path, icon, color FROM folders WHERE icon IS NOT NULL OR color IS NOT NULL").fetchall()
    finally:
        src.close()

    def here(sid):
        return sid_to.get(sid) or []

    def cover(sid):
        ids = by_fp.get(fp_of.get(sid)) if sid else None
        return ids[0] if ids else None
    now = time.time()
    rec = {"marks": [], "tags": [], "albums": [], "notes": [], "new_albums": [], "new_characters": [], "decided": [], "folders": []}
    touched = set()
    with db.write() as c:
        for m in s_marks:
            for fid in here(m["file_id"]):
                cur = c.execute("SELECT rating, favorite, source, updated FROM marks WHERE file_id=?", (fid,)).fetchone()
                if cur and cur["source"] == "user":
                    continue  # yours here win
                rec["marks"].append({"file_id": fid, "was": dict(cur) if cur else None})
                c.execute("INSERT OR REPLACE INTO marks (file_id, rating, favorite, source, updated) VALUES (?,?,?,?,?)",
                          (fid, m["rating"], m["favorite"], m["source"], now))
                touched.add(fid)
        for t in s_tags:
            tid = metadata.tag_id(c, t["name"])
            for fid in here(t["file_id"]):
                cur = c.execute("SELECT source, removed FROM file_tags WHERE file_id=? AND tag_id=?", (fid, tid)).fetchone()
                if cur and (cur["source"], cur["removed"]) == (t["source"], t["removed"]):
                    continue
                rec["tags"].append({"file_id": fid, "tag": t["name"], "was": dict(cur) if cur else None})
                c.execute("INSERT OR REPLACE INTO file_tags (file_id, tag_id, source, removed) VALUES (?,?,?,?)", (fid, tid, t["source"], t["removed"]))
                touched.add(fid)
        for a in s_albums:
            if not c.execute("SELECT 1 FROM albums WHERE name=? COLLATE NOCASE", (a["name"],)).fetchone():
                aid = bulk.album_id(c, a["name"])
                c.execute("UPDATE albums SET icon=?, color=?, cover=? WHERE id=?", (a["icon"], a["color"], cover(a["cover"]), aid))
                rec["new_albums"].append(aid)
            aid = bulk.album_id(c, a["name"])
            for fid in here(a["file_id"]):
                cur = c.execute("SELECT source, removed FROM album_files WHERE album_id=? AND file_id=?", (aid, fid)).fetchone()
                if cur and (cur["source"], cur["removed"]) == (a["source"], a["removed"]):
                    continue
                rec["albums"].append({"album": aid, "file_id": fid, "was": dict(cur) if cur else None})
                c.execute("INSERT INTO album_files (album_id, file_id, source, removed, added) VALUES (?,?,?,?,?)"
                          " ON CONFLICT (album_id, file_id) DO UPDATE SET source=excluded.source, removed=excluded.removed", (aid, fid, a["source"], a["removed"], now))
                touched.add(fid)
        for n in s_notes:
            for fid in here(n["file_id"]):
                cur = c.execute("SELECT note, source FROM file_notes WHERE file_id=?", (fid,)).fetchone()
                if cur and cur["source"] == "user":
                    continue
                rec["notes"].append({"file_id": fid, "was": dict(cur) if cur else None})
                c.execute("INSERT OR REPLACE INTO file_notes (file_id, note, source) VALUES (?,?,?)", (fid, n["note"], n["source"]))
        cid_of = {}
        for ch in s_chars:
            have = c.execute("SELECT id FROM characters WHERE name=? COLLATE NOCASE", (ch["name"],)).fetchone()
            if have:
                cid_of[ch["id"]] = have[0]  # a character of the same name here: its own rules stay
                continue
            cid = c.execute("INSERT INTO characters (name, aliases, cover, created) VALUES (?,?,?,?)",
                            (ch["name"], ch["aliases"], cover(ch["cover"]), ch["created"] or now)).lastrowid
            cid_of[ch["id"]] = cid
            rec["new_characters"].append(cid)
            for r in s_rules:
                if r["character_id"] == ch["id"]:
                    c.execute("INSERT INTO character_rules (character_id, kind, value, strength, muted) VALUES (?,?,?,?,?)",
                              (cid, r["kind"], r["value"], r["strength"], r["muted"]))
        for d in s_decided:
            cid = cid_of.get(d["character_id"])
            for fid in here(d["file_id"]):
                cur = c.execute("SELECT state, source, at FROM file_characters WHERE file_id=? AND character_id=?", (fid, cid)).fetchone()
                if cur and cur["state"] in ("confirmed", "rejected"):
                    continue  # yours here win
                rec["decided"].append({"file_id": fid, "character": cid, "was": dict(cur) if cur else None})
                c.execute("INSERT OR REPLACE INTO file_characters (file_id, character_id, state, source, at) VALUES (?,?,?,?,?)",
                          (fid, cid, d["state"], d["source"], now))
        for cid in rec["new_characters"]:
            chars.rematch(c, cid)  # files read before the rules arrived
        looks = {_norm(f["path"]): f for f in s_folders}
        for f in c.execute("SELECT id, path, icon, color FROM folders WHERE icon IS NULL AND color IS NULL").fetchall():
            src_f = looks.get(_norm(f["path"]))
            if src_f:
                rec["folders"].append(f["id"])
                c.execute("UPDATE folders SET icon=?, color=? WHERE id=?", (src_f["icon"], src_f["color"], f["id"]))
        for fid in touched:
            metadata.refresh_search(c, fid)
        files = len({fid for _, ids in pairs for fid in ids})
        changed = sum(len(rec[k]) for k in ("marks", "tags", "albums", "notes", "decided", "new_characters", "new_albums", "folders"))
        op = fileops.record(c, "carry", f"Brought ratings, tags, albums, characters and notes from {name} ({files:,} file{'s' if files != 1 else ''} matched)",
                            rec) if changed else None
    return {"op": op, "files": files, "ratings": len(rec["marks"]), "tags": len(rec["tags"]), "albums": len(rec["albums"]),
            "notes": len(rec["notes"]), "characters": len(rec["new_characters"]), "decisions": len(rec["decided"])}


def undo_bring(db, data: dict) -> dict:
    """Each list backwards, back to how this library was before."""
    from . import metadata
    with db.write() as c:
        ids = {i["file_id"] for k in ("marks", "tags", "albums", "notes", "decided") for i in data[k]}
        alive = {r[0] for ch in _chunks(list(ids)) for r in c.execute(f"SELECT id FROM files WHERE id IN ({','.join('?' * len(ch))})", ch)}
        data = {**data, **{k: [i for i in data[k] if i["file_id"] in alive] for k in ("marks", "tags", "albums", "notes", "decided")}}
        for it in reversed(data["decided"]):
            if it["was"] is None:
                c.execute("DELETE FROM file_characters WHERE file_id=? AND character_id=?", (it["file_id"], it["character"]))
            else:
                w = it["was"]
                c.execute("INSERT OR REPLACE INTO file_characters (file_id, character_id, state, source, at) VALUES (?,?,?,?,?)",
                          (it["file_id"], it["character"], w["state"], w["source"], w["at"]))
        for cid in data["new_characters"]:
            c.execute("DELETE FROM characters WHERE id=?", (cid,))  # its rules and matches go with it
        for it in reversed(data["notes"]):
            if it["was"] is None:
                c.execute("DELETE FROM file_notes WHERE file_id=?", (it["file_id"],))
            else:
                c.execute("INSERT OR REPLACE INTO file_notes (file_id, note, source) VALUES (?,?,?)", (it["file_id"], it["was"]["note"], it["was"]["source"]))
        for it in reversed(data["albums"]):
            if it["was"] is None:
                c.execute("DELETE FROM album_files WHERE album_id=? AND file_id=?", (it["album"], it["file_id"]))
            else:
                c.execute("UPDATE album_files SET source=?, removed=? WHERE album_id=? AND file_id=?",
                          (it["was"]["source"], it["was"]["removed"], it["album"], it["file_id"]))
        for aid in data["new_albums"]:
            c.execute("DELETE FROM albums WHERE id=? AND id NOT IN (SELECT album_id FROM album_files)", (aid,))
        for it in reversed(data["tags"]):
            tid = metadata.tag_id(c, it["tag"])
            if it["was"] is None:
                c.execute("DELETE FROM file_tags WHERE file_id=? AND tag_id=?", (it["file_id"], tid))
            else:
                c.execute("INSERT OR REPLACE INTO file_tags (file_id, tag_id, source, removed) VALUES (?,?,?,?)",
                          (it["file_id"], tid, it["was"]["source"], it["was"]["removed"]))
        c.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM file_tags)")
        for it in reversed(data["marks"]):
            if it["was"] is None:
                c.execute("DELETE FROM marks WHERE file_id=?", (it["file_id"],))
            else:
                w = it["was"]
                c.execute("INSERT OR REPLACE INTO marks (file_id, rating, favorite, source, updated) VALUES (?,?,?,?,?)",
                          (it["file_id"], w["rating"], w["favorite"], w["source"], w.get("updated") or time.time()))
        for fid in data["folders"]:
            c.execute("UPDATE folders SET icon=NULL, color=NULL WHERE id=?", (fid,))
        for fid in {i["file_id"] for k in ("marks", "tags", "albums", "notes") for i in data[k]}:
            metadata.refresh_search(c, fid)
    return {"undone": sum(len(data[k]) for k in ("marks", "tags", "albums", "notes", "decided", "new_characters"))}


def _chunks(ids: list, size: int = 900):
    for i in range(0, len(ids), size):
        yield ids[i:i + size]


def bring_when_scanned(lib, root: str) -> None:
    """In the background on a new library's first start: once its folders are indexed, bring the marks
    library.json asks for (`bring_from`), and note what came in (`brought`)."""
    src = info(root).get("bring_from")
    if not isinstance(src, dict) or not src.get("data"):
        return
    while not lib._stop.is_set():
        with lib.db.read() as c:
            waiting = c.execute("SELECT COUNT(*) FROM folders WHERE last_scan IS NULL AND online=1").fetchone()[0]
        if not waiting and not lib.scanning and not lib._queue:
            break
        time.sleep(0.5)
    else:
        return
    lib.bringing = src.get("name") or "another library"
    try:
        r = bring_marks(lib.db, os.path.join(src["data"], "library.db"), lib.bringing)
        update_info(root, bring_from=None, brought=dict(r, name=lib.bringing, at=time.time()))
    except (ValueError, sqlite3.Error) as ex:
        update_info(root, bring_from=None, brought={"name": lib.bringing, "error": str(ex)[:300], "at": time.time()})
    finally:
        lib.bringing = None
