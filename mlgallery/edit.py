"""Light edits from the viewer: flip, rotate, crop.

What a file carries is kept: PNG text chunks (ComfyUI's prompt and workflow, A1111's parameters), EXIF and XMP
(where ComfyUI's WebP and JPEG saves put theirs), the colour profile - and so is its date, so it stays where it
is in the grid. Saving over the file keeps the original in the folder's .mlg-trash/edits, so Undo puts it back;
"Save as copy" writes "name (edited).ext" beside it and leaves the original alone.
"""
from __future__ import annotations

import os
import shutil
import time

from PIL import Image, ImageOps
from PIL.PngImagePlugin import PngInfo

from . import fileops, scan
from .fileops import OpError

EDITABLE = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif", ".tif", ".tiff"}
TIFF_KEEP = (0x010E, 0x010F, 0x0110, 0x0131, 0x0132, 0x013B, 0x8298)  # description, make, model, software, date, artist, copyright
ROTATE = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_90}  # clockwise, as shown


def _edited(src: str, rotate: int, flip: bool, crop, of=None) -> tuple[Image.Image, dict, str]:
    """The picture as the viewer showed it (EXIF orientation applied), flipped, then turned clockwise, then cropped.
    `of`: the size the crop was measured on (the viewer's picture, turned): scaled to the real one, so a crop
    made on a preview (HEIC, TIFF) or on a picture saved smaller meanwhile lands in the same place."""
    with Image.open(src) as im:
        if getattr(im, "n_frames", 1) > 1:
            raise OpError("Animated pictures can't be edited here.")
        info, fmt = dict(im.info), im.format
        exif = im.getexif()
        out = ImageOps.exif_transpose(im)
        if flip:
            out = ImageOps.mirror(out)
        if rotate:
            out = out.transpose(ROTATE[rotate])
        if crop:
            crop = [float(v) for v in crop]
            if of and float(of[0]) > 0 and float(of[1]) > 0:
                sx, sy = out.width / float(of[0]), out.height / float(of[1])
                crop = [crop[0] * sx, crop[1] * sy, crop[2] * sx, crop[3] * sy]
            x, y, w, h = (int(round(v)) for v in crop)
            x, y = max(0, min(x, out.width - 1)), max(0, min(y, out.height - 1))
            w, h = max(1, min(w, out.width - x)), max(1, min(h, out.height - y))
            out = out.crop((x, y, x + w, y + h))
        out.load()
    if 0x0112 in exif:
        exif[0x0112] = 1  # turned already: the orientation tag would turn it again
    info["exif_obj"] = exif
    return out, info, fmt


def _save(im: Image.Image, info: dict, fmt: str, dest: str) -> None:
    exif = info["exif_obj"]
    extra = {"icc_profile": info["icc_profile"]} if info.get("icc_profile") else {}
    if fmt == "PNG":
        meta = PngInfo()
        for k, v in info.items():
            if isinstance(v, str) and k not in ("exif_obj",):
                meta.add_text(k, v)  # tEXt, or iTXt for text outside Latin-1
        im.save(dest, "PNG", pnginfo=meta, exif=exif.tobytes() if len(exif) else None, **extra)
    elif fmt == "JPEG":
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        im.save(dest, "JPEG", quality=95, subsampling=0, exif=exif.tobytes() if len(exif) else b"", **extra)
    elif fmt == "WEBP":
        kw = {"exif": exif.tobytes()} if len(exif) else {}
        if info.get("xmp"):
            kw["xmp"] = info["xmp"]
        im.save(dest, "WEBP", quality=95, method=4, **kw, **extra)
    elif fmt == "HEIF":  # an iPhone photo stays one (pillow-heif)
        kw = {"exif": exif.tobytes()} if len(exif) else {}
        if info.get("xmp"):
            kw["xmp"] = info["xmp"]
        im.save(dest, "HEIF", quality=95, **kw, **extra)
    elif fmt == "TIFF":  # a scan stays lossless; its own layout tags (size, strips) are the new picture's, only EXIF carries over
        kept = Image.Exif()
        for tag in TIFF_KEEP:
            if tag in exif:
                kept[tag] = exif[tag]
        sub = exif.get_ifd(0x8769)
        if sub:
            kept[0x8769] = dict(sub)
        # libtiff (which compresses) can't write the EXIF block that holds the date taken: with one, uncompressed
        comp = info.get("compression") if not sub and info.get("compression") not in (None, "raw") else None
        im.save(dest, "TIFF", **({"compression": comp} if comp else {}), **({"exif": kept.tobytes()} if len(kept) else {}), **extra)
    else:
        raise OpError("Only PNG, JPEG, WebP, HEIC and TIFF pictures can be edited here.")


def _refresh(db, fid: int, path: str) -> None:
    """The index follows the new bytes: a new fingerprint, so a new thumbnail; metadata read again."""
    size = os.path.getsize(path)
    with Image.open(path) as im:
        w, h = im.size
    with db.write() as c:
        c.execute("UPDATE files SET size=?, fp=?, width=?, height=?, thumb=0, meta=0, phash=NULL, colors=0 WHERE id=?",
                  (size, scan.fingerprint(path, size), w, h, fid))
        c.execute("DELETE FROM colors WHERE file_id=?", (fid,))  # cropped: its colours are worked out again


def apply(db, fid: int, rotate=0, flip=False, crop=None, copy=False, of=None) -> dict:
    rotate = int(rotate or 0) % 360
    if rotate % 90:
        raise OpError("Turns go in quarters.")
    if crop is not None and (not isinstance(crop, (list, tuple)) or len(crop) != 4):
        raise OpError("A crop is [x, y, width, height].")
    if not rotate and not flip and not crop:
        raise OpError("Nothing to save: flip, rotate or crop it first.")
    with db.read() as c:
        r = fileops._row(c, int(fid))
    if not r or r["missing"] or not r["online"]:
        raise OpError("That file isn't reachable right now.")
    src = fileops._full(r["root"], r["rel"])
    if os.path.splitext(src)[1].lower() not in EDITABLE:
        raise OpError("Only PNG, JPEG, WebP, HEIC and TIFF pictures can be edited here.")
    st = os.stat(src)
    if of is not None and (not isinstance(of, (list, tuple)) or len(of) != 2):
        raise OpError("A crop's size is [width, height].")
    im, info, fmt = _edited(src, rotate, bool(flip), crop, of)
    folder = {"id": r["folder_id"], "path": r["root"]}
    if copy:
        parent = "/".join(r["rel"].split("/")[:-1])
        stem, ext = os.path.splitext(r["name"])
        with db.read() as c:
            name = fileops._free_name(os.path.dirname(src), f"{stem} (edited){ext}", set(), fileops._held(c, r["folder_id"], parent))
        dest = os.path.join(os.path.dirname(src), name)
        _save(im, info, fmt, dest + ".mlg-edit")
        os.replace(dest + ".mlg-edit", dest)
        os.utime(dest, (st.st_atime, st.st_mtime))  # beside the original in the grid
        rel = f"{parent}/{name}" if parent else name
        scan.index_files(db, folder, [rel])
        with db.write() as c:
            new = c.execute("SELECT id FROM files WHERE folder_id=? AND rel=?", (r["folder_id"], rel)).fetchone()
            op = fileops.record(c, "edit", f"Saved an edited copy of {r['name']}", {"copy": new[0] if new else None})
        return {"op": op, "id": new[0] if new else None, "name": name}
    stamp = time.strftime('%Y%m%d-%H%M%S')
    for n in range(1, 1000):  # (a second edit within the same second: its own folder, not the first one's original overwritten)
        backup_rel = f"{fileops.TRASH}/edits/{stamp}{'' if n == 1 else f'-{n}'}/{r['rel']}"
        backup = fileops._full(r["root"], backup_rel)
        if not os.path.exists(backup):
            break
    os.makedirs(os.path.dirname(backup), exist_ok=True)
    shutil.copy2(src, backup)
    _save(im, info, fmt, src + ".mlg-edit")
    os.replace(src + ".mlg-edit", src)
    os.utime(src, (st.st_atime, st.st_mtime))
    _refresh(db, r["id"], src)
    with db.write() as c:
        op = fileops.record(c, "edit", f"Edited {r['name']}", {"id": r["id"], "rel": r["rel"], "backup_rel": backup_rel})
    return {"op": op, "id": r["id"], "name": r["name"]}


def undo(db, data: dict) -> dict:
    if data.get("copy") is not None:  # the copy goes to the Trash
        res = fileops.trash(db, [data["copy"]])
        if res["op"]:
            with db.write() as c:
                c.execute("DELETE FROM ops WHERE id=?", (res["op"],))  # the undo itself isn't a new step
        return {"undone": res["trashed"], "failed": [f["id"] for f in res["failed"]]}
    with db.read() as c:
        r = fileops._row(c, data["id"])
    if not r or r["rel"] != data["rel"] or r["missing"]:
        return {"undone": 0, "failed": [data["id"]]}  # moved or deleted since: left alone
    src, backup = fileops._full(r["root"], r["rel"]), fileops._full(r["root"], data["backup_rel"])
    if not os.path.isfile(backup):
        raise OpError("The original isn't kept any more, so this edit can't be undone.")
    st = os.stat(src)
    os.replace(backup, src)
    os.utime(src, (st.st_atime, st.st_mtime))
    fileops._tidy_trash(r["root"], data["backup_rel"])
    _refresh(db, r["id"], src)
    return {"undone": 1, "failed": []}
