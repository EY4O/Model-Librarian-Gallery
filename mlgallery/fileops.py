"""Moving and deleting files, each shown first and undoable.

* Move: into one of your library folders (or a folder inside one), so the file keeps its ratings, tags,
  albums and characters. On the same drive it's a rename; across drives it's copied, the copy checked
  (size and fingerprint), and only then the original removed. A name already taken gets " (2)" and so on.
* Delete: to the Gallery's Trash - a hidden `.mlg-trash` folder in the file's own library folder (the same
  drive, so it's instant). Restore puts it back where it was. Emptying the Trash sends the files to the
  Windows Recycle Bin, so even then they can be recovered.
* Every operation is written to `ops` first, with what's needed to undo it.

A file is in the Trash when files.missing = 2 (so every view that lists files with missing = 0 leaves it out).
A Live Photo's video goes wherever its photo goes, under the photo's name (scan.pair_live).
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
    return c.execute("SELECT f.id, f.folder_id, f.rel, f.name, f.size, f.fp, f.missing, f.trash_rel, f.motion, d.path AS root, d.online"
                     " FROM files f JOIN folders d ON d.id=f.folder_id WHERE f.id=?", (fid,)).fetchone()


PRESENT = (0, scan.PAIRED)  # in its folder: shown, or shown with its Live Photo


def _with_motion(c, rows) -> list[tuple]:
    """(row, photo id) for the files asked for, each Live Photo followed by its video (photo id: the photo it goes with)."""
    out, seen = [], set()
    for r in rows:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        out.append((r, None))
        m = _row(c, r["motion"]) if r["motion"] else None
        if m and m["id"] not in seen:
            seen.add(m["id"])
            out.append((m, r["id"]))
    return out


def _as_photo(photo_name: str, video_name: str) -> str:
    """A Live Photo's video under its photo's name (IMG_1234 (2).HEIC -> IMG_1234 (2).MOV), so they stay a pair."""
    return os.path.splitext(photo_name)[0] + os.path.splitext(video_name)[1]


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


def _relocate(db, fid: int, folder: int, rel: str, recredit: bool = True) -> bool:
    """The index follows a file to folder/rel; False (and nothing changed) if another record holds that name.
    recredit: its artist is looked for again (another folder may say who made it); a rename doesn't: what its old name
    said (a Pixiv post, a Danbooru artist) is still where it's from."""
    from .metadata import refresh_search
    name = rel.split("/")[-1]
    try:
        with db.write() as c:
            c.execute(f"UPDATE files SET folder_id=?, rel=?, name=?, ext=?{', credited=0' if recredit else ''} WHERE id=?",
                      (folder, rel, name, os.path.splitext(name)[1].lower(), fid))
            refresh_search(c, fid)
    except sqlite3.IntegrityError:
        return False
    return True


def record(c, kind: str, summary: str, data) -> int:
    return c.execute("INSERT INTO ops (at, kind, summary, data) VALUES (?,?,?,?)", (time.time(), kind, summary, json.dumps(data))).lastrowid


# ---------------------------------------------------------------- move
def _free_pair(dest_dir: str, photo: str, video: str, taken: set[str], held=None) -> tuple[str, str]:
    """One name free for both halves of a Live Photo there: IMG_3.HEIC and IMG_3.MOV, else IMG_3 (2).HEIC and IMG_3 (2).MOV."""
    stem, pe = os.path.splitext(photo)
    ve = os.path.splitext(video)[1]
    n = 1
    while True:
        s = stem if n == 1 else f"{stem} ({n})"
        names = (s + pe, s + ve)
        if not any(os.path.exists(os.path.join(dest_dir, x)) or os.path.normcase(os.path.join(dest_dir, x)) in taken
                   or (held is not None and held(x)) for x in names):
            taken.update(os.path.normcase(os.path.join(dest_dir, x)) for x in names)
            return names
        n += 1


def _touched(items) -> set[int]:
    """The folders some file items were in or went to (for the Live Photo pairing there)."""
    out = set()
    for it in items if isinstance(items, list) else ():
        if isinstance(it, dict):
            out.update(int(it[k]) for k in ("folder", "from_folder", "to_folder") if it.get(k) is not None)
    return out


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
        listed = _with_motion(c, rows)
        video_of = {photo: r for r, photo in listed if photo is not None}
        names: dict[int, str] = {}  # the name a Live Photo's video gets there, chosen with its photo's
        for r, photo in listed:
            src = _full(r["root"], r["rel"])
            ok = r["missing"] in PRESENT and r["online"] and os.path.isfile(src)
            if photo is not None:  # a Live Photo's video: where its photo goes, under its name, if its photo goes
                if r["id"] not in names or not ok:
                    continue
                name = names[r["id"]]
            else:
                if not ok:
                    out["unavailable"] += 1
                    continue
                if os.path.normcase(os.path.dirname(src)) == os.path.normcase(dest_dir):
                    out["already"] += 1
                    continue
                v = video_of.get(r["id"])
                if v is not None:
                    name, names[v["id"]] = _free_pair(dest_dir, r["name"], v["name"], taken, held)
                else:
                    name = _free_name(dest_dir, r["name"], taken, held)
                out["renamed"] += name != r["name"]
                out["count"] += 1  # a Live Photo counts as one file
            out["copies"] += photo is None and not _same_drive(src, d["path"])
            out["size"] += r["size"] or 0
            rel = (f"{sub}/{name}" if sub else name)
            item = {"id": r["id"], "from_folder": r["folder_id"], "from_rel": r["rel"], "to_folder": d["id"], "to_rel": rel}
            if photo is not None:
                item["with"] = photo
            out["items"].append(item)
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
    moved: dict[int, dict] = {}  # photos (and files) moved, by id: a Live Photo's video goes only after its photo
    with db.read() as c:
        roots = {r["id"]: r["path"] for r in c.execute("SELECT id, path FROM folders")}
    for it in plan["items"]:
        photo = it.get("with")
        if photo is not None and photo not in moved:
            continue  # its photo didn't move: neither does it
        src, dst = _full(roots[it["from_folder"]], it["from_rel"]), _full(roots[it["to_folder"]], it["to_rel"])
        try:
            _move_file(src, dst)
            if not _relocate(db, it["id"], it["to_folder"], it["to_rel"]):
                _move_file(dst, src)  # the index has that name already (it changed meanwhile): put the file back
                raise OpError("that name was taken meanwhile")
        except (OSError, OpError) as ex:
            failed.append({"id": it["id"] if photo is None else photo, "error": str(ex)})
            if photo is not None:  # its video wouldn't go: the photo goes back, so the two stay together
                p = moved.pop(photo)
                try:
                    _move_file(_full(roots[p["to_folder"]], p["to_rel"]), _full(roots[p["from_folder"]], p["from_rel"]))
                    _relocate(db, p["id"], p["from_folder"], p["from_rel"])
                    done.remove(p)
                except (OSError, OpError):
                    pass  # it stays moved, on its own
            continue
        done.append(it)
        if photo is None:
            moved[it["id"]] = it
    op = None
    if done:
        with db.write() as c:
            n = sum(1 for it in done if "with" not in it)
            op = record(c, "move", f"Moved {n:,} file{'s' if n != 1 else ''} to {os.path.basename(plan['dest']) or plan['dest']}", done)
            scan.pair_live(c, _touched(done))
            _recount(c)
        _note_empty(db, _left(done, "from_folder", "from_rel"))
    return {"op": op, "moved": sum(1 for it in done if "with" not in it), "failed": failed}


# ---------------------------------------------------------------- the Trash
def trash(db, ids) -> dict:
    """Into the Gallery's Trash (the folder's .mlg-trash, same drive: instant). A Live Photo goes whole: its video
    first, and if that can't go (in use), the photo stays too."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    done, failed = [], []
    with db.read() as c:
        groups: list[list] = []  # [file, its Live Photo video or None]
        for r, photo in _with_motion(c, [r for r in (_row(c, int(i)) for i in list(ids)[:MAX_ITEMS]) if r]):
            if photo is None:
                groups.append([r, None])
            else:
                groups[-1][1] = r  # a video comes right after its photo
    shown, unmarked = 0, []
    for r, video in groups:
        src = _full(r["root"], r["rel"])
        if r["missing"] not in PRESENT or not r["online"] or not os.path.isfile(src):
            failed.append({"id": r["id"], "error": "not reachable"})
            continue
        moved = []
        try:
            for f in ([video] if video is not None and os.path.isfile(_full(video["root"], video["rel"])) else []) + [r]:
                trash_rel = f"{TRASH}/{stamp}/{f['rel']}"
                dst = _full(f["root"], trash_rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.rename(_full(f["root"], f["rel"]), dst)  # inside its own folder: always the same drive
                moved.append({"id": f["id"], "folder": f["folder_id"], "rel": f["rel"], "trash_rel": trash_rel})
        except OSError as ex:
            for m in moved:  # the photo wouldn't go: its video comes back out
                try:
                    os.rename(_full(r["root"], m["trash_rel"]), _full(r["root"], m["rel"]))
                    _tidy_trash(r["root"], m["trash_rel"])
                except OSError:
                    done.append(m)  # it stays in the Trash, on its own
                    unmarked.append(m)
            failed.append({"id": r["id"], "error": str(ex)})
            continue
        moved.sort(key=lambda m: m["id"] != r["id"])  # the photo first: putting it back (Undo) brings its video with it
        done.extend(moved)
        unmarked.extend(moved)
        shown += 1
        if len(unmarked) >= 200:  # the index in batches: one write per file made thousands slow
            _mark_trashed(db, unmarked)
            unmarked = []
    _mark_trashed(db, unmarked)
    op = None
    if done:
        with db.write() as c:
            op = record(c, "trash", f"Moved {shown:,} file{'s' if shown != 1 else ''} to the Trash", done)
            scan.pair_live(c, _touched(done))
            _recount(c)
        _note_empty(db, _left(done, "folder", "rel"))
    return {"op": op, "trashed": shown, "failed": failed}


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
        pairs = _with_motion(c, [r for r in (_row(c, int(i)) for i in list(ids)[:MAX_ITEMS]) if r and r["missing"] == 2])
    names: dict[int, str] = {}  # the name each photo is put back under, for its Live Photo video
    video_of = {photo: r for r, photo in pairs if photo is not None}
    for r, photo in pairs:
        if r["missing"] != 2 or (photo is not None and photo not in names):
            continue
        src = _full(r["root"], r["trash_rel"])
        rel = r["rel"]
        if photo is not None:
            rel = "/".join(rel.split("/")[:-1] + [_as_photo(names[photo], rel.split("/")[-1])])
        elif r["id"] in video_of and video_of[r["id"]]["missing"] == 2:  # a Live Photo: a name free for both, if its own isn't
            v = video_of[r["id"]]
            folder = os.path.dirname(_full(r["root"], rel))
            with db.read() as c:
                held = _held(c, r["folder_id"], "/".join(rel.split("/")[:-1]))
                own = {rel.split("/")[-1].casefold(), _as_photo(rel.split("/")[-1], v["rel"].split("/")[-1]).casefold()}
                name, _ = _free_pair(folder, rel.split("/")[-1], v["rel"].split("/")[-1], set(), lambda n: n.casefold() not in own and held(n))
            rel = "/".join(rel.split("/")[:-1] + [name])
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
        names[r["id"]] = rel.split("/")[-1]
        _tidy_trash(r["root"], r["trash_rel"])
        if len(done) % 200 == 0:
            _mark_restored(db, done[-200:])
    _mark_restored(db, done[len(done) - len(done) % 200:])
    op = None
    shown = sum(1 for r, photo in pairs if photo is None and r["id"] in names)
    if done:
        with db.write() as c:
            if as_op:
                op = record(c, "restore", f"Put back {shown:,} file{'s' if shown != 1 else ''} from the Trash", done)
            scan.pair_live(c, _touched(done))
            _recount(c)
    return {"op": op, "restored": shown, "failed": failed}


def empty(db, ids=None) -> dict:
    """Send Trash files (these, or all of them) to the Windows Recycle Bin. Not undoable here: restore them from the Recycle Bin.
    Emptying all of it also sends the originals kept for undoing edits (each folder's .mlg-trash/edits) there."""
    from send2trash import send2trash
    with db.read() as c:
        if ids is None:
            rows = c.execute("SELECT f.id, f.trash_rel, f.motion, d.path AS root FROM files f JOIN folders d ON d.id=f.folder_id WHERE f.missing=2").fetchall()
        else:
            rows = [r for r, _ in _with_motion(c, [r for r in (_row(c, int(i)) for i in list(ids)[:MAX_ITEMS]) if r and r["missing"] == 2])
                    if r["missing"] == 2]
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
    backups = 0
    if ids is None:
        with db.read() as c:
            roots = [r[0] for r in c.execute("SELECT path FROM folders WHERE online=1")]
        for root in roots:
            edits = os.path.join(root, TRASH, "edits")
            if not os.path.isdir(edits):
                continue
            n = sum(len(fs) for _, _, fs in os.walk(edits))
            try:
                send2trash(edits)
                backups += n
            except OSError as ex:
                failed.append({"id": None, "error": str(ex)})
                continue
            try:
                os.rmdir(os.path.join(root, TRASH))  # nothing else left in it
            except OSError:
                pass
    videos = {r["motion"] for r in rows if r["motion"]}
    shown = sum(1 for i in gone if i not in videos)  # a Live Photo counts as one
    if gone or backups:
        with db.write() as c:
            c.executemany("DELETE FROM files WHERE id=?", [(i,) for i in gone])
            what = [f"{shown:,} file{'s' if shown != 1 else ''} from the Trash"] if gone else []
            what += [f"{backups} original{'s' if backups != 1 else ''} kept for undoing edits"] if backups else []
            record(c, "empty", f"Sent {' and '.join(what)} to the Recycle Bin", {"count": len(gone), "backups": backups})
            _recount(c)
    return {"emptied": shown, "backups": backups, "failed": failed}


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


def _note_empty(db, places) -> None:
    """Folders left empty, their files moved away or put in the Trash: still listed in the sidebar (as the next scan
    would list them), until they're deleted. places: (folder id, folder inside it)."""
    with db.read() as c:
        roots = {r["id"]: r["path"] for r in c.execute("SELECT id, path FROM folders")}
    empty = sorted({(fid, rel) for fid, rel in places if rel and fid in roots and scan.is_empty_dir(_full(roots[fid], rel))})
    if empty:
        with db.write() as c:
            c.executemany("INSERT OR IGNORE INTO dirs (folder_id, rel) VALUES (?, ?)", empty)


def _left(items, folder: str, rel: str) -> set:
    """The folders some file items were taken out of."""
    return {(it[folder], it[rel].rsplit("/", 1)[0]) for it in items if "/" in it[rel]}


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
        for it in [i for i in reversed(data) if "with" not in i] + [i for i in reversed(data) if "with" in i]:
            if "with" in it and it["with"] in failed:
                continue  # its photo couldn't go back: it stays with it
            cur = None
            with db.read() as c:
                cur = c.execute("SELECT folder_id, rel FROM files WHERE id=?", (it["id"],)).fetchone()
            if cur and cur["folder_id"] == it["from_folder"] and cur["rel"] == it["from_rel"]:
                back += 1  # back already (an earlier try that undid part of it)
                continue
            if not cur or cur["folder_id"] != it["to_folder"] or cur["rel"] != it["to_rel"]:
                failed.append(it["id"])  # moved again since, or gone: left alone
                continue
            if it["from_folder"] not in roots or it["to_folder"] not in roots:
                failed.append(it["id"])  # (its folder isn't in the library any more)
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
        back = sum(1 for i in ids if states.get(i) in PRESENT)  # put back already
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
    elif kind in ("marks", "tags", "albums", "credits", "characters", "album_delete", "dupes_kept", "dupes_unkept", "album_move", "review"):
        from . import bulk
        result = bulk.undo(db, kind, data)
    elif kind == "eagle":
        from . import eagle
        result = eagle.undo_import(db, data)
    elif kind == "edit":
        from . import edit
        result = edit.undo(db, data)
    elif kind == "carry":
        from . import libraries
        result = libraries.undo_bring(db, data)
    elif kind == "file":
        from . import downloads
        result = downloads.undo(db, data)
    elif kind == "rename":
        from . import rename
        result = rename.undo(db, data)
    elif kind in ("dir_make", "dir_move", "dir_delete", "copy_in"):
        from . import folders
        result = folders.undo(db, kind, data)
    else:
        raise OpError("Emptying the Trash can't be undone here: the files are in the Windows Recycle Bin.")
    with db.write() as c:
        if not result.get("failed"):  # a part that failed can be tried again
            c.execute("UPDATE ops SET undone=? WHERE id=?", (time.time(), op["id"]))
        scan.pair_live(c, _touched(data))
        _recount(c)
    db.user_change()  # the lists counted ahead (Artists, Characters...) are counted again, not shown as they were
    if result.get("failed") and not result.get("undone"):
        raise OpError("Nothing could be undone: the files changed since (moved again, or something else took their place).")
    return result


def history(db, limit: int = 100) -> list[dict]:
    with db.read() as c:
        out = []
        for r in c.execute("SELECT id, at, kind, summary, undone, data FROM ops ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall():
            undoable = r["kind"] != "empty" and not r["undone"]
            if undoable and r["kind"] == "edit":  # a replaced original sent to the Recycle Bin (by emptying the Trash) can't come back from here
                data = json.loads(r["data"])
                if data.get("backup_rel"):
                    f = _row(c, data["id"])
                    undoable = bool(f) and os.path.isfile(_full(f["root"], data["backup_rel"]))
            out.append({"id": r["id"], "at": r["at"], "kind": r["kind"], "summary": r["summary"], "undone": r["undone"], "undoable": undoable})
        return out
