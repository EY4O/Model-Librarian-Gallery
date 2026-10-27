"""Renaming many files at once by a pattern, shown first and undoable.

    {artist}  who made it            {date}  when it was taken (else modified), 2024-06-12     {n}  1, 2, 3... (001 with {n:3})
    {name}    its name as it is      {model} the checkpoint it was made with   {seed}  its seed   {lora}  its first LoRA

A file stays in its folder and keeps its extension; a name that's taken there gets " (2)". What a picture doesn't have
({artist} for one nobody's named) is left out, with the separator next to it. A Live Photo's video is renamed with its photo.
"""
from __future__ import annotations

import os
import re
import time

from . import fileops, scan

TOKEN = re.compile(r"\{(artist|date|n|name|model|seed|lora)(?::(\d))?\}")
BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _clean(s: str) -> str:
    return BAD.sub(" ", s).strip()


def _fields(c, ids: list[int]) -> dict[int, dict]:
    marks = ",".join("?" * len(ids))
    out = {r["id"]: dict(r) for r in c.execute(
        f"SELECT f.id, f.name, f.rel, f.folder_id, f.shot, f.missing, f.motion, d.path AS root, a.name AS artist, g.model, g.seed"
        f" FROM files f JOIN folders d ON d.id=f.folder_id LEFT JOIN artists a ON a.id=f.artist_id LEFT JOIN generations g ON g.file_id=f.id"
        f" WHERE f.id IN ({marks})", ids)}
    for r in c.execute(f"SELECT file_id, name FROM uses WHERE kind='lora' AND file_id IN ({marks}) ORDER BY rowid", ids):
        out.get(r[0], {}).setdefault("lora", r[1])
    return out


def render(pattern: str, f: dict, n: int, width: int) -> str:
    """The new name (without its extension) for one file, the n-th."""
    def one(m):
        key, pad = m.group(1), m.group(2)
        if key == "n":
            return str(n).zfill(int(pad) if pad else width)
        if key == "date":
            return time.strftime("%Y-%m-%d", time.localtime(f["shot"])) if f.get("shot") else ""
        if key == "name":
            return os.path.splitext(f["name"])[0]
        v = f.get(key)
        return _clean(str(v)) if v not in (None, "") else ""
    out = TOKEN.sub(lambda m: one(m) or "\x00", pattern)
    out = re.sub(r"^\x00[ _.\-]?|[ _.\-]?\x00", "", out)  # what a file doesn't have is left out, with the separator next to it (yours stay)
    out = re.sub(r"^[ _.\-]+|[ _.\-]+$", "", out)
    return _clean(out)[:150].rstrip(". ")


def check(pattern: str, many: bool = True) -> str:
    pattern = str(pattern or "").strip()
    if not pattern:
        raise fileops.OpError("Type a pattern, like {artist}_{date}_{n}.")
    left = TOKEN.sub("", pattern)
    if "{" in left or "}" in left:
        raise fileops.OpError("A part in braces isn't one the pattern knows: {artist}, {date}, {n}, {name}, {model}, {seed}, {lora}.")
    if many and not TOKEN.search(pattern):
        raise fileops.OpError("With no {...} in it, every file would get the same name: add {n} to number them.")
    return pattern


def plan(db, ids, pattern: str, start: int = 1) -> dict:
    """What renaming these files would do: [{id, folder, from_rel, to_rel}] (a Live Photo's video with its photo)."""
    ids = list(ids)
    limited = len(ids) > fileops.MAX_ITEMS  # (said, not done quietly)
    ids = [int(i) for i in ids[:fileops.MAX_ITEMS]]
    pattern = check(pattern, many=len(ids) > 1)
    start = max(0, int(start or 0))
    width = max(2, len(str(start + len(ids) - 1)))
    items, same, unavailable = [], 0, 0
    taken: set[str] = set()
    with db.read() as c:
        fields = {}
        for i in range(0, len(ids), 30000):
            fields.update(_fields(c, ids[i:i + 30000]))
        n = start
        for fid in ids:
            f = fields.get(fid)
            if not f or f["missing"] not in fileops.PRESENT or f["missing"] == scan.PAIRED:
                unavailable += 1
                continue
            stem = render(pattern, f, n, width) or os.path.splitext(f["name"])[0]
            n += 1
            ext = os.path.splitext(f["name"])[1]
            sub = f["rel"].rsplit("/", 1)[0] if "/" in f["rel"] else ""
            folder = os.path.join(f["root"], *sub.split("/")) if sub else f["root"]
            if stem + ext == f["name"]:
                same += 1
                taken.add(os.path.normcase(os.path.join(folder, f["name"])))
                continue
            held = fileops._held(c, f["folder_id"], sub)
            video = fileops._row(c, f["motion"]) if f["motion"] else None
            if video is not None and video["missing"] == scan.PAIRED:
                new, vnew = fileops._free_pair(folder, stem + ext, video["name"], taken, held)
            elif (stem + ext).lower() == f["name"].lower():  # only the case changes: the name is its own already
                video, new = None, stem + ext
                taken.add(os.path.normcase(os.path.join(folder, new)))
            else:
                video, new = None, fileops._free_name(folder, stem + ext, taken, held)
            items.append({"id": fid, "folder": f["folder_id"], "from_rel": f["rel"], "to_rel": f"{sub}/{new}" if sub else new})
            if video is not None:
                items.append({"id": video["id"], "folder": f["folder_id"], "from_rel": video["rel"], "to_rel": f"{sub}/{vnew}" if sub else vnew, "with": fid})
    shown = [it for it in items if "with" not in it]
    return {"count": len(shown), "same": same, "unavailable": unavailable, "items": items, "limited": limited,
            "preview": [{"from": it["from_rel"].split("/")[-1], "to": it["to_rel"].split("/")[-1]} for it in shown[:8]]}


def _move(db, it: dict, back: bool = False) -> bool:
    src_rel, dst_rel = (it["to_rel"], it["from_rel"]) if back else (it["from_rel"], it["to_rel"])
    with db.read() as c:
        root = c.execute("SELECT path FROM folders WHERE id=?", (it["folder"],)).fetchone()
        cur = c.execute("SELECT folder_id, rel FROM files WHERE id=?", (it["id"],)).fetchone()
    if not root or not cur or cur["folder_id"] != it["folder"] or cur["rel"] != src_rel:
        return False  # moved, renamed or gone since: left alone
    src, dst = fileops._full(root[0], src_rel), fileops._full(root[0], dst_rel)
    if os.path.exists(dst) and os.path.normcase(src) != os.path.normcase(dst):
        return False
    try:
        os.rename(src, dst)
    except OSError:
        return False
    if not fileops._relocate(db, it["id"], it["folder"], dst_rel, recredit=False):
        os.rename(dst, src)
        return False
    return True


def apply(db, ids, pattern: str, start: int = 1) -> dict:
    p = plan(db, ids, pattern, start)
    if not p["count"]:
        raise fileops.OpError("Nothing to rename: those names are what the pattern gives already." if p["same"] else "Nothing to rename.")
    done, failed, ok_photos = [], [], set()
    for it in p["items"]:
        if "with" in it and it["with"] not in ok_photos:
            continue  # its photo kept its name: so does it
        if _move(db, it):
            done.append(it)
            if "with" not in it:
                ok_photos.add(it["id"])
        elif "with" in it:  # its video couldn't follow: the photo goes back, so the two stay a pair
            photo = next(d for d in done if d["id"] == it["with"])
            if _move(db, photo, back=True):
                done.remove(photo)
            failed.append(it["with"])
        else:
            failed.append(it["id"])
    op = None
    if done:
        with db.write() as c:
            n = sum(1 for it in done if "with" not in it)
            first = next((it for it in done if "with" not in it), None)
            to = first["to_rel"].split("/")[-1] if first else ""
            op = fileops.record(c, "rename", f"Renamed {n:,} file{'s' if n != 1 else ''} {f'to {to}' if n == 1 and to else 'by a pattern'}", done)
    db.user_change()
    return {"op": op, "renamed": sum(1 for it in done if "with" not in it), "failed": failed}


def undo(db, data) -> dict:
    back, failed = 0, []
    for it in reversed(data):
        if _move(db, it, back=True):
            back += "with" not in it
        else:
            failed.append(it["id"])
    return {"undone": back, "failed": failed}
