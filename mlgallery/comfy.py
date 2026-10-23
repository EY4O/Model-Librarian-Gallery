"""Working with ComfyUI and Model Librarian: open a file's workflow in ComfyUI, save what made it as a recipe.

* Open in ComfyUI sends the workflow to Model Librarian's route in a running ComfyUI (the address is in
  Settings); Model Librarian opens it in a new tab there. Nothing is queued.
* Save as recipe writes the recipe (Model Librarian's recipe/1 format) and a 512 px picture into Model
  Librarian's recipe folder, where its Recipes tab finds it.
"""
from __future__ import annotations

import io
import json
import os
import re
import tempfile
import time
import uuid

from .meta import recipe_parse

THUMB_PX = 512


class ComfyError(Exception):
    pass


async def open_in_comfy(url: str, workflow: dict | None, prompt: dict | None, name: str) -> dict:
    import aiohttp
    base = (url or "").rstrip("/")
    if not re.match(r"^https?://[^/\s]+", base):
        raise ComfyError("Set ComfyUI's address in Settings first (e.g. http://127.0.0.1:8188).")
    body = {"name": name}
    if workflow and isinstance(workflow.get("nodes"), list):
        body["workflow"] = workflow
    elif prompt:
        body["prompt"] = prompt
    else:
        raise ComfyError("This file has no workflow to open.")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.post(base + "/sm-fetcher/gallery/open", json=body) as r:
                if r.status == 404:
                    raise ComfyError("ComfyUI is running there, but without Model Librarian 1.23 or newer (it opens the workflow): update Model Librarian.")
                data = await r.json(content_type=None)
                if r.status >= 400:
                    raise ComfyError((data or {}).get("error") or f"ComfyUI said {r.status}")
    except aiohttp.ClientConnectorError:
        raise ComfyError(f"ComfyUI isn't running at {base} (or the address in Settings is wrong).")
    except (aiohttp.ClientError, TimeoutError) as ex:
        raise ComfyError(f"Couldn't reach ComfyUI at {base}: {ex}")
    if not data.get("clients"):
        raise ComfyError("ComfyUI is running, but not open in a browser: open it, then try again.")
    return {"clients": data["clients"]}


def recipe_from_file(path: str, kind: str, name: str) -> dict:
    """The recipe for one file, as Model Librarian reads it."""
    if kind == "video":
        rec = recipe_parse.from_video(path, name)
    else:
        with open(path, "rb") as f:
            rec = recipe_parse.from_image(f.read(), name)
    rec["source"]["site"] = rec["source"].get("site") or "Model Librarian Gallery"
    return recipe_parse.clean(rec)


def save_recipe(ml_data: str, path: str, kind: str, name: str, title: str | None = None) -> dict:
    """Write it into Model Librarian's recipes folder (a new id, never replacing one there)."""
    if not ml_data or not os.path.isdir(ml_data):
        raise ComfyError("Model Librarian's data folder wasn't found: set it in Settings.")
    try:
        rec = recipe_from_file(path, kind, name)
    except recipe_parse.NoMetadata as ex:
        raise ComfyError(str(ex))
    folder = os.path.join(ml_data, "recipes")
    os.makedirs(folder, exist_ok=True)
    while True:
        rid = uuid.uuid4().hex[:12]
        if not os.path.exists(os.path.join(folder, rid + ".json")):
            break
    rec["id"] = rid
    rec["title"] = (title or rec.get("title") or os.path.splitext(name)[0])[:200]
    rec["created"] = time.time()
    rec["schema"] = rec.get("schema") or "recipe/1"
    thumb = _thumb(path, kind)
    if thumb:
        _write(os.path.join(folder, rid + ".webp"), thumb)
    _write(os.path.join(folder, rid + ".json"), json.dumps(rec, ensure_ascii=False, indent=1).encode("utf-8"))
    return {"id": rid, "title": rec["title"], "folder": folder}


def _thumb(path: str, kind: str) -> bytes | None:
    try:
        from PIL import Image
        if kind == "video":
            from .thumbs import find_ffmpeg, video_thumb
            ff = find_ffmpeg()
            return video_thumb(ff, path, THUMB_PX) if ff else None
        with Image.open(path) as im:
            im.thumbnail((THUMB_PX, THUMB_PX))
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
            out = io.BytesIO()
            im.save(out, "WEBP", quality=82)
            return out.getvalue()
    except Exception:
        return None


def _write(p: str, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=os.path.basename(p) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
