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

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".avif"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".m4v", ".avi", ".3gp", ".3g2"}
PAIRED = 3  # files.missing for the short video of a Live Photo: shown with its photo, not on its own (see pair_live)
LIVE_PHOTO = (".heic", ".heif", ".jpg", ".jpeg")  # the photo of a Live Photo, preferred in this order
LIVE_MAX = 20 * 1024 * 1024  # a Live Photo's video is a few seconds (a few MB): a bigger .MOV is a video of its own
CHUNK = 64 * 1024
BATCH = 400  # files written per transaction
APP_EXE = "Model Librarian Gallery.exe"  # a folder holding it is the Gallery itself (its own icons aren't media)


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
                    if not skip_dir(e.name, skip_eagle) and not os.path.isfile(os.path.join(e.path, APP_EXE)):  # the portable Gallery kept in a library
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
            gone = [r["id"] for rel, r in known.items() if rel not in seen and r["missing"] in (0, PAIRED)]
            # a file found elsewhere in this scan (a move) has its new folder/rel already: leave it
            for i in gone:
                # (and one put in the Trash while the walk went on isn't gone: it's in the Trash)
                cur = c.execute("SELECT folder_id, rel, missing FROM files WHERE id=?", (i,)).fetchone()
                if cur and cur["folder_id"] == fid and cur["rel"] not in seen and cur["missing"] in (0, PAIRED):
                    c.execute("UPDATE files SET missing=1 WHERE id=?", (i,))
                    counts["missing"] += 1
        pair_live(c, fid)
        n = c.execute("SELECT COUNT(*) FROM files WHERE folder_id=? AND missing=0", (fid,)).fetchone()[0]
        c.execute("UPDATE folders SET online=1, last_scan=?, file_count=? WHERE id=?", (now, n, fid))
    if progress:
        progress(dict(counts))
    return counts


def _visit(db, fid, full, rel, size, mtime, old, now, touched, pending, counts) -> None:
    """One file found on disk: unchanged, changed, moved here from elsewhere, or new."""
    if old and old["size"] == size and abs(old["mtime"] - mtime) < 1e-6:
        counts["unchanged"] += 1
        if old["missing"] and old["missing"] != PAIRED:  # it's back (a drive plugged in again): no longer missing
            touched.append(old["id"])
        return
    fp = fingerprint(full, size)
    name = os.path.basename(rel)
    ext = os.path.splitext(name)[1].lower()
    kind = kind_of(name)
    w, h = image_size(full) if kind == "image" else (None, None)
    if old and old["missing"] == 2:
        # a new file where one in the Trash was: that one keeps its ratings and tags under a free name, this one is new
        pending.append(("evict", (fid, old["id"], rel)))
        old = None
    if old:
        pending.append(("change", (size, mtime, fp, w, h, now, old["id"])))
        counts["changed"] += 1
        return
    moved = _moved_from(db, fp, size, pending)
    if moved:
        pending.append(("move", (fid, rel, name, ext, size, mtime, now, moved)))
        counts["moved"] += 1
    else:
        from .dates import from_name
        taken = from_name(name, mtime)
        pending.append(("insert", (fid, rel, name, ext, kind, size, mtime, fp, w, h, now, now, taken, taken or mtime)))
        counts["added"] += 1


def _flush(db, now, touched: list, pending: list) -> None:
    if not touched and not pending:
        return
    from .metadata import refresh_search, search_name
    with db.write() as c:
        if touched:
            # back where it was: no longer missing (a file in the Trash brought back by hand leaves the Trash too)
            c.executemany("UPDATE files SET last_seen=?, missing=0, trashed=NULL, trash_rel=NULL WHERE id=?", [(now, i) for i in touched])
        for op, args in pending:
            if op == "evict":
                fid, oid, rel = args
                parent, name = rel.rsplit("/", 1) if "/" in rel else ("", rel)
                stem, ext = os.path.splitext(name)
                n = 2
                while c.execute("SELECT 1 FROM files WHERE folder_id=? AND rel=? COLLATE NOCASE",
                                (fid, f"{parent}/{stem} ({n}){ext}" if parent else f"{stem} ({n}){ext}")).fetchone():
                    n += 1
                free = f"{stem} ({n}){ext}"
                c.execute("UPDATE files SET rel=?, name=? WHERE id=?", (f"{parent}/{free}" if parent else free, free, oid))
                refresh_search(c, oid)
            elif op == "insert":
                new = c.execute("INSERT INTO files (folder_id, rel, name, ext, kind, size, mtime, fp, width, height, first_seen, last_seen, taken, shot)"
                                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", args).lastrowid
                # findable by its name at once; reading its metadata fills in the rest
                c.execute("INSERT INTO search (rowid, name, prompt, negative, models, tags) VALUES (?,?,'','','','')", (new, search_name(args[2])))
            elif op == "change":
                c.execute("UPDATE files SET size=?, mtime=?, fp=?, width=?, height=?, last_seen=?, missing=0, thumb=0, meta=0, phash=NULL, colors=0,"
                          " dated=0, trashed=NULL, trash_rel=NULL WHERE id=?", args)
                c.execute("DELETE FROM colors WHERE file_id=?", (args[-1],))  # another picture now: its colours are worked out again
            elif op == "move":
                c.execute("UPDATE files SET folder_id=?, rel=?, name=?, ext=?, size=?, mtime=?, last_seen=?, missing=0, meta=0, dated=0 WHERE id=?", args)
                refresh_search(c, args[-1])
    touched.clear()
    pending.clear()


def index_files(db, folder: dict, rels: list[str], *, skip_eagle: bool = True) -> dict:
    """Bring just these files (paths inside the folder, with /) up to date: what the folder watcher does
    when something changes, instead of walking the whole folder. A path that's gone is marked missing."""
    root, fid = folder["path"], folder["id"]
    now = time.time()
    counts = {"seen": 0, "added": 0, "changed": 0, "moved": 0, "missing": 0, "unchanged": 0, "errors": 0, "offline": False}
    rels = [r for r in dict.fromkeys(rels) if kind_of(r) and not any(skip_dir(p, skip_eagle) for p in r.split("/")[:-1])]
    app_dirs: dict[str, bool] = {}

    def in_app(rel: str) -> bool:  # inside the portable Gallery kept in a library (its own icons, an update being unpacked)
        parts = rel.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            d = "/".join(parts[:i])
            if d not in app_dirs:
                app_dirs[d] = os.path.isfile(os.path.join(root, *parts[:i], APP_EXE))
            if app_dirs[d]:
                return True
        return False
    rels = [r for r in rels if not in_app(r)]
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
                if rel in known and known[rel]["missing"] in (0, PAIRED):
                    gone.append((known[rel]["id"], rel))
                continue
            counts["seen"] += 1
            try:
                _visit(db, fid, full, rel, st.st_size, st.st_mtime, known.get(rel), now, touched, pending, counts)
            except OSError:
                counts["errors"] += 1
        _flush(db, now, touched, pending)
        if gone:
            with db.write() as c:  # still gone, and not put in the Trash meanwhile (its move is what we saw)
                for g, rel in gone:
                    if not os.path.exists(os.path.join(root, *rel.split("/"))):
                        n = c.execute(f"UPDATE files SET missing=1 WHERE id=? AND missing IN (0, {PAIRED})", (g,)).rowcount
                        counts["missing"] += n
    with db.write() as c:
        pair_live(c, fid)
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
        if r["id"] in claimed or r["missing"] in (2, PAIRED):  # in the Trash, or a Live Photo's video: still there, not moved
            continue
        if r["missing"] or not os.path.exists(os.path.join(r["path"], r["rel"])):
            return r["id"]
    return None


def pair_live(c, folders=None) -> int:
    """Live Photos, in a photo library: a photo (HEIC or JPEG) with a short .MOV of the same name beside it is one
    item. The video is kept out of the grid (missing = PAIRED) and the photo points at it (files.motion), so it plays
    on hover and goes wherever the photo goes. Two photos of one name (IMG_1.HEIC and an exported IMG_1.JPG): the
    pair there is stays, else the HEIC. A video too big to be a Live Photo's, or any library that isn't for photos,
    keeps its .MOVs as videos of their own. Run after scans and file operations, for the folders they touched
    (None: all of them); -> how many pairs there are there now."""
    if folders is not None:
        folders = sorted({int(f) for f in ([folders] if isinstance(folders, int) else folders)})
        if not folders:
            return 0
    scope = f"AND folder_id IN ({','.join('?' * len(folders))})" if folders is not None else ""
    args = tuple(folders or ())
    kind = c.execute("SELECT value FROM library_meta WHERE key='kind'").fetchone()
    live = bool(kind) and "photos" in kind[0].split(",")
    movs = c.execute(f"SELECT id, folder_id, rel, missing, size FROM files WHERE ext = '.mov'"
                     f" AND missing IN (0, {PAIRED}) {scope}", args).fetchall()
    pairs: dict[int, int] = {}
    for m in movs if live else ():
        if (m["size"] or 0) > LIVE_MAX:
            continue
        stem = os.path.splitext(m["rel"])[0]
        names = [stem + e for x in LIVE_PHOTO for e in (x, x.upper())]
        rows = c.execute(f"SELECT id, ext, motion FROM files WHERE folder_id=? AND rel IN ({','.join('?' * len(names))}) AND missing=0",
                         (m["folder_id"], *names)).fetchall()
        rows = [r for r in rows if r["id"] not in pairs]
        if rows:
            best = min(rows, key=lambda r: (r["motion"] != m["id"], LIVE_PHOTO.index(r["ext"]) if r["ext"] in LIVE_PHOTO else 9, r["id"]))
            pairs[best["id"]] = m["id"]
    paired_movs = set(pairs.values())
    c.executemany(f"UPDATE files SET missing={PAIRED} WHERE id=?", [(m["id"],) for m in movs if m["id"] in paired_movs and m["missing"] != PAIRED])
    c.executemany("UPDATE files SET missing=0 WHERE id=?", [(m["id"],) for m in movs if m["id"] not in paired_movs and m["missing"] == PAIRED])
    # (a photo in the Trash keeps its video: it goes back, or to the Recycle Bin, with it)
    had = {r["id"]: r["motion"] for r in c.execute(f"SELECT id, motion FROM files WHERE motion IS NOT NULL AND missing=0 {scope}", args)}
    c.executemany("UPDATE files SET motion=? WHERE id=?", [(v, k) for k, v in pairs.items() if had.get(k) != v])
    c.executemany("UPDATE files SET motion=NULL WHERE id=?", [(k,) for k in had if k not in pairs])
    return len(pairs)
