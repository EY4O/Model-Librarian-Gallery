"""Model Librarian's recipes, read and written where Model Librarian keeps them (Settings, Recipes).

A recipe is what made a picture, in the shared recipe/1 format (its prompt, settings, checkpoint and LoRAs with their
strengths, and the ComfyUI workflow when there is one). Model Librarian keeps each one as `<id>.json` with an optional
512 px picture `<id>.webp` in `recipes/` inside its shared data folder, so the Gallery's Recipes screen and Model
Librarian's Recipes tab in ComfyUI are one list. This works on those files as Model Librarian's smf/recipe_store.py
does: a new recipe always gets a new id (one is never replaced), and deleting moves its files into `recipes/_deleted/`.

Recipes come in from a picture or video (its generation data), a recipe file (recipe/1, or LoRA Manager's), a link (a
CivitAI image or post, or any public image), and from the Chrome extension: with ComfyUI not running it leaves the
CivitAI link as `<name>.mlrecipe.json` in Downloads\\Model Librarian Gallery, and while the Gallery is open with
Recipes on, it reads that link and saves the recipe here (pickup / loop).
"""
from __future__ import annotations

import asyncio
import binascii
import io
import ipaddress
import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from urllib.parse import urlparse

from .config import write_atomic
from .meta import recipe_parse

ID = re.compile(r"^[a-f0-9]{12}$")
THUMB_PX = 512
MAX_IMAGE = 40 * 1024 * 1024      # a picture fetched or dropped in (as Model Librarian's limit)
MAX_JSON = 8 * 1024 * 1024        # a recipe file (a big ComfyUI workflow fits easily)
SUFFIX = ".mlrecipe.json"         # what the Chrome extension leaves for the Gallery
NOTE_MAX = 600_000                # ... a link and perhaps a small picture: anything much bigger isn't one
EVERY = 20                        # seconds between looks for them
EDITABLE = ("title", "tags", "notes", "fav")
CIVITAI_HOSTS = ("civitai.com", "civitai.green", "civitai.red")


class RecipeError(Exception):
    """Said to you as it is."""


def civitai_base() -> str:
    return (os.environ.get("MLG_CIVITAI") or "https://civitai.com").rstrip("/")  # (the tests stand in for it)


# ---------------------------------------------------------------- the store
def folder(ml_dir: str | None, make: bool = False) -> str:
    if not ml_dir or not os.path.isdir(ml_dir):
        raise RecipeError("Model Librarian's data folder wasn't found: set it in Settings, Model Librarian and ComfyUI.")
    d = os.path.join(ml_dir, "recipes")
    if make:
        os.makedirs(d, exist_ok=True)
    return d


def _path(ml_dir: str, rid: str, ext: str = ".json") -> str:
    if not ID.match(str(rid or "")):
        raise RecipeError("There's no such recipe.")
    return os.path.join(folder(ml_dir), rid + ext)


def make_thumbnail(data: bytes | None) -> bytes | None:
    """Any picture -> a 512 px WebP (None if it can't be read)."""
    if not data:
        return None
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            im.thumbnail((THUMB_PX, THUMB_PX))
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
            out = io.BytesIO()
            im.save(out, "WEBP", quality=82)
            return out.getvalue()
    except Exception:
        return None


def thumb_from_data_url(s) -> bytes | None:
    if not isinstance(s, str) or not s.startswith("data:image/") or "," not in s:
        return None
    try:
        return make_thumbnail(binascii.a2b_base64(s.split(",", 1)[1]))
    except (ValueError, binascii.Error):
        return None


def _read(p: str) -> dict | None:
    try:
        with open(p, encoding="utf-8") as f:
            r = json.load(f, parse_constant=lambda _c: None)
        return r if isinstance(r, dict) else None
    except (OSError, ValueError):
        return None


def _name(ref) -> str | None:
    if not isinstance(ref, dict):
        return None
    return ref.get("display_name") or ref.get("name") or recipe_parse._stem(ref.get("file"))


def summary(ml_dir: str, r: dict) -> dict:
    """What a card shows, and what the list is searched by."""
    rid = r.get("id")
    try:
        st = os.stat(_path(ml_dir, rid, ".webp"))
        thumb_v = st.st_mtime_ns // 1_000_000
    except (OSError, RecipeError):
        thumb_v = 0
    loras = [l for l in r.get("loras") or [] if isinstance(l, dict)]
    return {
        "id": rid, "title": r.get("title") or "Untitled recipe", "created": r.get("created"), "format": r.get("format"),
        "base_model": r.get("base_model"), "source_url": (r.get("source") or {}).get("url"),
        "model": _name(r.get("model")), "loras": [n for n in (_name(l) for l in loras) if n],
        "prompt": str((r.get("prompt") or {}).get("positive") or "")[:400],
        "tags": [str(t) for t in r.get("tags") or []], "fav": bool(r.get("fav")), "media": r.get("media") or "image",
        "workflow": bool(isinstance(r.get("comfy"), dict) and (r["comfy"].get("workflow") or r["comfy"].get("prompt"))),
        "thumb_v": thumb_v,
    }


def list_all(ml_dir: str) -> list[dict]:
    d = folder(ml_dir)
    if not os.path.isdir(d):
        return []
    out = []
    for fn in os.listdir(d):
        if fn.endswith(".json") and ID.match(fn[:-5]):
            r = _read(os.path.join(d, fn))
            if r is not None:
                out.append(summary(ml_dir, {**r, "id": fn[:-5]}))
    return sorted(out, key=lambda r: r.get("created") or 0, reverse=True)


def count(ml_dir: str | None) -> int:
    try:
        return sum(1 for fn in os.listdir(folder(ml_dir)) if fn.endswith(".json") and ID.match(fn[:-5]))
    except (OSError, RecipeError):
        return 0


def get(ml_dir: str, rid: str) -> dict | None:
    r = _read(_path(ml_dir, rid))
    return {**recipe_parse.clean(r), "id": rid} if r is not None else None


def _dump(r: dict) -> bytes:
    return json.dumps(r, ensure_ascii=False, indent=1).encode("utf-8")


def save(ml_dir: str, recipe: dict, thumb: bytes | None = None) -> dict:
    """A new recipe, under an id of its own: one that comes in carrying an id never replaces the recipe saved under it."""
    d = folder(ml_dir, make=True)
    r = recipe_parse.clean(dict(recipe))
    while True:
        rid = uuid.uuid4().hex[:12]
        if not os.path.exists(os.path.join(d, rid + ".json")):
            break
    r["id"] = rid
    r.pop("image", None)  # (a full-size copy kept with another recipe isn't this one's)
    r["title"] = str(r.get("title") or "Untitled recipe")[:200]
    r["created"] = r.get("created") if isinstance(r.get("created"), (int, float)) else time.time()
    r["schema"] = r.get("schema") or recipe_parse.SCHEMA
    if thumb:
        write_atomic(os.path.join(d, rid + ".webp"), thumb)  # (the picture first: the list never names a recipe without it)
    write_atomic(os.path.join(d, rid + ".json"), _dump(r))
    return r


def update(ml_dir: str, rid: str, patch: dict) -> dict | None:
    r = get(ml_dir, rid)
    if r is None:
        return None
    for k in EDITABLE:
        if k not in patch:
            continue
        v = patch[k]
        if k == "tags":
            v = list(dict.fromkeys(str(t).strip() for t in (v or []) if str(t).strip()))[:50]
        elif k == "fav":
            v = bool(v)
        elif k == "title":
            v = str(v or "").strip()[:200]
            if not v:
                raise RecipeError("Give it a name.")
        else:
            v = str(v or "")[:2000]
        r[k] = v
    r["updated"] = time.time()
    write_atomic(_path(ml_dir, rid), _dump(r))
    return r


def delete(ml_dir: str, rid: str) -> bool:
    """Into recipes/_deleted, as Model Librarian deletes one (it can be put back by hand)."""
    p = _path(ml_dir, rid)
    if not os.path.isfile(p):
        return False
    trash = os.path.join(folder(ml_dir), "_deleted")
    os.makedirs(trash, exist_ok=True)
    for ext in (".json", ".webp", ".png", ".jpg", ".jpeg"):
        src = _path(ml_dir, rid, ext)
        if os.path.isfile(src):
            dst = os.path.join(trash, rid + ext)
            if os.path.exists(dst):
                dst = os.path.join(trash, f"{rid}-{time.time_ns()}{ext}")
            shutil.move(src, dst)
    return True


def thumb_path(ml_dir: str, rid: str) -> str | None:
    p = _path(ml_dir, rid, ".webp")
    return p if os.path.isfile(p) else None


# ---------------------------------------------------------------- reading recipes from things
def _useful(rec: dict) -> bool:
    return bool(rec.get("model") or rec.get("loras") or (rec.get("prompt") or {}).get("positive"))


def from_media(data: bytes, filename: str, ffmpeg: str | None = None) -> tuple[dict, bytes | None]:
    """A picture or video's own generation data -> (recipe, picture). recipe_parse.NoMetadata says what's missing."""
    from .meta import videometa
    if len(data) > MAX_IMAGE * 4:
        raise recipe_parse.NoMetadata("That file is too big to read a recipe from.")
    if videometa.is_video(filename, None):
        fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(filename)[1] or ".mp4")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            rec = recipe_parse.from_video(tmp, filename)
            thumb = None
            if ffmpeg:
                from .thumbs import video_thumb
                try:
                    thumb = video_thumb(ffmpeg, tmp, THUMB_PX)
                except Exception:
                    thumb = None
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    else:
        rec = recipe_parse.from_image(data, filename)
        thumb = make_thumbnail(data)
    if not _useful(rec):
        raise recipe_parse.NoMetadata(f"{filename} has no prompt or models in it.")
    return recipe_parse.clean(rec), thumb


def from_file(path: str, kind: str, name: str, ffmpeg: str | None = None) -> tuple[dict, bytes | None]:
    """A file in the library -> (recipe, picture), read where it is."""
    if kind == "video":
        rec = recipe_parse.from_video(path, name)
        thumb = None
        if ffmpeg:
            from .thumbs import video_thumb
            try:
                thumb = video_thumb(ffmpeg, path, THUMB_PX)
            except Exception:
                thumb = None
    else:
        with open(path, "rb") as f:
            data = f.read()
        rec = recipe_parse.from_image(data, name)
        thumb = make_thumbnail(data)
    rec["title"] = rec.get("title") or os.path.splitext(name)[0]
    return recipe_parse.clean(rec), thumb


def _is_lm(d: dict) -> bool:
    return isinstance(d.get("gen_params"), dict) or ("file_path" in d and isinstance(d.get("loras"), list) and "schema" not in d)


def from_json(data: bytes, filename: str = "") -> list[tuple[dict, bytes | None]]:
    """A recipe file -> [(recipe, picture)]: recipe/1 (one, a list of them, or {recipe, thumb} as the extension and
    share cards send it), or a LoRA Manager recipe."""
    if len(data) > MAX_JSON:
        raise recipe_parse.NoMetadata(f"{filename or 'That file'} is too big to be a recipe.")
    try:
        d = recipe_parse._loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise recipe_parse.NoMetadata(f"{filename or 'That file'} isn't a recipe file (it isn't JSON).")
    items = d if isinstance(d, list) else [d]
    out = []
    for it in items[:500]:
        if not isinstance(it, dict):
            continue
        thumb = None
        if isinstance(it.get("recipe"), dict):
            thumb = thumb_from_data_url(it.get("thumb"))
            it = it["recipe"]
        if _is_lm(it):
            rec = recipe_parse.from_lm_recipe(it)
            rec["lm_id"] = None  # (a copy: not the recipe LoRA Manager has)
        elif isinstance(it.get("prompt"), dict) or str(it.get("schema") or "").startswith("recipe/"):
            rec = recipe_parse.normalize(it)
        else:
            continue
        if not rec.get("title") and filename:
            rec["title"] = re.sub(r"\.[^.]+$", "", os.path.basename(filename))
        if _useful(rec):
            out.append((rec, thumb))
    if not out:
        raise recipe_parse.NoMetadata(f"{filename or 'That file'} has no recipe in it (recipe/1 or LoRA Manager's).")
    return out


# ---------------------------------------------------------------- links
def public_https(url: str) -> bool:
    """Only public https links are fetched (the Gallery fetches them, so nothing on this PC or the home network)."""
    u = urlparse(url or "")
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not host or host == "localhost" or host.endswith((".local", ".localhost", ".internal", ".lan", ".home")):
        return False
    try:
        ip = ipaddress.ip_address(host)
        return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast)
    except ValueError:
        return True  # (a name)


def civitai_still(url: str, width: int = 512) -> str:
    """CivitAI's link to a still of it (a video's first frame)."""
    return re.sub(r"/(?:original=true|width=\d+|anim=false[^/]*|transcode=true[^/]*)/", f"/anim=false,width={width}/", url or "")


def _session():
    import aiohttp
    from . import VERSION
    return aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30), headers={"User-Agent": f"ModelLibrarianGallery/{VERSION}"})


async def _get_json(s, url: str):
    async with s.get(url) as r:
        if r.status >= 400:
            raise RecipeError(f"CivitAI said {r.status}.")
        return await r.json(content_type=None)


async def fetch_image(s, url: str) -> bytes | None:
    """A public https picture, whole. Each redirect is checked too (a public link mustn't lead to this PC)."""
    import yarl
    trusted = os.environ.get("MLG_CIVITAI") and url.startswith(civitai_base())  # (the tests' stand-in, on this PC)
    try:
        for _hop in range(6):
            async with s.get(url, allow_redirects=False) as r:
                if r.status in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                    url = str(r.url.join(yarl.URL(r.headers["Location"])))
                    if not (public_https(url) or trusted):
                        return None
                    continue
                if r.status != 200 or not r.headers.get("Content-Type", "").startswith("image"):
                    return None
                data = bytearray()
                async for chunk in r.content.iter_chunked(1 << 16):
                    data += chunk
                    if len(data) > MAX_IMAGE:
                        return None
                if r.content_length is not None and not r.headers.get("Content-Encoding") and len(data) != r.content_length:
                    return None  # (cut off)
                return bytes(data)
    except Exception:
        return None
    return None


async def _civitai_images(s, query: str) -> list[dict]:
    items = []
    for extra in ("", "&nsfw=X"):  # (higher content levels only come back when asked for)
        d = await _get_json(s, f"{civitai_base()}/api/v1/images?{query}&withMeta=true{extra}")
        items = (d or {}).get("items") or []
        if items:
            break
    return items


async def from_url(url: str, ffmpeg: str | None = None) -> dict:
    """A link -> {"recipe", "thumb"} for one picture, or {"choices": [{url, thumb, has_meta}]} for a CivitAI post.
    recipe_parse.NoMetadata says what's wrong with the link; RecipeError when the site didn't answer."""
    url = (url or "").strip()
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if not public_https(url):
        raise recipe_parse.NoMetadata("Paste a public https:// link: a CivitAI image or post, or a picture.")
    import aiohttp
    try:
        async with _session() as s:
            if host in CIVITAI_HOSTS:
                m = re.match(r"^/images/(\d+)", u.path)
                if m:
                    items = await _civitai_images(s, f"imageId={m.group(1)}")
                    if not items:
                        raise recipe_parse.NoMetadata("CivitAI has no public image with that id.")
                    item = items[0]
                    rec = recipe_parse.from_civitai_item(item, f"https://{host}/images/{item.get('id')}")
                    if not _useful(rec):
                        raise recipe_parse.NoMetadata("CivitAI has no generation data for this image.")
                    if item.get("type") == "video":
                        rec["media"] = "video"
                    img = await fetch_image(s, civitai_still(item.get("url"))) if item.get("url") else None
                    return {"recipe": recipe_parse.clean(rec), "thumb": make_thumbnail(img)}
                m = re.match(r"^/posts/(\d+)", u.path)
                if m:
                    items = await _civitai_images(s, f"postId={m.group(1)}&limit=50")
                    if not items:
                        raise recipe_parse.NoMetadata("CivitAI has no public images in that post.")
                    return {"choices": [{"url": f"https://{host}/images/{i['id']}", "thumb": civitai_still(i.get("url"), 256),
                                         "has_meta": bool(i.get("meta"))} for i in items if i.get("id")]}
                raise recipe_parse.NoMetadata("Paste a CivitAI image (/images/…) or post (/posts/…) link.")
            data = await fetch_image(s, url)
    except (aiohttp.ClientError, asyncio.TimeoutError) as ex:
        raise RecipeError(f"Couldn't reach {host}: {ex or 'it took too long'}.")
    if not data:
        raise recipe_parse.NoMetadata("That link isn't a picture. Paste a picture's own link, or a CivitAI image or post.")
    name = os.path.basename(u.path) or "picture"
    rec, thumb = await asyncio.to_thread(from_media, data, name, ffmpeg)
    rec["source"]["url"] = url
    return {"recipe": rec, "thumb": thumb}


# ---------------------------------------------------------------- left by the Chrome extension
def waiting(folder_: str | None) -> list[str]:
    if not folder_ or not os.path.isdir(folder_):
        return []
    try:
        found = [e for e in os.scandir(folder_) if e.name.lower().endswith(SUFFIX) and e.is_file(follow_symlinks=False)]
    except OSError:
        return []
    return [e.path for e in sorted(found, key=lambda e: e.stat().st_mtime)]


def read_note(path: str) -> dict | None:
    """{url, page, image, recipe, thumb} from one file the extension left, or None when it isn't one."""
    try:
        if os.path.getsize(path) > NOTE_MAX:
            return None
        with open(path, encoding="utf-8-sig") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("v") != 1:
        return None
    url = str(d.get("url") or "")
    rec = d.get("recipe") if isinstance(d.get("recipe"), dict) else None
    if not url and not rec:
        return None
    return {"url": url, "page": str(d.get("page") or ""), "image": str(d.get("image") or ""), "recipe": rec, "thumb": d.get("thumb")}


_lock = threading.Lock()   # (one pickup at a time in this Gallery)
_failed: dict = {}         # file -> when it last couldn't be read: tried again after a while, not every look


async def _from_note(note: dict, ffmpeg: str | None) -> tuple[dict, bytes | None]:
    if note["recipe"]:
        rec = recipe_parse.normalize(note["recipe"])
        if not _useful(rec):
            raise recipe_parse.NoMetadata("no recipe in it")
        return rec, thumb_from_data_url(note["thumb"])
    got = await from_url(note["url"], ffmpeg)
    if "choices" in got:
        raise recipe_parse.NoMetadata("a post, not one picture")
    return got["recipe"], got["thumb"]


def pickup(ml_dir: str | None, folder_: str | None, ffmpeg: str | None = None, retry_after: float = 600) -> list[dict]:
    """Saves each recipe the extension left into Model Librarian's recipes and removes its file -> the recipes saved.
    A link that can't be read now (CivitAI down) is tried again later; one that will never be a recipe is removed."""
    if not ml_dir or not waiting(folder_):
        return []
    saved = []
    with _lock:
        for p in waiting(folder_):
            if time.time() - _failed.get(p, 0) < retry_after:
                continue
            note = read_note(p)
            gone = note is None
            if note:
                try:
                    rec, thumb = asyncio.run(_from_note(note, ffmpeg))
                    if note["page"] and not rec["source"].get("url"):
                        rec["source"]["url"] = note["page"]
                    saved.append(save(ml_dir, rec, thumb))
                    gone = True
                except recipe_parse.NoMetadata:
                    gone = True  # (it never will be one)
                except Exception:
                    _failed[p] = time.time()
            if gone:
                try:
                    os.remove(p)
                    _failed.pop(p, None)
                except OSError:
                    _failed[p] = time.time()
    return saved


def on(lib) -> bool:
    return bool(lib.settings.get("recipes"))


def loop(lib, stop: threading.Event, every: float = EVERY) -> None:
    """While the Gallery is open with Recipes on: the extension's recipes, saved as they arrive (off, they wait)."""
    from .charfiles import folders
    from .thumbs import find_ffmpeg
    while not stop.wait(every):
        if not on(lib):
            continue
        ml = lib.models.data_dir()
        for f in folders(lib):
            try:
                pickup(ml, f, find_ffmpeg(lib.settings.get("ffmpeg")))
            except Exception:
                pass  # (next time)
