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
from . import remote
from .library import Library
from .update import Updater

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")
mimetypes.init()  # first: a later init() would drop the types added below (and Windows' registry can map .webp oddly)
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("video/webm", ".webm")
mimetypes.add_type("video/mp4", ".mp4")
mimetypes.add_type("video/mp4", ".m4v")
mimetypes.add_type("video/mp4", ".mov")  # QuickTime is the same container family: WebView2 plays it when it's called mp4 (Live Photos)
mimetypes.add_type("image/avif", ".avif")
mimetypes.add_type("image/bmp", ".bmp")
mimetypes.add_type("text/javascript", ".js")


LIB = web.AppKey("lib", Library)
TOKEN = web.AppKey("token", str)
PICK = web.AppKey("pick_folder", object)
UPDATER = web.AppKey("updater", Updater)
QUIT = web.AppKey("quit", object)
RELAUNCH = web.AppKey("relaunch", object)
DAILY = web.AppKey("daily", object)
DEVICES = web.AppKey("devices", remote.Devices)
RUNNER = web.AppKey("runner", object)      # the server's runner, for the second site (phones) to join
REMOTE = web.AppKey("remote_site", object)  # that site, while it runs


def _err(msg: str, status: int = 400) -> web.Response:
    return web.json_response({"error": msg}, status=status)


def make_app(lib: Library, token: str | None = None, *, pick_folder=None, quit=None, relaunch=None) -> web.Application:
    """The aiohttp application. `pick_folder()`: the window's folder dialog, when there is one; `quit()` closes the
    Gallery and `relaunch(args)` starts another one (switching libraries, Restart now), when it can."""
    token = token or secrets.token_urlsafe(24)
    app = web.Application(middlewares=[_guard], client_max_size=4 * 1024 * 1024)  # (_guard: this PC, or a paired device)
    app[LIB], app[TOKEN], app[PICK], app[QUIT], app[RELAUNCH] = lib, token, pick_folder, quit, relaunch
    app[UPDATER] = Updater(lib.home, lib.settings)
    app[DEVICES] = remote.Devices(lib.settings)
    app[REMOTE] = None
    app.on_startup.append(_start_daily)
    app.on_cleanup.append(_stop_daily)
    r = app.router
    r.add_get("/", index)
    r.add_get("/m", phone_page)
    r.add_get("/pair", pair)
    r.add_get("/api/remote", remote_get)
    r.add_post("/api/remote", remote_set)
    r.add_post("/api/remote/code", remote_code)
    r.add_post("/api/remote/forget", remote_forget)
    r.add_post("/api/remote/rename", remote_rename)
    r.add_static("/ui/", UI_DIR, show_index=False)
    r.add_get("/api/status", status)
    r.add_post("/api/library/kind", library_kind)
    r.add_get("/api/folders", folders)
    r.add_post("/api/folders", add_folder)
    r.add_post("/api/folders/pick", pick)
    r.add_delete("/api/folders/{fid}", remove_folder)
    r.add_post("/api/folders/{fid}/scan", scan_folder)
    r.add_post("/api/folders/{fid}/look", folder_look)
    r.add_get("/api/folders/{fid}/dirs", folder_dirs)
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
    r.add_get("/api/artists", artists_list)
    r.add_post("/api/artists", artist_create)
    r.add_get("/api/artists/{aid}", artist_get)
    r.add_post("/api/artists/{aid}", artist_update)
    r.add_delete("/api/artists/{aid}", artist_delete)
    r.add_post("/api/file/{id}/credit", file_credit)
    r.add_get("/api/inbox", inbox_list)
    r.add_get("/api/inbox/folders", inbox_folders)
    r.add_post("/api/inbox/folders", inbox_folders_set)
    r.add_post("/api/inbox/file", inbox_file)
    r.add_post("/api/inbox/dismiss", inbox_dismiss)
    r.add_post("/api/inbox/undismiss", inbox_undismiss)
    r.add_post("/api/inbox/recycle", inbox_recycle)
    r.add_get("/api/inbox/thumb/{key}", inbox_thumb)
    r.add_get("/api/inbox/file/{key}", inbox_original)
    r.add_post("/api/open-url", open_url)
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
    r.add_post("/api/albums/move", album_move)
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
    r.add_post("/api/eagle/stop", eagle_stop)
    r.add_post("/api/eagle/seen", eagle_seen)
    r.add_get("/api/sort", sort_queue)
    r.add_post("/api/sort", sort_decide)
    r.add_post("/api/sort/undo", sort_undo)
    r.add_post("/api/sort/reset", sort_reset)
    r.add_post("/api/ops/rename/plan", rename_plan)
    r.add_post("/api/ops/rename", rename_apply)
    r.add_post("/api/eagle/apply", eagle_apply)
    r.add_get("/api/eagle/folders", eagle_folders)
    r.add_post("/api/eagle/send", eagle_send)
    r.add_get("/api/settings", settings_get)
    r.add_post("/api/update/check", update_check)
    r.add_post("/api/update/install", update_install)
    r.add_post("/api/update/restart", update_restart)
    r.add_get("/api/libraries", libraries_get)
    r.add_post("/api/libraries/new", library_new)
    r.add_post("/api/libraries/open", library_open)
    r.add_post("/api/libraries/forget", library_forget)
    r.add_post("/api/settings", settings_set)
    r.add_get("/api/thumb/{id}", thumb)
    r.add_get("/api/file/{id}", original)
    return app


def _peer(request) -> str:
    return request.remote or ""


def _is_local(request) -> bool:
    return _peer(request) in ("127.0.0.1", "::1")


@web.middleware
async def _guard(request: web.Request, handler):
    if not _is_local(request):  # another device: a paired phone or tablet on a private network, or nothing
        return await _guard_device(request, handler)
    host = (request.host or "").rsplit(":", 1)[0].strip("[]").lower()
    if host not in ("127.0.0.1", "localhost"):
        return _err("Only this PC can use the Gallery.", 403)
    if request.path.startswith("/api/"):
        given = request.headers.get("X-MLG-Token") or request.query.get("t") or ""
        if not secrets.compare_digest(given, request.app[TOKEN]):
            return _err("Open the Gallery from its own window.", 403)
    return await _run(request, handler)


async def _guard_device(request: web.Request, handler):
    """A phone or tablet: only while Phone and tablet is on, only on a private network, only a paired device (but for
    pairing itself), and only what a device may do (remote.ALLOWED). Its API requests carry a header a web page
    elsewhere can't send without asking first, and the cookie is never sent from another site: no other page can act for it."""
    lib = _lib(request)
    lib.settings.fresh()  # (turned off in another Gallery on this PC: off here too)
    if not lib.settings.get("remote") or not remote.private(_peer(request)):
        return web.Response(status=403, text="Not here.")
    if request.path == "/pair":
        return await _run(request, handler)
    device = request.app[DEVICES].check(request.cookies.get(remote.COOKIE))
    if not device:
        return web.Response(status=403, content_type="text/html", text=_PAIR_PAGE.format(app=APP_NAME, title="This device isn't paired",
                            text="On the PC: Settings, Phone and tablet, Pair a device. Scan its QR code with this device, or type its code here."))
    path = getattr(request.rel_url, "path_safe", None) or request.path  # (as the router reads it: an encoded / stays encoded)
    if path.startswith("/api/"):
        picture = request.method == "GET" and remote.MEDIA.fullmatch(path)
        if not (request.headers.get("X-MLG-Token") or picture) or not remote.allowed(request.method, path):
            return _err("Not from a phone or tablet: do that on the PC.", 403)
    elif not (path in ("/", "/m") or path.startswith("/ui/")):
        return web.Response(status=404)
    return await _run(request, handler)


# what an unpaired device sees: how to pair, and a box for the code (sent to /pair, as the QR code's link does)
_PAIR_PAGE = ("<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{app}</title>"
              "<body style='font:16px/1.5 system-ui;background:#1c1c1c;color:#eee;padding:24px;max-width:520px;margin:auto'>"
              "<h2 style='font-weight:600'>{title}</h2><p>{text}</p><form action='/pair' method='get' style='display:flex;gap:8px'>"
              "<input name='c' placeholder='ABCD-2345' autocapitalize='characters' autocomplete='off' aria-label='Code' "
              "style='flex:1;font:inherit;padding:10px 12px;border-radius:10px;border:1px solid #444;background:#2a2a2a;color:#eee;letter-spacing:.1em'>"
              "<button style='font:inherit;padding:10px 16px;border-radius:10px;border:0;background:#8b7cf6;color:#fff'>Pair</button></form>")


async def _run(request: web.Request, handler):
    try:
        resp = await handler(request)
    except (ValueError, TypeError, OverflowError, sqlite3.IntegrityError) as ex:
        if not request.path.startswith("/api/"):
            raise
        # a request that makes no sense (an id that isn't a number, a file that isn't there): said so, not a crash
        return _err(f"That request doesn't fit: {ex}"[:300], 400)
    if request.method != "GET" and request.path.startswith("/api/") and not request.path.endswith("/viewed") and resp.status < 400:
        request.app[LIB].db.user_change()  # something you did: Statistics and the like count it in straight away
    if request.path.startswith("/ui/"):
        # asked again each time (an unchanged file is a quick "not modified"): after an update the window never
        # mixes a new app.js with the old modules it imports (only app.js and app.css carry the version)
        resp.headers["Cache-Control"] = "no-cache"
    return resp


async def index(request: web.Request):
    if not _is_local(request):
        return await phone_page(request)  # (a paired device: the phone layout)
    if not secrets.compare_digest(request.query.get("t", ""), request.app[TOKEN]):
        return web.Response(text=f"<!doctype html><title>{APP_NAME}</title><p>Open the Gallery from its own window.", content_type="text/html", status=403)
    with open(os.path.join(UI_DIR, "index.html"), encoding="utf-8") as f:
        html = f.read()
    html = html.replace("{{TOKEN}}", request.app[TOKEN]).replace("{{VERSION}}", VERSION).replace("{{APP}}", APP_NAME)
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-store"})


async def phone_page(request: web.Request):
    """The layout made for phones (ui/phone.html): a paired device's, and on the PC at /m?t=<token> to see it."""
    local = _is_local(request)
    if local and not secrets.compare_digest(request.query.get("t", ""), request.app[TOKEN]):
        return web.Response(text=f"<!doctype html><title>{APP_NAME}</title><p>Open the Gallery from its own window.", content_type="text/html", status=403)
    with open(os.path.join(UI_DIR, "phone.html"), encoding="utf-8") as f:
        html = f.read()
    html = html.replace("{{TOKEN}}", request.app[TOKEN] if local else "device").replace("{{VERSION}}", VERSION).replace("{{APP}}", APP_NAME)
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-store"})


async def pair(request: web.Request):
    """A device opening the pairing address (the QR code): remembered, with a key in a cookie only it sends."""
    if _is_local(request):
        return web.Response(status=404)
    key = request.app[DEVICES].pair(request.query.get("c", ""), request.headers.get("User-Agent", ""))
    if not key:
        return web.Response(status=403, content_type="text/html", text=_PAIR_PAGE.format(app=APP_NAME, title="That code didn't work",
                            text="A code lasts 10 minutes and pairs one device. On the PC: Settings, Phone and tablet, Pair a device, for a new one."))
    resp = web.Response(status=302, headers={"Location": "/"})
    resp.set_cookie(remote.COOKIE, key, max_age=10 * 365 * 86400, httponly=True, samesite="Strict", path="/")
    return resp


# ---------------------------------------------------------------- Phone and tablet, from the PC's Settings
async def remote_start(app) -> str | None:
    """Listen for paired devices (on every network: only private addresses are answered). -> why it couldn't, or None."""
    if app[REMOTE] or not app.get(RUNNER):
        return None
    lib = app[LIB]
    port = int(lib.settings.get("remote_port") or remote.PORT)
    site = web.TCPSite(app[RUNNER], os.environ.get("MLG_REMOTE_HOST") or "0.0.0.0", port)  # (MLG_REMOTE_HOST: the tests keep it on 127.0.0.1)
    try:
        await site.start()
    except OSError as ex:
        return f"Port {port} is taken ({ex.strerror or ex}): pick another in Settings."
    app[REMOTE] = site
    return None


async def remote_stop(app) -> None:
    site, app[REMOTE] = app[REMOTE], None
    if site:
        await site.stop()


def _remote_view(request) -> dict:
    app, lib = request.app, _lib(request)
    port = int(lib.settings.get("remote_port") or remote.PORT)
    return {"on": bool(lib.settings.get("remote")), "running": bool(app[REMOTE]), "port": port, "devices": app[DEVICES].list(),
            "addresses": [dict(a, url=f"http://{a['ip']}:{port}/") for a in remote.addresses()]}


async def remote_get(request):
    return web.json_response(_remote_view(request))


async def remote_set(request):
    """{on: true|false, port}: listen for paired devices, or stop."""
    b = await _body(request)
    lib, app = _lib(request), request.app
    patch = {}
    if "port" in b:
        try:
            port = int(b["port"])
        except (TypeError, ValueError):
            return _err("A port is a number, like 8766.", 422)
        if not 1024 <= port <= 65535:
            return _err("A port is between 1024 and 65535.", 422)
        patch["remote_port"] = port
    if isinstance(b.get("on"), bool):
        patch["remote"] = b["on"]
    await asyncio.to_thread(lib.settings.update, patch)
    if app[REMOTE] and ("remote_port" in patch or patch.get("remote") is False):
        await remote_stop(app)
    if lib.settings.get("remote"):
        why = await remote_start(app)
        if why:
            await asyncio.to_thread(lib.settings.update, {"remote": False})
            return _err(why, 409)
    return web.json_response(_remote_view(request))


async def remote_code(request):
    """A pairing code (10 minutes, one device), and the addresses to open with it (for the QR code)."""
    if not _lib(request).settings.get("remote") or not request.app[REMOTE]:
        return _err("Turn Phone and tablet on first.", 409)
    code = request.app[DEVICES].new_code()
    view = _remote_view(request)
    return web.json_response({**view, "code": code, "minutes": remote.CODE_FOR // 60,
                              "pair": [f"{a['url']}pair?c={code}" for a in view["addresses"]]})


async def remote_forget(request):
    b = await _body(request)
    ok = await asyncio.to_thread(request.app[DEVICES].forget, str(b.get("id") or ""))
    return web.json_response(_remote_view(request)) if ok else _err("not found", 404)


async def remote_rename(request):
    b = await _body(request)
    try:
        ok = await asyncio.to_thread(request.app[DEVICES].rename, str(b.get("id") or ""), b.get("name"))
    except ValueError as ex:
        return _err(str(ex), 422)
    return web.json_response(_remote_view(request)) if ok else _err("not found", 404)


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
    st["job"] = eagle_job()  # a long job of yours under way (an Eagle library brought in), for the status bar
    return web.json_response(st)


async def library_kind(request):
    """What this library is for (AI generations, photos, illustrations): asked once, changeable in Settings."""
    b = await _body(request)
    try:
        kinds = b.get("kinds") if isinstance(b.get("kinds"), list) else [str(b.get("kind") or "")]
        kinds = await asyncio.to_thread(_lib(request).set_kinds, [str(k) for k in kinds])
    except ValueError as ex:
        return _err(str(ex), 422)
    return web.json_response({"kinds": kinds})


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
        cid = await asyncio.to_thread(_lib(request).character_save, None, b.get("name"), aliases, rules or [], who=b.get("who"))
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
        if "who" in b:
            kw["who"] = b["who"]
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


# ---------------------------------------------------------------- artists, and each file's artist and source
def _aid(request) -> int | None:
    try:
        return int(request.match_info["aid"])
    except ValueError:
        return None


def _names(b, key: str) -> list:
    v = b.get(key) or []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ValueError(f"{key} is a list of text")
    return v


async def artists_list(request):
    return web.json_response(await asyncio.to_thread(_lib(request).artists))


async def artist_create(request):
    b = await _body(request)
    lib = _lib(request)
    try:
        aid = await asyncio.to_thread(lib.artist_save, None, b.get("name"), _names(b, "aliases"), _names(b, "links"))
    except ValueError as ex:
        return _err(str(ex), 422)
    return web.json_response(await asyncio.to_thread(lib.artist, aid))


async def artist_get(request):
    aid = _aid(request)
    a = await asyncio.to_thread(_lib(request).artist, aid) if aid else None
    return web.json_response(a) if a else _err("not found", 404)


async def artist_update(request):
    aid, b = _aid(request), await _body(request)
    lib = _lib(request)
    cur = await asyncio.to_thread(lib.artist, aid) if aid else None
    if not cur:
        return _err("not found", 404)
    try:
        kw = {"cover": b["cover"]} if "cover" in b else {}
        await asyncio.to_thread(lib.artist_save, aid, b.get("name", cur["name"]), _names(b, "aliases") if "aliases" in b else cur["aliases"],
                                _names(b, "links") if "links" in b else cur["links"], **kw)
    except ValueError as ex:
        return _err(str(ex), 422)
    return web.json_response(await asyncio.to_thread(lib.artist, aid))


async def artist_delete(request):
    aid = _aid(request)
    ok = await asyncio.to_thread(_lib(request).artist_delete, aid) if aid else False
    return web.json_response({"ok": True}) if ok else _err("not found", 404)


async def file_credit(request):
    """{artist: a name, '' for none, null for what was found; url: the same for the source}: only the ones given change."""
    fid, b = _fid(request), await _body(request)
    kw = {k: b[k] for k in ("artist", "url") if k in b}
    if any(v is not None and not isinstance(v, str) for v in kw.values()):
        return _err("artist and url are text (or null)")
    try:
        return web.json_response(await asyncio.to_thread(_lib(request).set_credit, fid, **kw))
    except KeyError:
        return _err("not found", 404)
    except ValueError as ex:
        return _err(str(ex), 422)


# ---------------------------------------------------------------- new downloads (downloads.py)
async def inbox_list(request):
    q = request.query
    try:
        offset, limit = int(q.get("offset", 0)), int(q.get("limit", 60))
    except ValueError:
        return _err("bad offset or limit")
    return web.json_response(await asyncio.to_thread(_lib(request).inbox.items, offset, limit))


async def inbox_folders(request):
    return web.json_response({"folders": await asyncio.to_thread(_lib(request).inbox.folders)})


async def inbox_folders_set(request):
    """{add: path} / {remove: path} / {downloads: true|false} / {notify: true|false}"""
    b = await _body(request)
    inbox = _lib(request).inbox
    try:
        if isinstance(b.get("notify"), bool):  # a Windows notification when new ones arrive
            await asyncio.to_thread(_lib(request).settings.update, {"inbox_notify": b["notify"]})
        if isinstance(b.get("downloads"), bool):
            await asyncio.to_thread(_lib(request).settings.update, {"inbox_downloads": b["downloads"]})
            inbox._listing = None
        if b.get("add"):
            await asyncio.to_thread(inbox.add_folder, str(b["add"]))
        if b.get("remove"):
            await asyncio.to_thread(inbox.remove_folder, str(b["remove"]))
    except ValueError as ex:
        return _err(str(ex), 422)
    return web.json_response({"folders": await asyncio.to_thread(inbox.folders)})


def _keys(b) -> list[str]:
    v = b.get("items") or []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ValueError("items is a list of download keys")
    return v[:2000]


async def inbox_file(request):
    from .fileops import OpError
    b = await _body(request)
    try:
        artist = b.get("artist")
        if artist is not None and not isinstance(artist, str):
            raise ValueError("artist is text")
        return web.json_response(await asyncio.to_thread(_lib(request).inbox.file, _keys(b), int(b.get("folder") or 0), str(b.get("sub") or ""), artist))
    except (OpError, ValueError) as ex:
        return _err(str(ex), 422)


async def inbox_dismiss(request):
    b = await _body(request)
    try:
        return web.json_response({"dismissed": await asyncio.to_thread(_lib(request).inbox.dismiss, _keys(b))})
    except ValueError as ex:
        return _err(str(ex))


async def inbox_undismiss(request):
    return web.json_response({"back": await asyncio.to_thread(_lib(request).inbox.undismiss)})


async def inbox_recycle(request):
    b = await _body(request)
    try:
        return web.json_response(await asyncio.to_thread(_lib(request).inbox.recycle, _keys(b)))
    except ValueError as ex:
        return _err(str(ex))


async def inbox_thumb(request):
    path = await asyncio.to_thread(_lib(request).inbox.thumb, request.match_info["key"])
    if not path:
        return web.Response(status=404)
    return web.FileResponse(path, headers={"Cache-Control": "max-age=86400", "Content-Type": "image/webp"})


async def inbox_original(request):
    e = await asyncio.to_thread(_lib(request).inbox.path_of, request.match_info["key"])
    if not e or not os.path.isfile(e["path"]):
        return _err("That download isn't there any more.", 404)
    return web.FileResponse(e["path"], headers={"Cache-Control": "no-cache"})


async def open_url(request):
    """A source or an artist's page, in your web browser (only http and https addresses)."""
    import webbrowser
    from .credits import clean_url
    url = clean_url((await _body(request)).get("url"))
    if not url:
        return _err("That isn't a web address.")
    await asyncio.to_thread(webbrowser.open, url)
    return web.json_response({"ok": True})


# ---------------------------------------------------------------- swipe review, and renaming by a pattern
async def sort_queue(request):
    q = request.query
    try:
        return web.json_response(await asyncio.to_thread(_lib(request).sort_queue, seed=int(q.get("seed", 0)), limit=int(q.get("limit", 40)), **_view(q)))
    except ValueError:
        return _err("bad seed or limit")


async def sort_decide(request):
    b = await _body(request)
    try:
        return web.json_response(await asyncio.to_thread(_lib(request).sort_decide, int(b.get("file") or 0), str(b.get("verdict")), b.get("value")))
    except KeyError:
        return _err("not found", 404)
    except (TypeError, ValueError) as ex:
        return _err(str(ex))


async def sort_undo(request):
    b = await _body(request)
    try:
        await asyncio.to_thread(_lib(request).sort_undo, int(b.get("file") or 0), b.get("was"))
    except (TypeError, ValueError):
        return _err("file: an id")
    return web.json_response({"ok": True})


async def sort_reset(request):
    b = await _body(request)
    v = b.get("view") if isinstance(b.get("view"), dict) else {}
    n = await asyncio.to_thread(_lib(request).sort_reset, str(v.get("sort") or "newest"), int(v["folder"]) if str(v.get("folder", "")).isdigit() else None,
                                v.get("kind") or None, str(v.get("q") or "")[:2000])
    return web.json_response({"reset": n})


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


@_op
async def rename_plan(request, b):
    from . import rename
    p = await asyncio.to_thread(rename.plan, _lib(request).db, await _ids(request, b), str(b.get("pattern") or ""), int(b.get("start", 1) or 0))
    p.pop("items")
    return p


@_op
async def rename_apply(request, b):
    from . import rename
    return await asyncio.to_thread(rename.apply, _lib(request).db, await _ids(request, b), str(b.get("pattern") or ""), int(b.get("start", 1) or 0))


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
    from . import characters as chars
    lib = _lib(request)
    if b.get("action") == "character" and isinstance(b.get("value"), str) and b["value"].strip():  # a new name: made first
        def known():
            with lib.db.read() as c:
                return chars.find(c, b["value"])
        if not await asyncio.to_thread(known):
            try:
                await asyncio.to_thread(lib.character_save, None, b["value"])
            except ValueError as ex:
                from .fileops import OpError
                raise OpError(str(ex))
    return await asyncio.to_thread(bulk.apply, lib.db, await _ids(request, b), str(b.get("action")), b.get("value"))


@_op
async def file_edit(request, b):
    from . import edit
    lib = _lib(request)
    r = await asyncio.to_thread(edit.apply, lib.db, int(request.match_info["id"]), b.get("rotate") or 0, bool(b.get("flip")),
                                b.get("crop"), bool(b.get("copy")), b.get("of"))
    lib.thumbs.nudge()
    lib.meta.nudge()
    return r


async def history(request):
    from . import fileops
    return web.json_response({"ops": await asyncio.to_thread(fileops.history, _lib(request).db)})


async def undo(request):
    from . import fileops
    try:
        oid = int(request.match_info["oid"])
        if not _is_local(request):  # a phone or tablet undoes only what a device can do
            def kind():
                with _lib(request).db.read() as c:
                    r = c.execute("SELECT kind FROM ops WHERE id=?", (oid,)).fetchone()
                return r[0] if r else None
            if await asyncio.to_thread(kind) not in remote.UNDO:
                return _err("Undo that on the PC.", 403)
        return web.json_response(await asyncio.to_thread(fileops.undo, _lib(request).db, oid))
    except fileops.OpError as ex:
        return _err(str(ex), 422)
    except ValueError:
        return _err("bad id")


async def albums(request):
    lib = _lib(request)
    def both():
        out = lib.albums()
        return {"albums": out, "groups": lib.album_groups(out)}  # (groups: the names with albums inside, counted whole)
    return web.json_response(await asyncio.to_thread(both))


async def album_move(request):
    """{path, to}: an album, with the albums inside it, to another name (into another album, or to the top)."""
    b = await _body(request)
    try:
        return web.json_response(await asyncio.to_thread(_lib(request).album_move, str(b.get("path") or ""), str(b.get("to") or "")))
    except KeyError:
        return _err("not found", 404)
    except ValueError as ex:
        return _err(str(ex), 422)


async def folder_dirs(request):
    try:
        fid = int(request.match_info["fid"])
    except ValueError:
        return _err("bad id")
    return web.json_response({"dirs": await asyncio.to_thread(_lib(request).dirs, fid)})


async def album_create(request):
    b = await _body(request)
    try:  # {name, query}: with a query, a smart album (the search, kept)
        if b.get("query") is not None and not isinstance(b.get("query"), str):
            raise ValueError("query: a search")
        return web.json_response(await asyncio.to_thread(_lib(request).album_save, None, b.get("name"), query=b.get("query")))
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
        kw = {k: b[k] for k in ("cover", "query") if k in b}
        if kw.get("query") is not None and not isinstance(kw.get("query"), str):
            raise ValueError("query: a search")
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
    if _EAGLE.get("state") in ("reading", "applying"):
        return _err("An Eagle library is being read or brought in: wait for it to finish.", 409)
    lib = _lib(request)
    _EAGLE.clear()
    _EAGLE.update(state="reading", path=lib_path, done=0, total=0, title=f"Reading {os.path.basename(lib_path)[: -len('.library')] or lib_path}")

    def run():
        try:
            _EAGLE["total"] = eagle.count_items(lib_path)
            plan = eagle.plan_import(lib.db, lib_path, progress=lambda n: _EAGLE.update(done=n), stop=lambda: _EAGLE.get("stop"))
            _EAGLE.update(state="ready", plan=plan, summary=eagle.summary(plan))
        except eagle.Stopped:
            _EAGLE.clear()
            _EAGLE.update(state="stopped")
        except Exception as ex:
            _EAGLE.update(state="failed", error=str(ex))
    import threading
    threading.Thread(target=run, name="eagle", daemon=True).start()
    return web.json_response({"state": "reading", "path": lib_path})


async def eagle_read_status(request):
    return web.json_response({k: v for k, v in _EAGLE.items() if k != "plan"} or {"state": "idle"})


async def eagle_stop(request):
    """Stop reading, or copying: what's copied so far stays, with its marks (one History step, as ever)."""
    if _EAGLE.get("state") not in ("reading", "applying"):
        return _err("Nothing is being read or brought in.", 409)
    _EAGLE["stop"] = True
    return web.json_response({"ok": True})


async def eagle_seen(request):
    """The end of a job told (its dialog showed it, or the status bar did): not told again."""
    _EAGLE["seen"] = True
    return web.json_response({"ok": True})


def eagle_job() -> dict | None:
    """The Eagle library being read or brought in, for the status bar (while its dialog is hidden); once it's over,
    how it ended, until that's been told (the window may have been reloaded meanwhile: the result isn't lost)."""
    if _EAGLE.get("state") in ("done", "failed") and _EAGLE.get("ended") and not _EAGLE.get("seen"):
        return {"state": _EAGLE["state"], "title": _EAGLE.get("ended"), "result": _EAGLE.get("result"), "error": _EAGLE.get("error")}
    if _EAGLE.get("state") not in ("reading", "applying"):
        return None
    return {k: _EAGLE.get(k) for k in ("state", "title", "done", "total", "bytes", "bytes_total", "stop")}


async def eagle_apply(request):
    """Bring the read Eagle library in, in the background (GET /api/eagle/read follows it):
    {target: "here", copy_to: folder id, sub}: its marks onto the files this library has, the rest copied into that folder;
    {target: "new", path, name}: a new library of its own in that folder, every item copied in, laid out by Eagle folder."""
    import threading
    import time
    from . import eagle, libraries
    b = await _body(request)
    plan = _EAGLE.get("plan")
    if _EAGLE.get("state") != "ready" or not plan:
        return _err("Read an Eagle library first.", 409)
    lib = _lib(request)
    new = b.get("target") == "new"
    made = None
    _EAGLE.update(state="applying", done=0, total=0, bytes=0, bytes_total=0, error=None, stop=False,
                  title=f"Bringing in {plan['name']}")  # at once: a second click meanwhile is refused, not done twice
    if new:
        path = str(b.get("path") or "")
        bad = None
        if await asyncio.to_thread(libraries.find, path):
            bad = "That folder is a library already: open it, then bring the Eagle library into it."
        else:
            try:
                made = await asyncio.to_thread(libraries.create, path, b.get("name") or plan["name"])
            except (ValueError, OSError) as ex:
                bad = str(ex)
        if bad:
            _EAGLE["state"] = "ready"
            return _err(bad, 422)
        work = eagle.for_new_library(plan)
    else:
        work = plan
    try:
        copy_to = int(b["copy_to"]) if not new and b.get("copy_to") and plan["missing"] else None
    except (TypeError, ValueError):
        _EAGLE["state"] = "ready"
        return _err("copy_to: a folder's id", 400)
    sub = b.get("sub")  # None: "From Eagle - <name>"; "": the library folder itself
    total = len(work["missing"]) if new or copy_to else 0
    _EAGLE.update(total=total, bytes_total=sum(it.get("size") or 0 for it in work["missing"]) if total else 0)
    progress = lambda n, b: _EAGLE.update(done=n, bytes=b)  # noqa: E731
    stop = lambda: _EAGLE.get("stop")  # noqa: E731

    def run():
        try:
            if new:
                from .db import Database
                root = made["root"]
                db = Database(os.path.join(libraries.data_dir(root), "library.db"))
                with db.write() as c:
                    fid = c.execute("INSERT INTO folders (path, added) VALUES (?, ?)", (os.path.normpath(root), time.time())).lastrowid
                libraries.update_info(root, opened=time.time())
                copied = eagle.copy_in(db, work, fid, "", progress=progress, stop=stop)
                r = eagle.apply_import(db, work)
                result = {**r, "copied": copied, "failed": work.get("failed") or [], "stopped": work.get("stopped") or 0,
                          "library": {"root": root, "name": made["name"]}}
            else:
                copied = eagle.copy_in(lib.db, work, copy_to, None if sub is None else str(sub), progress=progress, stop=stop) if copy_to else 0
                r = eagle.apply_import(lib.db, work)
                result = {**r, "copied": copied, "failed": work.get("failed") or [], "stopped": work.get("stopped") or 0}
                lib.thumbs.nudge()
                lib.meta.nudge()
                lib.db.user_change()
            _EAGLE.clear()
            _EAGLE.update(state="done", result=result, ended=f"Brought in {plan['name']}")
        except Exception as ex:
            _EAGLE.clear()
            _EAGLE.update(state="failed", error=str(ex)[:300], ended=f"Bringing in {plan['name']}")
    threading.Thread(target=run, name="eagle-import", daemon=True).start()
    return web.json_response({"state": "applying", "total": total})


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
    shown = {k: v for k, v in lib.settings.all().items() if k != "remote_devices"}  # (the devices' keys' hashes stay in the file)
    return {"settings": shown, "ml_data_found": lib.models.data_dir(), "data_dir": lib.data_dir,
            "ffmpeg_found": find_ffmpeg(lib.settings.get("ffmpeg")), "last_backup": last[-1] if last else None, "version": VERSION,
            "shortcut": shortcut.exists() if os.name == "nt" else None, "update": dict(updater.info(), can_restart=can_restart)}


def _can_relaunch(app) -> bool:
    return bool(app[QUIT] and app[RELAUNCH])


def _settings_view(request) -> dict:
    return _settings_out(_lib(request), request.app[UPDATER], _can_relaunch(request.app) and bool(request.app[UPDATER].root))


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
    up = request.app[UPDATER]
    if not (up.root and up.staged() and _can_relaunch(request.app)):
        return _err("There's no update waiting, or this Gallery can't restart itself: close it and start it again.", 409)
    request.app[RELAUNCH](_lib(request).reopen)  # the launcher waits for this one to close, then swaps the update in
    asyncio.get_running_loop().call_later(0.3, request.app[QUIT])  # after this answer has gone
    return web.json_response({"ok": True})


# ---------------------------------------------------------------- libraries (see libraries.py)
def _libraries_out(app) -> dict:
    from . import libraries
    lib = app[LIB]
    cur = {"root": lib.root, "name": lib.name, "data_dir": lib.data_dir, "bringing": lib.bringing}
    if lib.root:
        cur["brought"] = libraries.info(lib.root).get("brought")
    return {"current": cur, "recent": [dict(r, open=bool(lib.root) and libraries.same(r["root"], lib.root)) for r in libraries.recent()],
            "note": lib.note, "can_switch": _can_relaunch(app)}


async def libraries_get(request):
    return web.json_response(await asyncio.to_thread(_libraries_out, request.app))


def _switch(request, target: list[str], data: str):
    """Start a Gallery on another library and close this one -> an error response, or None."""
    from . import libraries
    if not _can_relaunch(request.app):
        return _err("This Gallery can't switch libraries (it runs as a server only): start it again with --library.", 409)
    if libraries.same(data, _lib(request).data_dir):
        return _err("That library is open already.", 409)
    if libraries.in_use(data):
        return _err("That library is open in another Gallery window already.", 409)
    request.app[RELAUNCH](target)
    asyncio.get_running_loop().call_later(0.3, request.app[QUIT])  # after this answer has gone
    return None


async def library_open(request):
    from . import libraries
    from .config import default_data_dir
    b = await _body(request)
    if b.get("local"):
        target, data = ["--local"], default_data_dir()
    else:
        root = await asyncio.to_thread(libraries.find, str(b.get("path") or ""))
        if not root:
            return _err("That folder isn't a library yet (it has no .mlgallery folder): use New library to make it one.", 422)
        target, data = ["--library", root], libraries.data_dir(root)
    bad = _switch(request, target, data)
    return bad or web.json_response({"ok": True})


async def library_new(request):
    """Make a folder a library (bringing this library's marks for the same files, if asked), then open it."""
    from . import libraries
    b = await _body(request)
    lib = _lib(request)
    bring = {"data": lib.data_dir, "name": lib.name or "this PC's library"} if b.get("bring") else None
    try:
        made = await asyncio.to_thread(libraries.create, str(b.get("path") or ""), b.get("name"), bring)
        if made["existing"] and bring:  # a library already: the marks come when it next opens
            await asyncio.to_thread(libraries.update_info, made["root"], bring_from=bring)
    except (ValueError, OSError) as ex:
        return _err(str(ex), 422)
    if not _can_relaunch(request.app):
        return web.json_response(dict(made, switched=False))
    bad = _switch(request, ["--library", made["root"]], libraries.data_dir(made["root"]))
    return bad or web.json_response(dict(made, switched=True))


async def library_forget(request):
    from . import libraries
    b = await _body(request)
    await asyncio.to_thread(libraries.forget, str(b.get("path") or ""))
    return web.json_response(await asyncio.to_thread(_libraries_out, request.app))


async def reveal_folder(request):
    """Open a library folder in Explorer (only one that's in the library), or {rel} a folder inside it."""
    f = next((f for f in await asyncio.to_thread(_lib(request).folders) if f["id"] == int(request.match_info["fid"])), None)
    rel = str((await _body(request)).get("rel") or "") if request.can_read_body else ""
    path = os.path.normpath(os.path.join(f["path"], rel)) if f else ""
    try:
        inside = bool(f) and os.path.commonpath([path, os.path.normpath(f["path"])]) == os.path.normpath(f["path"])
    except ValueError:  # (another drive)
        inside = False
    if not inside or not os.path.isdir(path):
        return _err("That folder isn't reachable right now.", 404)
    import subprocess
    subprocess.Popen(["explorer", path])
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
    from .thumbs import NOT_IN_BROWSER
    if os.path.splitext(path)[1].lower() in NOT_IN_BROWSER and request.query.get("raw") != "1":
        shown = await asyncio.to_thread(_lib(request).thumbs.display, fid)  # HEIC, TIFF: a large WebP of it
        if shown:
            return web.FileResponse(shown, headers={"Cache-Control": "no-cache", "Content-Type": "image/webp"})
    return web.FileResponse(path, headers={"Cache-Control": "no-cache"})  # ranges supported: videos seek


async def serve(lib: Library, port: int = 0, token: str | None = None, pick_folder=None, quit=None, relaunch=None):
    """Start serving on 127.0.0.1 -> (runner, port, token)."""
    app = make_app(lib, token, pick_folder=pick_folder, quit=quit, relaunch=relaunch)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    real = site._server.sockets[0].getsockname()[1]
    app[RUNNER] = runner
    if lib.settings.get("remote"):  # Phone and tablet was on: paired devices can come back
        await remote_start(app)
    return runner, real, app[TOKEN]
