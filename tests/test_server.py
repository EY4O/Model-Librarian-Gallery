"""The server: the token and Host checks, and the API the interface uses."""
import asyncio
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

from conftest import image
from mlgallery import scan
from mlgallery.server import make_app

TOKEN = "test-token"


def run(lib, fn, pick_folder=None):
    """Call fn(client) against the app on a real local socket."""
    async def go():
        client = TestClient(TestServer(make_app(lib, TOKEN, pick_folder=pick_folder), host="127.0.0.1"))
        await client.start_server()
        try:
            return await fn(client)
        finally:
            await client.close()
    return asyncio.run(go())


H = {"X-MLG-Token": TOKEN}


def indexed(lib, media, n=3):
    now = time.time()
    for i in range(n):
        p = image(media / f"{i}.png", (120, 90), seed=i)
        t = now - i * 3600
        import os
        os.utime(p, (t, t))
    with lib.db.write() as c:
        fid = c.execute("INSERT INTO folders (path, added) VALUES (?, ?)", (str(media), now)).lastrowid
    scan.scan_folder(lib.db, {"id": fid, "path": str(media)})
    return fid


def test_api_needs_the_token(lib):
    async def fn(c):
        assert (await c.get("/api/status")).status == 403
        assert (await c.get("/api/status", headers={"X-MLG-Token": "wrong"})).status == 403
        assert (await c.get("/api/status", headers=H)).status == 200
        assert (await c.get(f"/api/status?t={TOKEN}")).status == 200  # <img> and <video> can't send headers
    run(lib, fn)


def test_other_hosts_are_refused_even_with_the_token(lib):
    """A web page can point a name it controls at 127.0.0.1 (DNS rebinding): the Host shows it."""
    async def fn(c):
        r = await c.get("/api/status", headers={**H, "Host": "evil.example:1234"})
        assert r.status == 403
        r = await c.get("/api/status", headers={**H, "Host": "localhost"})
        assert r.status == 200
    run(lib, fn)


def test_index_page_needs_the_token_and_gets_it_filled_in(lib):
    async def fn(c):
        r = await c.get("/")
        assert r.status == 403
        r = await c.get(f"/?t={TOKEN}")
        body = await r.text()
        assert r.status == 200 and f'content="{TOKEN}"' in body and "{{" not in body
        assert r.headers["Cache-Control"] == "no-store"
    run(lib, fn)


def test_ui_files_are_served(lib):
    async def fn(c):
        for p in ("/ui/app.js", "/ui/app.css", "/ui/icon.png"):
            assert (await c.get(p)).status == 200, p
        assert (await c.get("/ui/../server.py")).status in (403, 404)
    run(lib, fn)


def test_files_days_thumb_and_original(lib, media):
    indexed(lib, media, 3)

    async def fn(c):
        r = await (await c.get("/api/files?limit=2", headers=H)).json()
        assert r["total"] == 3 and len(r["items"]) == 2 and r["items"][0]["name"] == "0.png"
        assert {"id", "name", "rel", "kind", "width", "height", "size", "mtime", "thumb", "folder_id"} <= set(r["items"][0])
        d = await (await c.get("/api/days?sort=newest", headers=H)).json()
        assert sum(x["n"] for x in d["days"]) == 3
        fid = r["items"][0]["id"]
        t = await c.get(f"/api/thumb/{fid}?t={TOKEN}")
        assert t.status == 200 and t.content_type == "image/webp"
        o = await c.get(f"/api/file/{fid}?t={TOKEN}", headers={"Range": "bytes=0-7"})
        assert o.status == 206 and (await o.read()).startswith(b"\x89PNG")
        assert (await c.get("/api/file/99999", headers=H)).status == 404
        assert (await c.get("/api/thumb/abc", headers=H)).status == 400
        assert (await c.get("/api/files?offset=x", headers=H)).status == 400
    run(lib, fn)


def test_add_and_remove_folders(lib, media):
    image(media / "a.png")

    async def fn(c):
        r = await c.post("/api/folders", json={"path": str(media / "missing")}, headers=H)
        assert r.status == 422 and "exist" in (await r.json())["error"]
        r = await c.post("/api/folders", json={"path": str(media)}, headers=H)
        f = (await r.json())["folder"]
        assert r.status == 200 and f["path"] == str(media)
        r = await c.post("/api/folders", json={"path": str(media)}, headers=H)
        assert r.status == 422  # already there (overlaps itself)
        assert (await c.delete(f"/api/folders/{f['id']}", headers=H)).status == 200
        assert (await c.delete(f"/api/folders/{f['id']}", headers=H)).status == 404
    run(lib, fn)


def test_folder_picker_only_in_the_window(lib, media):
    async def no_window(c):
        assert (await c.post("/api/folders/pick", headers=H)).status == 501
    run(lib, no_window)

    async def window(c):
        r = await c.post("/api/folders/pick", headers=H)
        assert (await r.json())["path"] == str(media)
    run(lib, window, pick_folder=lambda: str(media))


def test_reveal_only_indexed_files(lib, media, monkeypatch):
    indexed(lib, media, 1)
    opened = []
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: opened.append(args))

    async def fn(c):
        fid = (await (await c.get("/api/files", headers=H)).json())["items"][0]["id"]
        r = await c.post(f"/api/file/{fid}/reveal", headers=H)
        if r.status == 501:
            pytest.skip("Windows only")
        assert r.status == 200 and opened and opened[0][0] == "explorer" and opened[0][2].endswith("0.png")
        assert (await c.post("/api/file/424242/reveal", headers=H)).status == 404
    run(lib, fn)


def test_info_marks_tags_workflow_and_search(lib, media):
    import random
    from sample_media import generation, make_image
    make_image(str(media / "a.png"), (128, 128), 0, generation(0, random.Random(0), (128, 128)))
    make_image(str(media / "b.png"), (128, 128), 1)
    with lib.db.write() as c:
        fid = c.execute("INSERT INTO folders (path, added) VALUES (?, ?)", (str(media), time.time())).lastrowid
    scan.scan_folder(lib.db, {"id": fid, "path": str(media)})
    with lib.db.read() as c:
        rows = c.execute("SELECT f.id, f.rel, f.kind, d.path FROM files f JOIN folders d ON d.id=f.folder_id").fetchall()
    for r in rows:
        lib.meta.read_one(r)

    async def fn(c):
        items = (await (await c.get("/api/files", headers=H)).json())["items"]
        a = next(i for i in items if i["name"] == "a.png")
        info = await (await c.get(f"/api/file/{a['id']}/info", headers=H)).json()
        assert info["generation"]["source"] == "comfyui" and info["uses"][0]["name"] == "sample_xl_base"
        assert (await c.get("/api/file/999/info", headers=H)).status == 404
        r = await c.post(f"/api/file/{a['id']}/mark", json={"rating": 5, "favorite": True}, headers=H)
        assert await r.json() == {"rating": 5, "favorite": 1}
        assert (await c.post(f"/api/file/{a['id']}/mark", json={"rating": "x"}, headers=H)).status == 400
        assert (await c.post("/api/file/999/mark", json={"rating": 1}, headers=H)).status == 404
        r = await c.post(f"/api/file/{a['id']}/tags", json={"add": ["keep"], "remove": ["sample"]}, headers=H)
        assert "keep" in {t["name"] for t in (await r.json())["tags"]}
        assert (await c.post(f"/api/file/{a['id']}/tags", json={"add": "keep"}, headers=H)).status == 400
        wf = await c.get(f"/api/file/{a['id']}/workflow?download=1&t={TOKEN}")
        assert wf.status == 200 and "attachment" in wf.headers["Content-Disposition"] and (await wf.json())["nodes"]
        api_graph = await (await c.get(f"/api/file/{a['id']}/workflow?part=prompt", headers=H)).json()
        assert api_graph["13"]["class_type"] == "KSampler"
        b = next(i for i in items if i["name"] == "b.png")
        assert (await c.get(f"/api/file/{b['id']}/workflow", headers=H)).status == 404
        s = await (await c.get("/api/suggest?field=model&q=sample", headers=H)).json()
        assert s["items"][0]["name"] == "sample_xl_base"
        found = await (await c.get("/api/files?q=" + "is:fav%20tag:keep", headers=H)).json()
        assert [i["name"] for i in found["items"]] == ["a.png"] and found["items"][0]["rating"] == 5
        assert (await c.post(f"/api/file/{b['id']}/viewed", headers=H)).status == 200
        recent = await (await c.get("/api/files?sort=viewed", headers=H)).json()
        assert [i["name"] for i in recent["items"]] == ["b.png"]
        days = await (await c.get("/api/days?q=is:fav", headers=H)).json()
        assert sum(d["n"] for d in days["days"]) == 1
    run(lib, fn)
