"""Working with ComfyUI and Model Librarian: open a file's workflow in ComfyUI, save what made it as a recipe.

* Open in ComfyUI sends the workflow to Model Librarian's route in a running ComfyUI (the address is in
  Settings); Model Librarian opens it in a new tab there. Nothing is queued.
* Save as recipe writes the recipe (Model Librarian's recipe/1 format) and a 512 px picture into Model
  Librarian's recipe folder (recipes.py), where its Recipes tab finds it.
* Open in Model Librarian sends a recipe to the same route (Model Librarian 1.29.0 or newer), which opens it in its
  Recipes tab there, to download what's missing and fill the workflow.
"""
from __future__ import annotations

import os
import re

from .meta import recipe_parse


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


def save_recipe(ml_data: str, path: str, kind: str, name: str, title: str | None = None, ffmpeg: str | None = None) -> dict:
    """Write it into Model Librarian's recipes folder (a new id, never replacing one there)."""
    from . import recipes
    if not ml_data or not os.path.isdir(ml_data):
        raise ComfyError("Model Librarian's data folder wasn't found: set it in Settings.")
    try:
        rec, thumb = recipes.from_file(path, kind, name, ffmpeg)
    except recipe_parse.NoMetadata as ex:
        raise ComfyError(str(ex))
    rec["source"]["site"] = rec["source"].get("site") or "Model Librarian Gallery"
    if title:
        rec["title"] = title
    r = recipes.save(ml_data, rec, thumb)
    return {"id": r["id"], "title": r["title"], "folder": os.path.join(ml_data, "recipes")}


async def open_recipe(url: str, recipe: dict, thumb: bytes | None) -> dict:
    """Into Model Librarian's Recipes tab in ComfyUI (Model Librarian 1.29.0 or newer), to download what's missing
    and fill the workflow."""
    import aiohttp
    import binascii
    base = (url or "").rstrip("/")
    if not re.match(r"^https?://[^/\s]+", base):
        raise ComfyError("Set ComfyUI's address in Settings first (e.g. http://127.0.0.1:8188).")
    body = {"name": recipe.get("title") or "Recipe", "recipe": recipe, "recipe_id": recipe.get("id")}  # (saved there already: that one opens)
    if thumb:
        body["thumb"] = "data:image/webp;base64," + binascii.b2a_base64(thumb, newline=False).decode()
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.post(base + "/sm-fetcher/gallery/open", json=body) as r:
                data = await r.json(content_type=None) if r.status != 404 else None
                if r.status == 404 or (r.status == 400 and "recipe" not in str((data or {}).get("error") or "").lower()):
                    raise ComfyError("ComfyUI is running there, but its Model Librarian is older than 1.29.0 (it opens recipes from the Gallery): update Model Librarian.")
                if r.status >= 400:
                    raise ComfyError((data or {}).get("error") or f"ComfyUI said {r.status}")
    except aiohttp.ClientConnectorError:
        raise ComfyError(f"ComfyUI isn't running at {base} (or the address in Settings is wrong).")
    except (aiohttp.ClientError, TimeoutError) as ex:
        raise ComfyError(f"Couldn't reach ComfyUI at {base}: {ex}")
    if not (data or {}).get("clients"):
        raise ComfyError("ComfyUI is running, but not open in a browser: open it, then try again.")
    return {"clients": data["clients"]}
