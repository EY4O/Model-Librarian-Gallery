"""Finding your media: walk the folders you added, fingerprint new files, notice changes, moves and deletions.

Read-only: nothing in your folders is ever written. A rescan only stats files it already knows; a file is read
(its first and last 64 KB, and an image's header) only when it's new or changed. A file that moved or was
renamed is recognised by its fingerprint and keeps its record (so later its ratings, tags and characters).
A file that's gone is marked missing, not forgotten; a folder whose drive isn't there is marked offline and its
records are left alone.
"""
from __future__ import annotations

import hashlib
import os
import time

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".m4v"}
CHUNK = 64 * 1024
BATCH = 400  # files written per transaction


def kind_of(name: str) -> str | None:
    ext = os.path.splitext(name)[1].lower()
    return "image" if ext in IMAGE_EXTS else "video" if ext in VIDEO_EXTS else None


def fingerprint(path: str, size: int) -> str:
    """Size + first and last 64 KB: fast on a hard drive, and a match is the same file in practice."""
    h = hashlib.blake2b(str(size).encode(), digest_size=16)
    with open(path, "rb") as f:
        h.update(f.read(CHUNK))
        if size > 2 * CHUNK:
            f.seek(-CHUNK, os.SEEK_END)
            h.update(f.read(CHUNK))
    return h.hexdigest()


def image_size(path: str) -> tuple[int | None, int | None]:
    """Width and height from the header (the pixels aren't decoded)."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
            orientation = (im.getexif() or {}).get(0x0112, 1)
            return (h, w) if orientation in (5, 6, 7, 8) else (w, h)  # shown turned by its EXIF orientation
    except Exception:
        return None, None


def skip_dir(name: str, skip_eagle: bool) -> bool:
    """Hidden and system folders, thumbnail caches, and (by default) Eagle libraries, which Eagle manages."""
    low = name.lower()
    return (name.startswith((".", "$")) or low in ("__pycache__", "system volume information", "@eadir")
            or (skip_eagle and low.endswith(".library")))


def walk(root: str, skip_eagle: bool = True):
    """(full path, relative path with /, DirEntry) for every media file under root, without following links."""
    stack = [""]
    while stack:
        rel_dir = stack.pop()
        try:
            with os.scandir(os.path.join(root, rel_dir) if rel_dir else root) as it:
                entries = list(it)
        except OSError:
            continue  # a folder we may not read: skipped, not fatal
        for e in entries:
            rel = f"{rel_dir}/{e.name}" if rel_dir else e.name
            try:
                if e.is_dir(follow_symlinks=False):
                    if not skip_dir(e.name, skip_eagle):
                        stack.append(rel)
                elif e.is_file(follow_symlinks=False) and kind_of(e.name):
                    yield e.path, rel, e
            except OSError:
                continue


def overlapping(existing: list[str], path: str) -> str | None:
    """The folder already added that this one is inside, or that's inside it (it would be indexed twice)."""
    p = os.path.normcase(os.path.abspath(path)).rstrip("\\/") + os.sep
    for other in existing:
        o = os.path.normcase(os.path.abspath(other)).rstrip("\\/") + os.sep
        if p.startswith(o) or o.startswith(p):
            return other
    return None


def scan_folder(db, folder: dict, *, skip_eagle: bool = True, progress=None, stop=None) -> dict:
    """Bring the index up to date with one folder. -> counts. `progress(dict)` is called now and then;
    `stop()` returning True ends the walk early (then nothing is marked missing)."""
    root, fid = folder["path"], folder["id"]
    now = time.time()
    counts = {"seen": 0, "added": 0, "changed": 0, "moved": 0, "missing": 0, "unchanged": 0, "errors": 0, "offline": False}
    if not os.path.isdir(root):
        with db.write() as c:
            c.execute("UPDATE folders SET online=0 WHERE id=?", (fid,))
        counts["offline"] = True
        return counts

    with db.read() as c:
        known = {r["rel"]: r for r in c.execute("SELECT id, rel, size, mtime, missing FROM files WHERE folder_id=?", (fid,))}
    seen: set[str] = set()
    touched: list[int] = []
    pending: list[tuple] = []  # (op, args)

    for full, rel, entry in walk(root, skip_eagle):
        if stop and stop():
            break
        seen.add(rel)
        counts["seen"] += 1
        try:
            st = entry.stat()
            _visit(db, fid, full, rel, st.st_size, st.st_mtime, known.get(rel), now, touched, pending, counts)
        except OSError:
            counts["errors"] += 1
        if len(touched) + len(pending) >= BATCH:
            _flush(db, now, touched, pending)
            if progress:
                progress(dict(counts))
    _flush(db, now, touched, pending)

    stopped = bool(stop and stop())
    with db.write() as c:
        if not stopped:  # only a full walk can say a file is gone
            gone = [r["id"] for rel, r in known.items() if rel not in seen and not r["missing"]]
            # a file found elsewhere in this scan (a move) has its new folder/rel already: leave it
            for i in gone:
                cur = c.execute("SELECT folder_id, rel FROM files WHERE id=?", (i,)).fetchone()
                if cur and cur["folder_id"] == fid and cur["rel"] not in seen:
                    c.execute("UPDATE files SET missing=1 WHERE id=?", (i,))
                    counts["missing"] += 1
        n = c.execute("SELECT COUNT(*) FROM files WHERE folder_id=? AND missing=0", (fid,)).fetchone()[0]
        c.execute("UPDATE folders SET online=1, last_scan=?, file_count=? WHERE id=?", (now, n, fid))
    if progress:
        progress(dict(counts))
    return counts


def _visit(db, fid, full, rel, size, mtime, old, now, touched, pending, counts) -> None:
    """One file found on disk: unchanged, changed, moved here from elsewhere, or new."""
    if old and old["size"] == size and abs(old["mtime"] - mtime) < 1e-6:
        counts["unchanged"] += 1
        if old["missing"]:  # it's back (a drive plugged in again): no longer missing
            touched.append(old["id"])
        return
    fp = fingerprint(full, size)
    name = os.path.basename(rel)
    ext = os.path.splitext(name)[1].lower()
    kind = kind_of(name)
    w, h = image_size(full) if kind == "image" else (None, None)
    if old:
        pending.append(("change", (size, mtime, fp, w, h, now, old["id"])))
        counts["changed"] += 1
        return
    moved = _moved_from(db, fp, size, pending)
    if moved:
        pending.append(("move", (fid, rel, name, ext, size, mtime, now, moved)))
        counts["moved"] += 1
    else:
        pending.append(("insert", (fid, rel, name, ext, kind, size, mtime, fp, w, h, now, now)))
        counts["added"] += 1


def _flush(db, now, touched: list, pending: list) -> None:
    if not touched and not pending:
        return
    with db.write() as c:
        if touched:
            # back where it was: no longer missing (a file in the Trash brought back by hand leaves the Trash too)
            c.executemany("UPDATE files SET last_seen=?, missing=0, trashed=NULL, trash_rel=NULL WHERE id=?", [(now, i) for i in touched])
        for op, args in pending:
            if op == "insert":
                c.execute("INSERT INTO files (folder_id, rel, name, ext, kind, size, mtime, fp, width, height, first_seen, last_seen)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", args)
            elif op == "change":
                c.execute("UPDATE files SET size=?, mtime=?, fp=?, width=?, height=?, last_seen=?, missing=0, thumb=0, meta=0, phash=NULL, trashed=NULL, trash_rel=NULL WHERE id=?", args)
            elif op == "move":
                c.execute("UPDATE files SET folder_id=?, rel=?, name=?, ext=?, size=?, mtime=?, last_seen=?, missing=0, meta=0 WHERE id=?", args)
    touched.clear()
    pending.clear()


def index_files(db, folder: dict, rels: list[str], *, skip_eagle: bool = True) -> dict:
    """Bring just these files (paths inside the folder, with /) up to date: what the folder watcher does
    when something changes, instead of walking the whole folder. A path that's gone is marked missing."""
    root, fid = folder["path"], folder["id"]
    now = time.time()
    counts = {"seen": 0, "added": 0, "changed": 0, "moved": 0, "missing": 0, "unchanged": 0, "errors": 0, "offline": False}
    rels = [r for r in dict.fromkeys(rels) if kind_of(r) and not any(skip_dir(p, skip_eagle) for p in r.split("/")[:-1])]
    if not rels:
        return counts
    touched: list[int] = []
    pending: list[tuple] = []
    for i in range(0, len(rels), 500):
        part = rels[i:i + 500]
        with db.read() as c:
            known = {r["rel"]: r for r in c.execute(f"SELECT id, rel, size, mtime, missing FROM files WHERE folder_id=? AND rel IN ({','.join('?' * len(part))})",
                                                     (fid, *part))}
        gone = []
        for rel in part:
            full = os.path.join(root, *rel.split("/"))
            try:
                st = os.stat(full)
            except OSError:
                if rel in known and not known[rel]["missing"]:
                    gone.append(known[rel]["id"])
                continue
            counts["seen"] += 1
            try:
                _visit(db, fid, full, rel, st.st_size, st.st_mtime, known.get(rel), now, touched, pending, counts)
            except OSError:
                counts["errors"] += 1
        _flush(db, now, touched, pending)
        if gone:
            with db.write() as c:
                c.executemany("UPDATE files SET missing=1 WHERE id=?", [(g,) for g in gone])
            counts["missing"] += len(gone)
    with db.write() as c:
        n = c.execute("SELECT COUNT(*) FROM files WHERE folder_id=? AND missing=0", (fid,)).fetchone()[0]
        c.execute("UPDATE folders SET file_count=? WHERE id=?", (n, fid))
    return counts


def _moved_from(db, fp: str, size: int, pending: list) -> int | None:
    """A known file with this fingerprint that isn't where it was any more (moved or renamed): its id."""
    claimed = {args[-1] for op, args in pending if op == "move"}
    with db.read() as c:
        rows = c.execute("SELECT f.id, f.rel, f.missing, d.path FROM files f JOIN folders d ON d.id=f.folder_id WHERE f.fp=? AND f.size=?",
                         (fp, size)).fetchall()
    for r in rows:
        if r["id"] in claimed or r["missing"] == 2:  # a file in the Trash is still there, not moved
            continue
        if r["missing"] or not os.path.exists(os.path.join(r["path"], r["rel"])):
            return r["id"]
    return None
