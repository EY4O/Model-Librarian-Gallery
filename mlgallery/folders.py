"""The folders inside your library folders, from the sidebar: made, renamed, moved and deleted, and files dropped from
Explorer copied into them. Each is a History step, undoable like the file operations (fileops.py).

* New folder: made on disk at once, and listed in the sidebar while it's empty (the dirs table).
* Rename and move: the folder on disk, then every file under it in the index (those in the Trash and missing ones too,
  so they go back where it is now), so nothing loses its ratings, tags, albums or characters. A move stays on one
  drive: a folder of thousands copied across drives is a job for Explorer (or Move..., for its pictures).
* Delete: its files go to the Gallery's Trash (fileops.trash), then the folders left empty are removed. Files the
  Gallery doesn't show (a .txt, a .zip) stay where they are, and so does the folder that holds them.
* Copy in: a file dropped from Explorer is written where it goes as name.mlg-part, then given its name (or a free one,
  "name (2)"), with its date modified. Undo sends the copies to the Trash.

The top-level library folders themselves are only ever made into, never renamed, moved or deleted from here.
"""
from __future__ import annotations

import json
import os

from . import fileops, scan, watch
from .fileops import OpError

BAD = set('<>:"/\\|?*')
RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def check_name(name, what: str = "folder") -> str:
    """A name Windows takes as it is (and the Gallery doesn't leave out), or OpError saying what's wrong with it."""
    n = str(name or "").strip().rstrip(". ")  # (Windows drops trailing dots and spaces)
    if not n:
        raise OpError(f"Give the {what} a name.")
    if any(ch in BAD or ord(ch) < 32 for ch in n):
        raise OpError(f'A {what} name can\'t have any of \\ / : * ? " < > | in it.')
    if what == "folder" and n.startswith((".", "$")):
        raise OpError("A folder name can't start with a dot or a $: the Gallery leaves those folders out.")
    if n.split(".")[0].casefold() in RESERVED:
        raise OpError(f"Windows keeps the name {n} for itself.")
    if len(n) > 200:
        raise OpError("That name is too long.")
    return n


def _folder(c, fid) -> dict:
    d = c.execute("SELECT id, path, online FROM folders WHERE id=?", (int(fid),)).fetchone()
    if not d:
        raise OpError("That folder isn't in the library.")
    if not d["online"] or not os.path.isdir(d["path"]):
        raise OpError("That folder isn't reachable right now (a drive that isn't plugged in?).")
    return {"id": d["id"], "path": d["path"]}


def _sub(root: str, rel: str) -> str:
    """rel, cleaned and with the case the disk has."""
    return fileops._real_sub(root, fileops._clean_sub(rel))


def _existing(d: dict, rel: str) -> str:
    """A folder inside library folder d that's there (not the library folder itself)."""
    rel = _sub(d["path"], rel)
    if not rel:
        raise OpError("That's the library folder itself: it's renamed, moved and deleted in Explorer.")
    if not os.path.isdir(fileops._full(d["path"], rel)):
        raise OpError("That folder isn't there any more.")
    return rel


def _like(rel: str) -> str:
    """A LIKE pattern for everything under rel/."""
    return rel.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"


def _join(parent: str, name: str) -> str:
    return f"{parent}/{name}" if parent else name


def _leaf(rel: str) -> str:
    return rel.rsplit("/", 1)[-1]


def _parent(rel: str) -> str:
    return rel.rsplit("/", 1)[0] if "/" in rel else ""


def _taken(folder: str, name: str) -> str | None:
    """The name of what's called name in folder, as the disk has it (Windows doesn't mind the case), or None."""
    try:
        return next((n for n in os.listdir(folder) if n.casefold() == name.casefold()), None)
    except OSError:
        return None


def _done(db, c, folders) -> None:
    scan.pair_live(c, folders)
    fileops._recount(c)


# ---------------------------------------------------------------- new folder
def make(db, fid: int, parent: str, name: str) -> dict:
    """A new, empty folder in library folder fid (inside parent, "" for its top). -> {op, rel}"""
    name = check_name(name)
    with db.read() as c:
        d = _folder(c, fid)
    parent = _sub(d["path"], parent)
    if parent and not os.path.isdir(fileops._full(d["path"], parent)):
        raise OpError("The folder to make it in isn't there any more.")
    rel = _join(parent, name)
    full = fileops._full(d["path"], rel)
    if os.path.exists(full):
        raise OpError(f"There's already a folder called {_taken(os.path.dirname(full), name) or name} there.")
    try:
        os.mkdir(full)
    except OSError as ex:
        raise OpError(f"Windows wouldn't make it: {ex.strerror or ex}")
    with db.write() as c:
        c.execute("INSERT OR IGNORE INTO dirs (folder_id, rel) VALUES (?, ?)", (d["id"], rel))
        op = fileops.record(c, "dir_make", f"Made the folder {name}", {"folder": d["id"], "rel": rel})
    return {"op": op, "rel": rel}


# ---------------------------------------------------------------- rename and move
def rename(db, fid: int, rel: str, name: str) -> dict:
    """A folder inside library folder fid, renamed. -> {op, rel}"""
    name = check_name(name)
    with db.read() as c:
        d = _folder(c, fid)
    rel = _existing(d, rel)
    if name == _leaf(rel):
        raise OpError("That's its name already.")
    return _relocate(db, d, rel, d, _join(_parent(rel), name), "rename")


def move(db, fid: int, rel: str, to_fid: int, to_parent: str) -> dict:
    """A folder inside library folder fid, with everything in it, into another folder (to_parent of library folder
    to_fid, "" for its top). -> {op, rel}"""
    with db.read() as c:
        d, t = _folder(c, fid), _folder(c, to_fid)
    rel = _existing(d, rel)
    to_parent = _sub(t["path"], to_parent)
    if to_parent and not os.path.isdir(fileops._full(t["path"], to_parent)):
        raise OpError("The folder to put it in isn't there any more.")
    if d["id"] == t["id"]:
        if to_parent.casefold() == rel.casefold() or to_parent.casefold().startswith(rel.casefold() + "/"):
            raise OpError("A folder can't go inside itself.")
        if to_parent.casefold() == _parent(rel).casefold():
            raise OpError("It's there already.")
    if not fileops._same_drive(fileops._full(d["path"], rel), fileops._full(t["path"], to_parent)):
        raise OpError("A folder moves on the same drive only. To take its pictures to another drive, select them and use Move….")
    return _relocate(db, d, rel, t, _join(to_parent, _leaf(rel)), "move")


def _relocate(db, d: dict, rel: str, t: dict, to: str, how: str, *, record: bool = True) -> dict:
    """Folder d/rel to t/to on disk, then the index after it."""
    src, dst = fileops._full(d["path"], rel), fileops._full(t["path"], to)
    case_only = d["id"] == t["id"] and rel.casefold() == to.casefold()
    if os.path.exists(dst) and not case_only:
        raise OpError(f"There's already a folder called {_taken(os.path.dirname(dst), _leaf(to)) or _leaf(to)} there.")
    with db.read() as c:
        if not case_only and c.execute("SELECT 1 FROM files WHERE folder_id=? AND rel LIKE ? ESCAPE '\\' LIMIT 1", (t["id"], _like(to))).fetchone():
            raise OpError(f"The library still has files it remembers in a folder called {_leaf(to)} there (missing, or in the Trash): pick another name.")
    watch.expect([src])
    try:
        os.rename(src, dst)
    except OSError as ex:
        raise OpError(f"Windows wouldn't {'rename' if how == 'rename' else 'move'} it (is a file in it open in another program?): {ex.strerror or ex}")
    try:
        _shift(db, d["id"], rel, t["id"], to)
    except Exception:
        os.rename(dst, src)  # the index wouldn't follow: the folder goes back, so the two agree
        raise
    fileops._note_empty(db, {(d["id"], _parent(rel))})  # (the folder it was in, if that's empty now)
    op = None
    if record:
        what = (f"Renamed the folder {_leaf(rel)} to {_leaf(to)}" if how == "rename"
                else f"Moved the folder {_leaf(rel)} into {_leaf(_parent(to)) if _parent(to) else os.path.basename(t['path'])}")
        with db.write() as c:
            op = fileops.record(c, "dir_move", what, {"from_folder": d["id"], "from_rel": rel, "to_folder": t["id"], "to_rel": to})
    db.user_change()
    return {"op": op, "rel": to}


def _shift(db, fid: int, rel: str, to_fid: int, to: str) -> None:
    """The index follows a folder: every file (and empty folder) under fid/rel to to_fid/to."""
    n = len(rel) + 1  # (substr from here: "/" and what's after it)
    with db.write() as c:
        recredit = ", credited=0"  # (its artist is looked for again: a folder's name may say who made it)
        c.execute(f"UPDATE files SET folder_id=?, rel=? || substr(rel, ?){recredit} WHERE folder_id=? AND rel LIKE ? ESCAPE '\\'",
                  (to_fid, to, n, fid, _like(rel)))
        moved = [r[0] for r in c.execute("SELECT rel FROM dirs WHERE folder_id=? AND (rel=? COLLATE NOCASE OR rel LIKE ? ESCAPE '\\')", (fid, rel, _like(rel)))]
        c.executemany("DELETE FROM dirs WHERE folder_id=? AND rel=?", [(fid, r) for r in moved])
        c.executemany("INSERT OR IGNORE INTO dirs (folder_id, rel) VALUES (?, ?)", [(to_fid, to + r[len(rel):]) for r in moved])
        _done(db, c, {fid, to_fid})


# ---------------------------------------------------------------- delete
def delete(db, fid: int, rel: str) -> dict:
    """A folder inside library folder fid: its files to the Trash, then the folders left empty removed.
    -> {op, trashed, failed, kept (files left: ones the Gallery doesn't show, or that couldn't go), gone (the folder is)}"""
    with db.read() as c:
        d = _folder(c, fid)
        rel = _existing(d, rel)
        ids = [r[0] for r in c.execute(f"SELECT id FROM files WHERE folder_id=? AND rel LIKE ? ESCAPE '\\' AND missing IN (0, {scan.PAIRED})",
                                       (d["id"], _like(rel)))]
    t = fileops.trash(db, ids) if ids else {"op": None, "trashed": 0, "failed": []}
    items = []
    if t["op"]:  # (one History step for it all: the Trash's own goes into this one)
        with db.write() as c:
            items = json.loads(c.execute("SELECT data FROM ops WHERE id=?", (t["op"],)).fetchone()[0])
            c.execute("DELETE FROM ops WHERE id=?", (t["op"],))
    full = fileops._full(d["path"], rel)
    watch.expect([full])
    removed, kept = [], 0
    for at, _dirs, files in os.walk(full, topdown=False):  # the deepest first
        kept += len(files)
        try:
            os.rmdir(at)
            removed.append(os.path.relpath(at, d["path"]).replace("\\", "/"))
        except OSError:
            pass
    with db.write() as c:
        c.executemany("DELETE FROM dirs WHERE folder_id=? AND rel=?", [(d["id"], r) for r in removed])
    fileops._note_empty(db, {(d["id"], _parent(rel))})  # (the folder it was in, if that's empty now)
    with db.write() as c:
        op = None
        if items or removed:
            n = t["trashed"]
            what = f"Deleted the folder {_leaf(rel)}" + (f" ({n:,} file{'s' if n != 1 else ''} to the Trash)" if n else "")
            op = fileops.record(c, "dir_delete", what, {"folder": d["id"], "rel": rel, "files": items, "dirs": removed})
    db.user_change()
    return {"op": op, "trashed": t["trashed"], "failed": t["failed"], "kept": kept, "gone": not os.path.exists(full)}


# ---------------------------------------------------------------- copy in (dropped from Explorer)
def copy_dir(db, fid: int, parent: str, name: str, fresh: bool) -> dict:
    """A folder for a dropped folder's files: fresh, a new one ("name (2)" if name is taken), else name as it is (made
    if it isn't there). -> {rel, made}"""
    name = check_name(name)
    with db.read() as c:
        d = _folder(c, fid)
    parent = _sub(d["path"], parent)
    base = fileops._full(d["path"], parent) if parent else d["path"]
    if not os.path.isdir(base):
        raise OpError("The folder to copy into isn't there any more.")
    if fresh:
        stem, k = name, 1
        while os.path.exists(os.path.join(base, name)):
            k += 1
            name = f"{stem} ({k})"
    rel = _join(parent, name)
    full = fileops._full(d["path"], rel)
    made = not os.path.isdir(full)
    if made:
        try:
            os.mkdir(full)
        except OSError as ex:
            raise OpError(f"Windows wouldn't make the folder {name}: {ex.strerror or ex}")
        with db.write() as c:
            c.execute("INSERT OR IGNORE INTO dirs (folder_id, rel) VALUES (?, ?)", (d["id"], rel))
    return {"rel": rel, "made": made}


def copy_target(db, fid: int, sub: str, name: str) -> dict:
    """Where a dropped file is written while it comes: {folder, sub, name, dir, tmp}."""
    name = check_name(os.path.basename(str(name or "").replace("\\", "/")), "file")
    if not scan.kind_of(name):
        raise OpError(f"{name}: only pictures and videos are copied in.")
    with db.read() as c:
        d = _folder(c, fid)
    sub = _sub(d["path"], sub)
    folder = fileops._full(d["path"], sub) if sub else d["path"]
    if not os.path.isdir(folder):
        raise OpError("The folder to copy into isn't there any more.")
    k = 0
    while True:  # (two files of one name coming at once each get their own)
        tmp = os.path.join(folder, f"{name}.{k}.mlg-part" if k else f"{name}.mlg-part")
        try:
            open(tmp, "xb").close()
            break
        except FileExistsError:
            k += 1
    return {"folder": d, "sub": sub, "name": name, "dir": folder, "tmp": tmp}


def copy_finish(db, target: dict, mtime, skip_eagle: bool = True) -> dict:
    """A dropped file that has all come: under its name (or a free one) there, its date modified kept, and in the index.
    -> {id, rel, name}"""
    d, sub, folder = target["folder"], target["sub"], target["dir"]
    try:
        when = float(mtime) if mtime not in (None, "") else None
    except ValueError:
        when = None
    for _ in range(20):
        with db.read() as c:
            name = fileops._free_name(folder, target["name"], set(), fileops._held(c, d["id"], sub))
        dst = os.path.join(folder, name)
        try:
            os.rename(target["tmp"], dst)  # (never over a file: Windows refuses if one took the name just now)
            break
        except FileExistsError:
            continue
    else:
        raise OpError(f"{target['name']}: no free name for it there.")
    if when:
        try:
            os.utime(dst, (when, when))
        except (OSError, OverflowError, ValueError):
            pass
    rel = _join(sub, name)
    scan.index_files(db, d, [rel], skip_eagle=skip_eagle)
    with db.read() as c:
        r = c.execute("SELECT id FROM files WHERE folder_id=? AND rel=?", (d["id"], rel)).fetchone()
    return {"id": r[0] if r else None, "rel": rel, "name": name}


def copy_done(db, fid: int, ids: list, dirs: list, into: str) -> dict:
    """The files of one drop, copied in: a History step, whose Undo sends them to the Trash. -> {op}"""
    ids = [int(i) for i in ids or [] if i is not None]
    dirs = [str(r) for r in dirs or []]
    if not ids and not dirs:
        return {"op": None}
    with db.write() as c:
        what = f"Copied {len(ids):,} file{'s' if len(ids) != 1 else ''} into {into or 'the folder'}"
        return {"op": fileops.record(c, "copy_in", what, {"folder": int(fid), "ids": ids, "dirs": dirs})}


# ---------------------------------------------------------------- undo
def undo(db, kind: str, data) -> dict:
    """Undo one of the steps above (fileops.undo hands them here). -> {undone, failed}"""
    with db.read() as c:
        roots = {r["id"]: r["path"] for r in c.execute("SELECT id, path FROM folders")}
    if kind == "dir_make":
        root = roots.get(data["folder"])
        full = fileops._full(root, data["rel"]) if root else None
        if full and os.path.isdir(full):
            if not scan.is_empty_dir(full):
                raise OpError(f"{_leaf(data['rel'])} has something in it now: delete it from the sidebar instead.")
            os.rmdir(full)
        with db.write() as c:
            c.execute("DELETE FROM dirs WHERE folder_id=? AND rel=?", (data["folder"], data["rel"]))
        return {"undone": 1, "failed": []}
    if kind == "dir_move":
        if data["from_folder"] not in roots or data["to_folder"] not in roots:
            raise OpError("That library folder isn't in the library any more.")
        d, t = {"id": data["from_folder"], "path": roots[data["from_folder"]]}, {"id": data["to_folder"], "path": roots[data["to_folder"]]}
        if not os.path.isdir(fileops._full(t["path"], data["to_rel"])):
            raise OpError(f"{_leaf(data['to_rel'])} isn't where it was put any more (moved or renamed again since?).")
        parent = _parent(data["from_rel"])
        if parent and not os.path.isdir(fileops._full(d["path"], parent)):
            raise OpError(f"The folder it was in ({_leaf(parent)}) isn't there any more.")
        _relocate(db, t, data["to_rel"], d, data["from_rel"], "move", record=False)
        return {"undone": 1, "failed": []}
    if kind == "dir_delete":
        root = roots.get(data["folder"])
        if root:
            for r in sorted(data.get("dirs") or [], key=len):  # the folders first, as they were (the empty ones too)
                os.makedirs(fileops._full(root, r), exist_ok=True)
            with db.write() as c:
                c.executemany("INSERT OR IGNORE INTO dirs (folder_id, rel) VALUES (?, ?)",
                              [(data["folder"], r) for r in data.get("dirs") or [] if scan.is_empty_dir(fileops._full(root, r))])
        ids = [it["id"] for it in data.get("files") or []]
        with db.read() as c:
            states = {r[0]: r[1] for r in c.execute(f"SELECT id, missing FROM files WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
        r = fileops.restore(db, [i for i in ids if states.get(i) == 2], as_op=False) if ids else {"restored": 0, "failed": []}
        back = sum(1 for i in ids if states.get(i) in fileops.PRESENT)
        return {"undone": r["restored"] + back + (0 if ids else 1), "failed": [f["id"] for f in r["failed"]]}
    if kind == "copy_in":
        ids = data.get("ids") or []
        with db.read() as c:
            here = [r[0] for r in c.execute(f"SELECT id FROM files WHERE id IN ({','.join('?' * len(ids))}) AND missing IN (0, {scan.PAIRED})", ids)] if ids else []
        t = fileops.trash(db, here) if here else {"op": None, "trashed": 0, "failed": []}
        if t["op"]:
            with db.write() as c:
                c.execute("DELETE FROM ops WHERE id=?", (t["op"],))  # (the undo itself isn't a new step)
        root = roots.get(data["folder"])
        if root:
            for r in sorted(data.get("dirs") or [], key=len, reverse=True):  # the folders made for it, if they're empty now
                full = fileops._full(root, r)
                if scan.is_empty_dir(full):
                    watch.expect([full])
                    try:
                        os.rmdir(full)
                    except OSError:
                        continue
                    with db.write() as c:
                        c.execute("DELETE FROM dirs WHERE folder_id=? AND rel=?", (data["folder"], r))
        return {"undone": t["trashed"] + (0 if ids else 1), "failed": [f["id"] for f in t["failed"]]}
    raise OpError("That can't be undone here.")
