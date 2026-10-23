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

from aiohttp import web

from . import APP_NAME, VERSION
from .library import Library

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")
mimetypes.init()  # first: a later init() would drop the types added below (and Windows' registry can map .webp oddly)
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("video/webm", ".webm")
mimetypes.add_type("video/mp4", ".mp4")
mimetypes.add_type("text/javascript", ".js")


LIB = web.AppKey("lib", Library)
TOKEN = web.AppKey("token", str)
PICK = web.AppKey("pick_folder", object)


def _err(msg: str, status: int = 400) -> web.Response:
    return web.json_response({"error": msg}, status=status)


def make_app(lib: Library, token: str | None = None, *, pick_folder=None) -> web.Application:
    """The aiohttp application. `pick_folder()`: the window's folder dialog, when there is one."""
    token = token or secrets.token_urlsafe(24)
    app = web.Application(middlewares=[_guard], client_max_size=4 * 1024 * 1024)
    app[LIB], app[TOKEN], app[PICK] = lib, token, pick_folder
    r = app.router
    r.add_get("/", index)
    r.add_static("/ui/", UI_DIR, show_index=False)
    r.add_get("/api/status", status)
    r.add_get("/api/folders", folders)
    r.add_post("/api/folders", add_folder)
    r.add_post("/api/folders/pick", pick)
    r.add_delete("/api/folders/{fid}", remove_folder)
    r.add_post("/api/folders/{fid}/scan", scan_folder)
    r.add_post("/api/scan", scan_all)
    r.add_get("/api/files", files)
    r.add_get("/api/days", days)
    r.add_post("/api/file/{id}/reveal", reveal)
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
    r.add_get("/api/settings", settings_get)
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
    return await handler(request)


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
    return web.json_response(await asyncio.to_thread(_lib(request).status))


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


async def stats_view(request):
    from . import stats
    d = request.query.get("days", "30")
    days = int(d) if d.isdigit() and int(d) > 0 else None
    return web.json_response(await asyncio.to_thread(stats.overview, _lib(request), days, _lib(request).models))


def _settings_out(lib) -> dict:
    return {"settings": lib.settings.all(), "ml_data_found": lib.models.data_dir(), "data_dir": lib.data_dir}


async def settings_get(request):
    return web.json_response(await asyncio.to_thread(_settings_out, _lib(request)))


async def settings_set(request):
    b = await _body(request)
    lib = _lib(request)
    await asyncio.to_thread(lib.settings.update, {k: v for k, v in b.items() if k in lib.settings.all()})
    return web.json_response(await asyncio.to_thread(_settings_out, lib))


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
    path = await asyncio.to_thread(_lib(request).thumbs.get, fid)
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


async def serve(lib: Library, port: int = 0, token: str | None = None, pick_folder=None):
    """Start serving on 127.0.0.1 -> (runner, port, token)."""
    app = make_app(lib, token, pick_folder=pick_folder)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    real = site._server.sockets[0].getsockname()[1]
    return runner, real, app[TOKEN]
