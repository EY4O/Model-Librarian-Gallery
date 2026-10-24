"""The Gallery's local web server: the interface and its API, on 127.0.0.1 only.

Every API request must carry this session's token (made fresh at each start), and the Host must be
127.0.0.1 or localhost: a web page you visit can't reach the Gallery, even though it's a local server
(and it will move and delete files once you ask it to).
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import secrets
import sqlite3

from aiohttp import web

from . import APP_NAME, VERSION
from .library import Library
from .update import Updater

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")
mimetypes.init()  # first: a later init() would drop the types added below (and Windows' registry can map .webp oddly)
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("video/webm", ".webm")
mimetypes.add_type("video/mp4", ".mp4")
mimetypes.add_type("text/javascript", ".js")


LIB = web.AppKey("lib", Library)
TOKEN = web.AppKey("token", str)
PICK = web.AppKey("pick_folder", object)
UPDATER = web.AppKey("updater", Updater)
QUIT = web.AppKey("quit", object)
DAILY = web.AppKey("daily", object)


def _err(msg: str, status: int = 400) -> web.Response:
    return web.json_response({"error": msg}, status=status)


def make_app(lib: Library, token: str | None = None, *, pick_folder=None, quit=None) -> web.Application:
    """The aiohttp application. `pick_folder()`: the window's folder dialog, when there is one; `quit()` closes the window."""
    token = token or secrets.token_urlsafe(24)
    app = web.Application(middlewares=[_guard], client_max_size=4 * 1024 * 1024)
    app[LIB], app[TOKEN], app[PICK], app[QUIT] = lib, token, pick_folder, quit
    app[UPDATER] = Updater(lib.data_dir, lib.settings)
    app.on_startup.append(_start_daily)
    app.on_cleanup.append(_stop_daily)
    r = app.router
    r.add_get("/", index)
    r.add_static("/ui/", UI_DIR, show_index=False)
    r.add_get("/api/status", status)
    r.add_get("/api/folders", folders)
    r.add_post("/api/folders", add_folder)
    r.add_post("/api/folders/pick", pick)
    r.add_delete("/api/folders/{fid}", remove_folder)
    r.add_post("/api/folders/{fid}/scan", scan_folder)
    r.add_post("/api/folders/{fid}/look", folder_look)
    r.add_post("/api/albums/{aid}/look", album_look)
    r.add_get("/api/shapes", shapes)
    r.add_post("/api/scan", scan_all)
    r.add_get("/api/files", files)
    r.add_get("/api/days", days)
    r.add_post("/api/file/{id}/reveal", reveal)
    r.add_post("/api/folders/{fid}/reveal", reveal_folder)
    r.add_post("/api/file/{id}/edit", file_edit)
    r.add_get("/api/file/{id}/info", file_info)
    r.add_get("/api/file/{id}/workflow", file_workflow)
    r.add_post("/api/file/{id}/mark", file_mark)
    r.add_post("/api/file/{id}/tags", file_tags)
    r.add_post("/api/file/{id}/viewed", file_viewed)
    r.add_get("/api/suggest", suggest)
    r.add_get("/api/stats", stats_view)
    r.add_get("/api/characters", characters_list)
    r.add_post("/api/characters", character_create)
    r.add_get("/api/characters/review", review_queue)
    r.add_get("/api/characters/words", trigger_words)
    r.add_get("/api/characters/{cid}", character_get)
    r.add_post("/api/characters/{cid}", character_update)
    r.add_delete("/api/characters/{cid}", character_delete)
    r.add_post("/api/characters/{cid}/review", character_review)
    r.add_post("/api/file/{id}/characters", file_character)
    r.add_post("/api/select", select)
    r.add_post("/api/ops/move/plan", move_plan)
    r.add_post("/api/ops/move", move)
    r.add_post("/api/ops/trash", trash)
    r.add_post("/api/ops/restore", restore)
    r.add_post("/api/ops/empty", empty)
    r.add_post("/api/ops/bulk", bulk_op)
    r.add_get("/api/ops", history)
    r.add_post("/api/ops/{oid}/undo", undo)
    r.add_get("/api/albums", albums)
    r.add_post("/api/albums", album_create)
    r.add_post("/api/albums/{aid}", album_update)
    r.add_delete("/api/albums/{aid}", album_delete)
    r.add_get("/api/duplicates", duplicates)
    r.add_post("/api/file/{id}/open-in-comfy", open_in_comfy)
    r.add_post("/api/file/{id}/recipe", save_recipe)
    r.add_post("/api/backup", backup_now)
    r.add_post("/api/open-data", open_data)
    r.add_post("/api/shortcut", make_shortcut)
    r.add_post("/api/file/{id}/note", file_note)
    r.add_post("/api/eagle/read", eagle_read)
    r.add_get("/api/eagle/read", eagle_read_status)
    r.add_post("/api/eagle/apply", eagle_apply)
    r.add_get("/api/eagle/folders", eagle_folders)
    r.add_post("/api/eagle/send", eagle_send)
    r.add_get("/api/settings", settings_get)
    r.add_post("/api/update/check", update_check)
    r.add_post("/api/update/install", update_install)
    r.add_post("/api/update/restart", update_restart)
    r.add_post("/api/settings", settings_set)
    r.add_get("/api/thumb/{id}", thumb)
    r.add_get("/api/file/{id}", original)
    return app


@web.middleware
async def _guard(request: web.Request, handler):
    host = (request.host or "").rsplit(":", 1)[0].strip("[]").lower()
    if host not in ("127.0.0.1", "localhost"):
        return _err("Only this PC can use the Gallery.", 403)
    if request.path.startswith("/api/"):
        given = request.headers.get("X-MLG-Token") or request.query.get("t") or ""
        if not secrets.compare_digest(given, request.app[TOKEN]):
            return _err("Open the Gallery from its own window.", 403)
    try:
        resp = await handler(request)
    except (ValueError, TypeError, OverflowError, sqlite3.IntegrityError) as ex:
        if not request.path.startswith("/api/"):
            raise
        # a request that makes no sense (an id that isn't a number, a file that isn't there): said so, not a crash
        return _err(f"That request doesn't fit: {ex}"[:300], 400)
    if request.method != "GET" and request.path.startswith("/api/") and not request.path.endswith("/viewed") and resp.status < 400:
        request.app[LIB].db.user_change()  # something you did: Statistics and the like count it in straight away
    return resp


async def index(request: web.Request):
    if not secrets.compare_digest(request.query.get("t", ""), request.app[TOKEN]):
        return web.Response(text=f"<!doctype html><title>{APP_NAME}</title><p>Open the Gallery from its own window.", content_type="text/html", status=403)
    with open(os.path.join(UI_DIR, "index.html"), encoding="utf-8") as f:
        html = f.read()
    html = html.replace("{{TOKEN}}", request.app[TOKEN]).replace("{{VERSION}}", VERSION).replace("{{APP}}", APP_NAME)
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-store"})


async def _body(request: web.Request) -> dict:
    try:
        b = await request.json()
    except (json.JSONDecodeError, ValueError):
        return {}
    return b if isinstance(b, dict) else {}


def _lib(request) -> Library:
    return request.app[LIB]


async def status(request):
    st = await asyncio.to_thread(_lib(request).status)
    st["update"] = request.app[UPDATER].available()  # a newer Gallery on GitHub (only known once it's been asked)
    return web.json_response(st)


async def folders(request):
    return web.json_response({"folders": await asyncio.to_thread(_lib(request).folders)})


async def add_folder(request):
    b = await _body(request)
    try:
        f = await asyncio.to_thread(_lib(request).add_folder, b.get("path"))
    except ValueError as ex:
        return _err(str(ex), 422)
    return web.json_response({"folder": f})


async def pick(request):
    """The window's own folder dialog (in the browser there's none: the path is typed instead)."""
    fn = request.app[PICK]
    if not fn:
        return _err("No folder dialog here: type the folder's path.", 501)
    path = await asyncio.to_thread(fn)
    return web.json_response({"path": path or ""})


async def remove_folder(request):
    ok = await asyncio.to_thread(_lib(request).remove_folder, int(request.match_info["fid"]))
    return web.json_response({"ok": ok}) if ok else _err("not found", 404)


async def scan_folder(request):
    _lib(request).scan(int(request.match_info["fid"]))
    return web.json_response({"ok": True})


async def scan_all(request):
    _lib(request).scan_all()
    return web.json_response({"ok": True})


def _view(q) -> dict:
    """The view's filters from the query: the same for the files and their day headings."""
    return {"sort": q.get("sort", "newest"), "folder": int(q["folder"]) if q.get("folder", "").isdigit() else None,
            "kind": q.get("kind") or None, "q": q.get("q", "")[:2000]}


async def files(request):
    q = request.query
    try:
        res = await asyncio.to_thread(_lib(request).files, int(q.get("offset", 0)), int(q.get("limit", 200)),
                                      seed=int(q.get("seed", 0)), **_view(q))
    except ValueError:
        return _err("bad paging")
    return web.json_response(res)


async def shapes(request):
    q = request.query
    return web.json_response({"shapes": await asyncio.to_thread(_lib(request).shapes, seed=int(q.get("seed", 0) or 0), **_view(q))})


def _look(kind, key):
    async def handler(request):
        b = await _body(request)
        try:
            await asyncio.to_thread(_lib(request).set_look, kind, int(request.match_info[key]),
                                    b["icon"] if "icon" in b else ..., b["color"] if "color" in b else ...)
        except KeyError:
            return _err("not found", 404)
        except ValueError as ex:
            return _err(str(ex), 422)
        return web.json_response({"ok": True})
    return handler


folder_look = _look("folder", "fid")
album_look = _look("album", "aid")


async def days(request):
    return web.json_response({"days": await asyncio.to_thread(_lib(request).days, **_view(request.query))})


def _fid(request) -> int | None:
    try:
        return int(request.match_info["id"])
    except ValueError:
        return None


async def file_info(request):
    fid = _fid(request)
    info = await asyncio.to_thread(_lib(request).info, fid) if fid is not None else None
    return web.json_response(info) if info else _err("not found", 404)


async def file_workflow(request):
    """The workflow saved in the file (?part=workflow, the default, or prompt: the API graph), to copy or save."""
    fid = _fid(request)
    wf = await asyncio.to_thread(_lib(request).workflow, fid) if fid is not None else None
    part = request.query.get("part", "workflow")
    data = (wf or {}).get(part) if part in ("workflow", "prompt") else None
    if not data:
        return _err("This file has no saved workflow.", 404)
    headers = {}
    if request.query.get("download"):
        info = await asyncio.to_thread(_lib(request).info, fid)
        stem = os.path.splitext(info["name"])[0] if info else "workflow"
        headers["Content-Disposition"] = f'attachment; filename="{stem}{"" if part == "workflow" else "_api"}.json"'
    return web.json_response(data, headers=headers)


async def file_mark(request):
    fid, b = _fid(request), await _body(request)
    kw = {k: b[k] for k in ("rating", "favorite") if k in b}
    try:
        if "rating" in kw:
            kw["rating"] = int(kw["rating"] or 0)
        return web.json_response(await asyncio.to_thread(_lib(request).mark, fid, **kw))
    except (KeyError, TypeError):
        return _err("not found", 404)
    except ValueError:
        return _err("bad rating")


async def file_tags(request):
    fid, b = _fid(request), await _body(request)
    add, remove = b.get("add") or [], b.get("remove") or []
    if not isinstance(add, list) or not isinstance(remove, list):
        return _err("add and remove are lists of tag names")
    try:
        return web.json_response({"tags": await asyncio.to_thread(_lib(request).set_tags, fid, add, remove)})
    except (KeyError, TypeError):
        return _err("not found", 404)


async def file_viewed(request):
    fid = _fid(request)
    if fid is not None:
        await asyncio.to_thread(_lib(request).viewed, fid)
    return web.json_response({"ok": True})


async def suggest(request):
    q = request.query
    return web.json_response({"items": await asyncio.to_thread(_lib(request).suggest, q.get("field", ""), q.get("q", "")[:200])})


def _cid(request) -> int | None:
    try:
        return int(request.match_info["cid"])
    except ValueError:
        return None


async def characters_list(request):
    return web.json_response(await asyncio.to_thread(_lib(request).characters))


def _rules(b):
    rules = b.get("rules")
    if rules is not None and (not isinstance(rules, list) or not all(isinstance(r, dict) for r in rules)):
        raise ValueError("rules is a list of {kind, value, strength}")
    aliases = b.get("aliases") or []
    if not isinstance(aliases, list):
        raise ValueError("aliases is a list of names")
    return rules, aliases


async def character_create(request):
    b = await _body(request)
    try:
        rules, aliases = _rules(b)
        cid = await asyncio.to_thread(_lib(request).character_save, None, b.get("name"), aliases, rules or [])
    except ValueError as ex:
        return _err(str(ex), 422)
    return web.json_response(await asyncio.to_thread(_lib(request).character, cid))


async def character_get(request):
    cid = _cid(request)
    ch = await asyncio.to_thread(_lib(request).character, cid) if cid else None
    return web.json_response(ch) if ch else _err("not found", 404)


async def character_update(request):
    cid, b = _cid(request), await _body(request)
    lib = _lib(request)
    cur = await asyncio.to_thread(lib.character, cid) if cid else None
    if not cur:
        return _err("not found", 404)
    try:
        rules, aliases = _rules(b)
        kw = {"cover": b["cover"]} if "cover" in b else {}
        await asyncio.to_thread(lib.character_save, cid, b.get("name", cur["name"]), aliases if "aliases" in b else cur["aliases"], rules, **kw)
    except ValueError as ex:
        return _err(str(ex), 422)
    return web.json_response(await asyncio.to_thread(lib.character, cid))


async def character_delete(request):
    cid = _cid(request)
    ok = await asyncio.to_thread(_lib(request).character_delete, cid) if cid else False
    return web.json_response({"ok": True}) if ok else _err("not found", 404)


async def character_review(request):
    cid, b = _cid(request), await _body(request)
    ids = b.get("files") or []
    if not isinstance(ids, list):
        return _err("files is a list of file ids")
    try:
        await asyncio.to_thread(_lib(request).character_review, cid, ids, b.get("verdict"))
    except KeyError:
        return _err("not found", 404)
    except (ValueError, TypeError) as ex:
        return _err(str(ex))
    return web.json_response({"ok": True})


async def review_queue(request):
    q = request.query
    cid = int(q["character"]) if q.get("character", "").isdigit() else None
    return web.json_response({"items": await asyncio.to_thread(_lib(request).review_queue, cid)})


async def trigger_words(request):
    return web.json_response({"words": await asyncio.to_thread(_lib(request).trigger_words, request.query.get("lora", "")[:200])})


async def file_character(request):
    fid, b = _fid(request), await _body(request)
    verdict = b.get("verdict")
    if verdict not in ("confirm", "reject", None) or not str(b.get("character", "")).isdigit():
        return _err("character (an id) and verdict (confirm, reject or null)")
    try:
        return web.json_response({"characters": await asyncio.to_thread(_lib(request).set_file_character, fid, int(b["character"]), verdict)})
    except (KeyError, TypeError):
        return _err("not found", 404)


async def _ids(request, b):
    from .fileops import OpError
    try:
        return await asyncio.to_thread(_lib(request).resolve, b.get("selection"))
    except (OpError, ValueError, TypeError):
        raise web.HTTPBadRequest(text='{"error": "selection: a list of ids, or {view: ...}"}', content_type="application/json")


def _op(fn):
    """Run a file operation; its refusals come back as a message."""
    async def handler(request):
        from .fileops import OpError
        try:
            return web.json_response(await fn(request, await _body(request)))
        except OpError as ex:
            return _err(str(ex), 422)
        except KeyError:
            return _err("not found", 404)
    return handler


async def select(request):
    b = await _body(request)
    return web.json_response({"ids": await _ids(request, b)})


@_op
async def move_plan(request, b):
    from . import fileops
    plan = await asyncio.to_thread(fileops.plan_move, _lib(request).db, await _ids(request, b), int(b.get("folder") or 0), b.get("sub") or "")
    plan.pop("items")
    return plan


@_op
async def move(request, b):
    from . import fileops
    return await asyncio.to_thread(fileops.move, _lib(request).db, await _ids(request, b), int(b.get("folder") or 0), b.get("sub") or "")


@_op
async def trash(request, b):
    from . import fileops
    return await asyncio.to_thread(fileops.trash, _lib(request).db, await _ids(request, b))


@_op
async def restore(request, b):
    from . import fileops
    return await asyncio.to_thread(fileops.restore, _lib(request).db, await _ids(request, b))


@_op
async def empty(request, b):
    from . import fileops
    ids = await _ids(request, b) if b.get("selection") is not None else None
    return await asyncio.to_thread(fileops.empty, _lib(request).db, ids)


@_op
async def bulk_op(request, b):
    from . import bulk
    return await asyncio.to_thread(bulk.apply, _lib(request).db, await _ids(request, b), str(b.get("action")), b.get("value"))


@_op
async def file_edit(request, b):
    from . import edit
    lib = _lib(request)
    r = await asyncio.to_thread(edit.apply, lib.db, int(request.match_info["id"]), b.get("rotate") or 0, bool(b.get("flip")),
                                b.get("crop"), bool(b.get("copy")))
    lib.thumbs.nudge()
    lib.meta.nudge()
    return r


async def history(request):
    from . import fileops
    return web.json_response({"ops": await asyncio.to_thread(fileops.history, _lib(request).db)})


async def undo(request):
    from . import fileops
    try:
        return web.json_response(await asyncio.to_thread(fileops.undo, _lib(request).db, int(request.match_info["oid"])))
    except fileops.OpError as ex:
        return _err(str(ex), 422)
    except ValueError:
        return _err("bad id")


async def albums(request):
    return web.json_response({"albums": await asyncio.to_thread(_lib(request).albums)})


async def album_create(request):
    b = await _body(request)
    try:
        return web.json_response(await asyncio.to_thread(_lib(request).album_save, None, b.get("name")))
    except ValueError as ex:
        return _err(str(ex), 422)


async def album_update(request):
    b = await _body(request)
    lib = _lib(request)
    try:
        aid = int(request.match_info["aid"])
        cur = next((a for a in await asyncio.to_thread(lib.albums) if a["id"] == aid), None)
        if not cur:
            return _err("not found", 404)
        kw = {"cover": b["cover"]} if "cover" in b else {}
        return web.json_response(await asyncio.to_thread(lib.album_save, aid, b.get("name", cur["name"]), **kw))
    except KeyError:
        return _err("not found", 404)
    except ValueError as ex:
        return _err(str(ex), 422)


async def album_delete(request):
    try:
        ok = await asyncio.to_thread(_lib(request).album_delete, int(request.match_info["aid"]))
    except ValueError:
        return _err("bad id")
    return web.json_response({"ok": True}) if ok else _err("not found", 404)


async def duplicates(request):
    return web.json_response(await asyncio.to_thread(_lib(request).duplicates))


async def open_in_comfy(request):
    from . import comfy
    fid = _fid(request)
    lib = _lib(request)
    info = await asyncio.to_thread(lib.info, fid) if fid is not None else None
    if not info:
        return _err("not found", 404)
    wf = await asyncio.to_thread(lib.workflow, fid) or {}
    try:
        r = await comfy.open_in_comfy(lib.settings.get("comfy_url"), wf.get("workflow"), wf.get("prompt"), os.path.splitext(info["name"])[0])
    except comfy.ComfyError as ex:
        return _err(str(ex), 502)
    return web.json_response(r)


async def save_recipe(request):
    from . import comfy
    fid, b = _fid(request), await _body(request)
    lib = _lib(request)
    info = await asyncio.to_thread(lib.info, fid) if fid is not None else None
    path = await asyncio.to_thread(lib.file_path, fid) if info else None
    if not path or not os.path.isfile(path):
        return _err("not found", 404)
    try:
        r = await asyncio.to_thread(comfy.save_recipe, lib.models.data_dir(), path, info["kind"], info["name"], b.get("title"))
    except comfy.ComfyError as ex:
        return _err(str(ex), 422)
    return web.json_response(r)


async def backup_now(request):
    path = await asyncio.to_thread(_lib(request).backup, True)
    return web.json_response({"path": path})


async def open_data(request):
    if os.name != "nt":
        return _err("Only on Windows.", 501)
    import subprocess
    subprocess.Popen(["explorer", os.path.normpath(_lib(request).data_dir)])
    return web.json_response({"ok": True})


async def make_shortcut(request):
    import subprocess
    from . import shortcut
    try:
        return web.json_response({"path": await asyncio.to_thread(shortcut.make)})
    except (OSError, subprocess.SubprocessError) as ex:
        return _err(f"The shortcut wasn't made: {ex}", 500)


async def file_note(request):
    fid, b = _fid(request), await _body(request)
    if not isinstance(b.get("note") or "", str):
        return _err("note: text")
    note = (b.get("note") or "").strip()[:4000]
    lib = _lib(request)
    if not fid or not await asyncio.to_thread(lib.info, fid):
        return _err("not found", 404)

    def save():
        with lib.db.write() as c:
            if note:
                c.execute("INSERT INTO file_notes (file_id, note, source) VALUES (?, ?, 'user') ON CONFLICT (file_id) DO UPDATE SET note=excluded.note, source='user'", (fid, note))
            else:
                c.execute("DELETE FROM file_notes WHERE file_id=?", (fid,))
    await asyncio.to_thread(save)
    return web.json_response({"note": note})


# ---------------------------------------------------------------- the Eagle bridge
_EAGLE: dict = {}  # the library being read, and what reading it found (one at a time)


async def eagle_read(request):
    """Start reading an Eagle library (only reading: nothing in it is changed)."""
    from . import eagle
    b = await _body(request)
    try:
        lib_path = eagle.library_root(b.get("path"))
    except eagle.EagleError as ex:
        return _err(str(ex), 422)
    if _EAGLE.get("state") == "reading":
        return _err("Another Eagle library is being read.", 409)
    lib = _lib(request)
    _EAGLE.clear()
    _EAGLE.update(state="reading", path=lib_path, done=0)

    def run():
        try:
            plan = eagle.plan_import(lib.db, lib_path, progress=lambda n: _EAGLE.update(done=n))
            _EAGLE.update(state="ready", plan=plan, summary=eagle.summary(plan))
        except Exception as ex:
            _EAGLE.update(state="failed", error=str(ex))
    import threading
    threading.Thread(target=run, name="eagle", daemon=True).start()
    return web.json_response({"state": "reading", "path": lib_path})


async def eagle_read_status(request):
    return web.json_response({k: v for k, v in _EAGLE.items() if k != "plan"} or {"state": "idle"})


async def eagle_apply(request):
    """Bring the read library's tags, albums, ratings and notes in (copying in what the Gallery doesn't have, if asked)."""
    from . import eagle
    b = await _body(request)
    plan = _EAGLE.get("plan")
    if _EAGLE.get("state") != "ready" or not plan:
        return _err("Read an Eagle library first.", 409)
    _EAGLE["state"] = "applying"  # a second click while this runs is refused, not done twice
    lib = _lib(request)
    try:
        copied = 0
        if b.get("copy_to") and plan["missing"]:
            copied = await asyncio.to_thread(eagle.copy_in, lib.db, plan, int(b["copy_to"]), b.get("sub") or "")
        r = await asyncio.to_thread(eagle.apply_import, lib.db, plan)
    except (eagle.EagleError, ValueError, OSError) as ex:
        _EAGLE["state"] = "ready"
        return _err(str(ex), 422)
    _EAGLE.clear()
    lib.thumbs.nudge()
    lib.meta.nudge()
    return web.json_response({**r, "copied": copied})


async def eagle_folders(request):
    from . import eagle
    lib = _lib(request)
    try:
        return web.json_response({"folders": await eagle.eagle_folders(lib.settings.get("eagle_url"), lib.settings.get("eagle_token"))})
    except eagle.EagleError as ex:
        return _err(str(ex), 502)


async def eagle_send(request):
    """Add files to Eagle (through its own API: Eagle copies them into its library), with tags and the prompt as the note."""
    from . import eagle
    b = await _body(request)
    lib = _lib(request)
    ids = (await _ids(request, b))[:5000]

    def gather():
        out = []
        for fid in ids:
            info = lib.info(fid)
            path = lib.file_path(fid)
            if info and path and os.path.isfile(path):
                tags = [t["name"] for t in info["tags"]] + [ch["name"] for ch in info.get("characters") or [] if ch["state"] in ("auto", "confirmed")]
                out.append({"path": path, "name": info["name"], "tags": tags, "note": eagle.note_for(info)})
        return out
    files = await asyncio.to_thread(gather)
    if not files:
        return _err("None of those files are reachable.", 422)
    try:
        r = await eagle.send(lib.settings.get("eagle_url"), lib.settings.get("eagle_token"), files, b.get("folder") or None)
    except eagle.EagleError as ex:
        return _err(str(ex), 502)
    return web.json_response(r)


async def stats_view(request):
    from . import stats
    d = request.query.get("days", "30")
    days = int(d) if d.isdigit() and int(d) > 0 else None
    return web.json_response(await asyncio.to_thread(stats.overview, _lib(request), days, _lib(request).models))


def _settings_out(lib, updater: Updater, can_restart: bool) -> dict:
    from .thumbs import find_ffmpeg
    backups = os.path.join(lib.data_dir, "backups")
    last = sorted(f for f in os.listdir(backups) if f.endswith(".db")) if os.path.isdir(backups) else []
    from . import shortcut
    return {"settings": lib.settings.all(), "ml_data_found": lib.models.data_dir(), "data_dir": lib.data_dir,
            "ffmpeg_found": find_ffmpeg(lib.settings.get("ffmpeg")), "last_backup": last[-1] if last else None, "version": VERSION,
            "shortcut": shortcut.exists() if os.name == "nt" else None, "update": dict(updater.info(), can_restart=can_restart)}


def _settings_view(request) -> dict:
    return _settings_out(_lib(request), request.app[UPDATER], bool(request.app[QUIT] and request.app[UPDATER].root))


async def settings_get(request):
    return web.json_response(await asyncio.to_thread(_settings_view, request))


def _check_settings(b: dict) -> dict:
    """Settings as they'll be saved, or ValueError saying why one can't work."""
    import re
    out = dict(b)
    ff = str(out.get("ffmpeg") or "").strip().strip('"') if "ffmpeg" in out else None
    if ff:
        if os.path.isdir(ff) and os.path.isfile(os.path.join(ff, "ffmpeg.exe")):
            ff = os.path.join(ff, "ffmpeg.exe")
        if not os.path.isfile(ff):
            raise ValueError("There's no ffmpeg there: pick ffmpeg.exe, or leave it empty to use the one the Gallery finds.")
        if not os.path.basename(ff).lower().startswith("ffmpeg"):
            raise ValueError(f"{os.path.basename(ff)} isn't ffmpeg: pick ffmpeg.exe.")
        out["ffmpeg"] = ff
    ml = str(out.get("ml_data") or "").strip().strip('"') if "ml_data" in out else None
    if ml:
        if not os.path.isdir(ml):
            raise ValueError("That folder doesn't exist: Model Librarian's data folder is .sm_model_fetcher, next to your models folder.")
        out["ml_data"] = ml
    for key, what in (("comfy_url", "ComfyUI"), ("eagle_url", "Eagle")):
        if key in out:
            v = str(out[key] or "").strip().rstrip("/")
            if v and not re.match(r"https?://", v, re.I) and re.fullmatch(r"[\w.-]+(:\d+)?", v):
                v = "http://" + v  # 127.0.0.1:8188 means http://127.0.0.1:8188
            if v and not re.fullmatch(r"https?://[\w.-]+(:\d{1,5})?(/\S*)?", v, re.I):
                raise ValueError(f"{what}'s address looks like http://127.0.0.1:{8188 if key == 'comfy_url' else 41595}.")
            out[key] = v
    return out


async def settings_set(request):
    b = await _body(request)
    lib = _lib(request)
    try:
        b = _check_settings({k: v for k, v in b.items() if k in lib.settings.all()})
    except ValueError as ex:
        return _err(str(ex), 422)
    await asyncio.to_thread(lib.settings.update, b)
    return web.json_response(await asyncio.to_thread(_settings_view, request))


# ---------------------------------------------------------------- updates (see update.py)
async def _start_daily(app):
    app[DAILY] = asyncio.get_running_loop().create_task(app[UPDATER].daily())


async def _stop_daily(app):
    app[DAILY].cancel()


async def update_check(request):
    from .update import UpdateError
    try:
        await request.app[UPDATER].check()
    except UpdateError as ex:
        return _err(str(ex), 502)
    return web.json_response(await asyncio.to_thread(_settings_view, request))


async def update_install(request):
    from .update import UpdateError
    try:
        await request.app[UPDATER].install()
    except UpdateError as ex:
        return _err(str(ex), 502)
    except OSError as ex:
        return _err(f"The update couldn't be unpacked: {ex}", 500)
    return web.json_response(await asyncio.to_thread(_settings_view, request))


async def update_restart(request):
    """Close the window and have the launcher start the Gallery again, swapping in the waiting update first."""
    up, quit = request.app[UPDATER], request.app[QUIT]
    if not (up.root and up.staged() and quit):
        return _err("There's no update waiting, or this Gallery can't restart itself: close it and start it again.", 409)
    import subprocess
    exe = os.path.join(up.root, f"{APP_NAME}.exe")
    subprocess.Popen([exe, "--wait", "--data", _lib(request).data_dir], cwd=up.root, close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    asyncio.get_running_loop().call_later(0.3, quit)  # after this answer has gone
    return web.json_response({"ok": True})


async def reveal_folder(request):
    """Open a library folder in Explorer (only one that's in the library)."""
    f = next((f for f in await asyncio.to_thread(_lib(request).folders) if f["id"] == int(request.match_info["fid"])), None)
    if not f or not os.path.isdir(f["path"]):
        return _err("That folder isn't reachable right now.", 404)
    import subprocess
    subprocess.Popen(["explorer", os.path.normpath(f["path"])])
    return web.json_response({"ok": True})


async def reveal(request):
    """Show the file in Explorer, selected (only on this PC, and only a file that's in the index)."""
    try:
        fid = int(request.match_info["id"])
    except ValueError:
        return _err("bad id")
    path = await asyncio.to_thread(_lib(request).file_path, fid)
    if not path or not os.path.isfile(path):
        return _err("That file isn't there any more.", 404)
    if os.name != "nt":
        return _err("Only on Windows.", 501)
    import subprocess
    subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    return web.json_response({"ok": True})


async def thumb(request):
    try:
        fid = int(request.match_info["id"])
    except ValueError:
        return _err("bad id")
    size = int(request.query.get("s", 0) or 0)
    path = await asyncio.to_thread(_lib(request).thumbs.get, fid, size if size in (512, 768) else None)
    if not path:
        return web.Response(status=404)
    return web.FileResponse(path, headers={"Cache-Control": "max-age=86400", "Content-Type": "image/webp"})


async def original(request):
    try:
        fid = int(request.match_info["id"])
    except ValueError:
        return _err("bad id")
    path = await asyncio.to_thread(_lib(request).file_path, fid)
    if not path or not os.path.isfile(path):
        return _err("That file isn't there any more.", 404)
    return web.FileResponse(path, headers={"Cache-Control": "no-cache"})  # ranges supported: videos seek


async def serve(lib: Library, port: int = 0, token: str | None = None, pick_folder=None, quit=None):
    """Start serving on 127.0.0.1 -> (runner, port, token)."""
    app = make_app(lib, token, pick_folder=pick_folder, quit=quit)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    real = site._server.sockets[0].getsockname()[1]
    return runner, real, app[TOKEN]
