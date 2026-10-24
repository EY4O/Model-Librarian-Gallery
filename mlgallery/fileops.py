"""Moving and deleting files, each shown first and undoable.

* Move: into one of your library folders (or a folder inside one), so the file keeps its ratings, tags,
  albums and characters. On the same drive it's a rename; across drives it's copied, the copy checked
  (size and fingerprint), and only then the original removed. A name already taken gets " (2)" and so on.
* Delete: to the Gallery's Trash - a hidden `.mlg-trash` folder in the file's own library folder (the same
  drive, so it's instant). Restore puts it back where it was. Emptying the Trash sends the files to the
  Windows Recycle Bin, so even then they can be recovered.
* Every operation is written to `ops` first, with what's needed to undo it.

A file is in the Trash when files.missing = 2 (so every view that lists files with missing = 0 leaves it out).
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time

from . import scan

TRASH = ".mlg-trash"
MAX_ITEMS = 20000


class OpError(ValueError):
    pass


def _row(c, fid: int):
    return c.execute("SELECT f.id, f.folder_id, f.rel, f.name, f.size, f.fp, f.missing, f.trash_rel, d.path AS root, d.online"
                     " FROM files f JOIN folders d ON d.id=f.folder_id WHERE f.id=?", (fid,)).fetchone()


def _full(root: str, rel: str) -> str:
    return os.path.join(root, *rel.split("/"))


def _free_name(dest_dir: str, name: str, taken: set[str], held=None) -> str:
    """name, or "name (2).ext" ... if that's already there (on disk, planned, or held by a file the index has: held(name))."""
    stem, ext = os.path.splitext(name)
    cand, n = name, 1
    while (os.path.exists(os.path.join(dest_dir, cand)) or os.path.normcase(os.path.join(dest_dir, cand)) in taken
           or (held is not None and held(cand))):
        n += 1
        cand = f"{stem} ({n}){ext}"
    taken.add(os.path.normcase(os.path.join(dest_dir, cand)))
    return cand


def _held(c, folder_id: int, sub: str):
    """held(name): is sub/name a file the index already has in that folder (in the Trash or missing included)?"""
    def held(name: str) -> bool:
        rel = f"{sub}/{name}" if sub else name
        return c.execute("SELECT 1 FROM files WHERE folder_id=? AND rel=? COLLATE NOCASE", (folder_id, rel)).fetchone() is not None
    return held


def _real_sub(root: str, sub: str) -> str:
    """sub with the case of the folders already on disk: Windows doesn't care, but the index (and a rescan) does."""
    out, cur = [], root
    for p in sub.split("/") if sub else []:
        try:
            p = next((n for n in os.listdir(cur) if n.casefold() == p.casefold() and os.path.isdir(os.path.join(cur, n))), p)
        except OSError:
            pass
        out.append(p)
        cur = os.path.join(cur, p)
    return "/".join(out)


def _same_drive(a: str, b: str) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return os.path.splitdrive(os.path.abspath(a))[0].lower() == os.path.splitdrive(os.path.abspath(b))[0].lower()


def _move_file(src: str, dst: str) -> None:
    """Rename on one drive; elsewhere copy, check, then remove the original. Never overwrites."""
    if os.path.exists(dst):
        raise OpError(f"{dst} is already there")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if _same_drive(src, os.path.dirname(dst)):
        os.rename(src, dst)
        return
    tmp = dst + ".mlg-part"
    shutil.copy2(src, tmp)
    size = os.path.getsize(src)
    if os.path.getsize(tmp) != size or scan.fingerprint(tmp, size) != scan.fingerprint(src, size):
        os.remove(tmp)
        raise OpError(f"the copy of {os.path.basename(src)} didn't match: the original was left where it is")
    os.replace(tmp, dst)
    try:
        os.remove(src)
    except OSError:
        os.remove(dst)  # the original couldn't go (in use?): don't leave two of it
        raise


def _relocate(db, fid: int, folder: int, rel: str) -> bool:
    """The index follows a file to folder/rel; False (and nothing changed) if another record holds that name."""
    from .metadata import refresh_search
    name = rel.split("/")[-1]
    try:
        with db.write() as c:
            c.execute("UPDATE files SET folder_id=?, rel=?, name=?, ext=? WHERE id=?", (folder, rel, name, os.path.splitext(name)[1].lower(), fid))
            refresh_search(c, fid)
    except sqlite3.IntegrityError:
        return False
    return True


def record(c, kind: str, summary: str, data) -> int:
    return c.execute("INSERT INTO ops (at, kind, summary, data) VALUES (?,?,?,?)", (time.time(), kind, summary, json.dumps(data))).lastrowid


# ---------------------------------------------------------------- move
def plan_move(db, ids: list[int], dest_folder: int, sub: str = "") -> dict:
    """What a move would do, before anything happens."""
    sub = _clean_sub(sub)
    with db.read() as c:
        d = c.execute("SELECT id, path, online FROM folders WHERE id=?", (int(dest_folder),)).fetchone()
        if not d:
            raise OpError("That folder isn't in the library.")
        if not d["online"] or not os.path.isdir(d["path"]):
            raise OpError("That folder isn't reachable right now (a drive that isn't plugged in?).")
        sub = _real_sub(d["path"], sub)
        dest_dir = os.path.join(d["path"], *sub.split("/")) if sub else d["path"]
        rows = [r for r in (_row(c, int(i)) for i in ids[:MAX_ITEMS]) if r]
        out = {"count": 0, "size": 0, "renamed": 0, "already": 0, "unavailable": 0, "copies": 0, "dest": dest_dir, "items": []}
        taken: set[str] = set()
        held = _held(c, d["id"], sub)
        for r in rows:
            src = _full(r["root"], r["rel"])
            if r["missing"] or not r["online"] or not os.path.isfile(src):
                out["unavailable"] += 1
                continue
            if os.path.normcase(os.path.dirname(src)) == os.path.normcase(dest_dir):
                out["already"] += 1
                continue
            name = _free_name(dest_dir, r["name"], taken, held)
            out["renamed"] += name != r["name"]
            out["copies"] += not _same_drive(src, d["path"])
            out["count"] += 1
            out["size"] += r["size"] or 0
            rel = (f"{sub}/{name}" if sub else name)
            out["items"].append({"id": r["id"], "from_folder": r["folder_id"], "from_rel": r["rel"], "to_folder": d["id"], "to_rel": rel})
    return out


def _clean_sub(sub: str) -> str:
    parts = [p.strip() for p in str(sub or "").replace("\\", "/").split("/")]
    for p in parts:
        if p in ("..",) or ":" in p or p.startswith("."):
            raise OpError("A folder name can't go up (..), name a drive, or start with a dot.")
    # Windows drops trailing dots and spaces from folder names: the index must store what the disk has
    return "/".join(q for q in (p.rstrip(". ") for p in parts) if q)


def move(db, ids, dest_folder: int, sub: str = "") -> dict:
    plan = plan_move(db, ids, dest_folder, sub)
    if not plan["count"]:
        raise OpError("Nothing to move: those files are already there, or not reachable.")
    done, failed = [], []
    with db.read() as c:
        roots = {r["id"]: r["path"] for r in c.execute("SELECT id, path FROM folders")}
    for it in plan["items"]:
        src, dst = _full(roots[it["from_folder"]], it["from_rel"]), _full(roots[it["to_folder"]], it["to_rel"])
        try:
            _move_file(src, dst)
        except (OSError, OpError) as ex:
            failed.append({"id": it["id"], "error": str(ex)})
            continue
        if not _relocate(db, it["id"], it["to_folder"], it["to_rel"]):
            _move_file(dst, src)  # the index has that name already (it changed meanwhile): put the file back
            failed.append({"id": it["id"], "error": "that name was taken meanwhile"})
            continue
        done.append(it)
    op = None
    if done:
        with db.write() as c:
            op = record(c, "move", f"Moved {len(done)} file{'s' if len(done) != 1 else ''} to {os.path.basename(plan['dest']) or plan['dest']}", done)
            _recount(c)
    return {"op": op, "moved": len(done), "failed": failed}


# ---------------------------------------------------------------- the Trash
def trash(db, ids) -> dict:
    """Into the Gallery's Trash (the folder's .mlg-trash, same drive: instant)."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    done, failed = [], []
    with db.read() as c:
        rows = [r for r in (_row(c, int(i)) for i in list(ids)[:MAX_ITEMS]) if r]
    for r in rows:
        src = _full(r["root"], r["rel"])
        if r["missing"] or not r["online"] or not os.path.isfile(src):
            failed.append({"id": r["id"], "error": "not reachable"})
            continue
        trash_rel = f"{TRASH}/{stamp}/{r['rel']}"
        dst = _full(r["root"], trash_rel)
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.rename(src, dst)  # inside its own folder: always the same drive
        except OSError as ex:
            failed.append({"id": r["id"], "error": str(ex)})
            continue
        done.append({"id": r["id"], "folder": r["folder_id"], "rel": r["rel"], "trash_rel": trash_rel})
        if len(done) % 200 == 0:  # the index in batches: one write per file made thousands slow
            _mark_trashed(db, done[-200:])
    _mark_trashed(db, done[len(done) - len(done) % 200:])
    op = None
    if done:
        with db.write() as c:
            op = record(c, "trash", f"Moved {len(done)} file{'s' if len(done) != 1 else ''} to the Trash", done)
            _recount(c)
    return {"op": op, "trashed": len(done), "failed": failed}


def _mark_trashed(db, items: list[dict]) -> None:
    if items:
        with db.write() as c:
            c.executemany("UPDATE files SET missing=2, trashed=?, trash_rel=? WHERE id=?", [(time.time(), it["trash_rel"], it["id"]) for it in items])


def _mark_restored(db, items: list[dict]) -> None:
    from .metadata import refresh_search
    if items:
        with db.write() as c:
            # a thumbnail that failed while it was away gets another go
            c.executemany("UPDATE files SET missing=0, trashed=NULL, trash_rel=NULL, rel=?, name=?, thumb=CASE WHEN thumb=-1 THEN 0 ELSE thumb END WHERE id=?",
                          [(it["rel"], it["rel"].split("/")[-1], it["id"]) for it in items])
            for it in items:
                refresh_search(c, it["id"])


def restore(db, ids, *, as_op: bool = True) -> dict:
    """Back where they were (or beside it, " (2)", if something has taken the name meanwhile)."""
    done, failed = [], []
    with db.read() as c:
        rows = [r for r in (_row(c, int(i)) for i in list(ids)[:MAX_ITEMS]) if r and r["missing"] == 2]
    for r in rows:
        src = _full(r["root"], r["trash_rel"])
        rel = r["rel"]
        dst = _full(r["root"], rel)
        parent = "/".join(rel.split("/")[:-1])
        with db.read() as c:
            held = _held(c, r["folder_id"], parent)
            others = lambda n: n.casefold() != rel.split("/")[-1].casefold() and held(n)  # noqa: E731 - its own record doesn't count
            if os.path.exists(dst) or c.execute("SELECT 1 FROM files WHERE folder_id=? AND rel=? COLLATE NOCASE AND id<>?", (r["folder_id"], rel, r["id"])).fetchone():
                folder = os.path.dirname(dst)
                name = _free_name(folder, os.path.basename(dst), set(), others)
                rel = "/".join(rel.split("/")[:-1] + [name])
                dst = os.path.join(folder, name)
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.rename(src, dst)
        except OSError as ex:
            failed.append({"id": r["id"], "error": str(ex)})
            continue
        done.append({"id": r["id"], "folder": r["folder_id"], "rel": rel, "trash_rel": r["trash_rel"]})
        _tidy_trash(r["root"], r["trash_rel"])
        if len(done) % 200 == 0:
            _mark_restored(db, done[-200:])
    _mark_restored(db, done[len(done) - len(done) % 200:])
    op = None
    if done:
        with db.write() as c:
            if as_op:
                op = record(c, "restore", f"Put back {len(done)} file{'s' if len(done) != 1 else ''} from the Trash", done)
            _recount(c)
    return {"op": op, "restored": len(done), "failed": failed}


def empty(db, ids=None) -> dict:
    """Send Trash files (these, or all of them) to the Windows Recycle Bin. Not undoable here: restore them from the Recycle Bin."""
    from send2trash import send2trash
    with db.read() as c:
        if ids is None:
            rows = c.execute("SELECT f.id, f.trash_rel, d.path AS root FROM files f JOIN folders d ON d.id=f.folder_id WHERE f.missing=2").fetchall()
        else:
            rows = [r for r in (_row(c, int(i)) for i in list(ids)[:MAX_ITEMS]) if r and r["missing"] == 2]
    gone, failed = [], []
    for r in rows:
        p = _full(r["root"], r["trash_rel"])
        try:
            if os.path.exists(p):
                send2trash(p)
            gone.append(r["id"])
            _tidy_trash(r["root"], r["trash_rel"])
        except OSError as ex:
            failed.append({"id": r["id"], "error": str(ex)})
    if gone:
        with db.write() as c:
            c.executemany("DELETE FROM files WHERE id=?", [(i,) for i in gone])
            record(c, "empty", f"Sent {len(gone)} file{'s' if len(gone) != 1 else ''} from the Trash to the Recycle Bin", {"count": len(gone)})
            _recount(c)
    return {"emptied": len(gone), "failed": failed}


def _tidy_trash(root: str, trash_rel: str) -> None:
    """Remove the empty folders a restored or emptied file leaves in .mlg-trash."""
    d = os.path.dirname(_full(root, trash_rel))
    top = os.path.join(root, TRASH)
    while os.path.normcase(d).startswith(os.path.normcase(top)):
        try:
            os.rmdir(d)
        except OSError:
            break
        if os.path.normcase(d) == os.path.normcase(top):
            break
        d = os.path.dirname(d)


def _recount(c) -> None:
    c.execute("UPDATE folders SET file_count=(SELECT COUNT(*) FROM files f WHERE f.folder_id=folders.id AND f.missing=0)")


# ---------------------------------------------------------------- undo
def undo(db, op_id: int) -> dict:
    with db.read() as c:
        op = c.execute("SELECT * FROM ops WHERE id=?", (int(op_id),)).fetchone()
    if not op:
        raise OpError("That operation isn't in the history.")
    if op["undone"]:
        raise OpError("That was undone already.")
    data = json.loads(op["data"])
    kind = op["kind"]
    result = {}
    if kind == "move":
        with db.read() as c:
            roots = {r["id"]: r["path"] for r in c.execute("SELECT id, path FROM folders")}
        back, failed = 0, []
        for it in reversed(data):
            cur = None
            with db.read() as c:
                cur = c.execute("SELECT folder_id, rel FROM files WHERE id=?", (it["id"],)).fetchone()
            if cur and cur["folder_id"] == it["from_folder"] and cur["rel"] == it["from_rel"]:
                back += 1  # back already (an earlier try that undid part of it)
                continue
            if not cur or cur["folder_id"] != it["to_folder"] or cur["rel"] != it["to_rel"]:
                failed.append(it["id"])  # moved again since, or gone: left alone
                continue
            src, dst = _full(roots[it["to_folder"]], it["to_rel"]), _full(roots[it["from_folder"]], it["from_rel"])
            try:
                _move_file(src, dst)
            except (OSError, OpError):
                failed.append(it["id"])
                continue
            if not _relocate(db, it["id"], it["from_folder"], it["from_rel"]):
                _move_file(dst, src)
                failed.append(it["id"])
                continue
            back += 1
        result = {"undone": back, "failed": failed}
    elif kind == "trash":
        ids = [it["id"] for it in data]
        with db.read() as c:
            states = {r[0]: r[1] for r in c.execute(f"SELECT id, missing FROM files WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
        r = restore(db, ids, as_op=False)
        back = sum(1 for i in ids if states.get(i) == 0)  # put back already
        result = {"undone": r["restored"] + back, "failed": [f["id"] for f in r["failed"]], "gone": [i for i in ids if i not in states]}
        if not result["undone"] and not result["failed"]:
            with db.write() as c:
                c.execute("UPDATE ops SET undone=? WHERE id=?", (time.time(), op["id"]))
            raise OpError("Those files were emptied from the Trash since: they're in the Windows Recycle Bin.")
    elif kind == "restore":
        r = trash(db, [it["id"] for it in data])
        with db.write() as c:
            if r["op"]:
                c.execute("DELETE FROM ops WHERE id=?", (r["op"],))  # the undo itself isn't a new step
        result = {"undone": r["trashed"], "failed": [f["id"] for f in r["failed"]]}
    elif kind in ("marks", "tags", "albums"):
        from . import bulk
        result = bulk.undo(db, kind, data)
    elif kind == "eagle":
        from . import eagle
        result = eagle.undo_import(db, data)
    elif kind == "edit":
        from . import edit
        result = edit.undo(db, data)
    else:
        raise OpError("Emptying the Trash can't be undone here: the files are in the Windows Recycle Bin.")
    with db.write() as c:
        if not result.get("failed"):  # a part that failed can be tried again
            c.execute("UPDATE ops SET undone=? WHERE id=?", (time.time(), op["id"]))
        _recount(c)
    if result.get("failed") and not result.get("undone"):
        raise OpError("Nothing could be undone: the files changed since (moved again, or something else took their place).")
    return result


def history(db, limit: int = 100) -> list[dict]:
    with db.read() as c:
        return [{"id": r["id"], "at": r["at"], "kind": r["kind"], "summary": r["summary"], "undone": r["undone"],
                 "undoable": r["kind"] != "empty" and not r["undone"]}
                for r in c.execute("SELECT id, at, kind, summary, undone FROM ops ORDER BY id DESC LIMIT ?", (int(limit),))]
