"""The Eagle bridge, on made-up Eagle libraries (the layout of a real Eagle 4 library) and a stand-in for Eagle's API.
No real Eagle library or Eagle app is ever touched."""
import asyncio
import json
import os
import shutil
import socket
import time

import pytest
from aiohttp import web

from conftest import image
from mlgallery import eagle, fileops, scan
from test_meta import q


def make_eagle(root, items, folders=None, name="Test"):
    """Name.library/ with metadata.json, images/<ID>.info/{file, thumbnail, metadata.json}."""
    lib = root / f"{name}.library"
    (lib / "images").mkdir(parents=True)
    (lib / "metadata.json").write_text(json.dumps({"applicationVersion": "4.0.0", "folders": folders or [], "smartFolders": [],
                                                   "quickAccess": [], "tagsGroups": [], "modificationTime": 1}))
    (lib / "tags.json").write_text('{"historyTags":[],"starredTags":[]}')
    for it in items:
        d = lib / "images" / f"{it['id']}.info"
        d.mkdir()
        stem, ext = os.path.splitext(it["file"])
        src = it.get("src")
        if src:
            shutil.copy2(src, d / it["file"])
        else:
            image(d / it["file"], (64, 64), seed=it.get("seed", 1))
        image(d / f"{stem}_thumbnail.png", (16, 16), seed=99)
        meta = {"id": it["id"], "name": stem, "size": os.path.getsize(d / it["file"]), "ext": ext[1:], "tags": it.get("tags", []),
                "folders": it.get("folders", []), "isDeleted": it.get("deleted", False), "url": it.get("url", ""), "annotation": it.get("note", ""),
                "modificationTime": 1, "width": 64, "height": 64}
        if "star" in it:
            meta["star"] = it["star"]
        (d / "metadata.json").write_text(json.dumps(meta))
    return lib


FOLDERS = [{"id": "F1", "name": "Characters", "children": [{"id": "F2", "name": "Aurelia", "children": []}]}, {"id": "F3", "name": "Coast", "children": []}]


def setup(lib, tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    a = image(media / "one.png", (64, 64), seed=1)
    b = image(media / "two.png", (64, 64), seed=2)
    f = lib.add_folder(str(media))
    scan.scan_folder(lib.db, f)
    ids = {r["name"]: r["id"] for r in lib.files()["items"]}
    eag = make_eagle(tmp_path / "eagle", [
        {"id": "AAA1", "file": "renamed in eagle.png", "src": a, "tags": ["dusk", "keeper"], "folders": ["F2", "F3"], "star": 4, "note": "the good one"},
        {"id": "AAA2", "file": "two.png", "src": b, "tags": ["dusk"]},
        {"id": "AAA3", "file": "only in eagle.png", "seed": 7, "tags": ["new"], "folders": ["F3"], "star": 5},
        {"id": "AAA4", "file": "gone.png", "seed": 8, "deleted": True},
        {"id": "AAA5", "file": "notes.txt.png", "seed": 9},
    ], FOLDERS)
    return media, f, ids, eag


def test_find_the_library(tmp_path):
    lib = make_eagle(tmp_path, [])
    assert eagle.library_root(str(lib)) == str(lib) and eagle.library_root(str(tmp_path)) == str(lib)
    with pytest.raises(eagle.EagleError, match="isn't an Eagle library"):
        eagle.library_root(str(tmp_path / "nope"))
    make_eagle(tmp_path, [], name="Second")
    with pytest.raises(eagle.EagleError, match="more than one"):
        eagle.library_root(str(tmp_path))
    assert eagle.folder_names(str(lib)) == {}


def test_plan_matches_by_fingerprint_whatever_the_name(lib, tmp_path):
    media, f, ids, eag = setup(lib, tmp_path)
    before = {p: os.path.getmtime(p) for p in eag.rglob("*")}
    plan = eagle.plan_import(lib.db, str(eag))
    s = eagle.summary(plan)
    assert s["matched"] == 2 and s["missing"] == 2  # the deleted item is left out
    assert {i["file_id"] for i in plan["matched"]} == {ids["one.png"], ids["two.png"]}
    assert s["albums"] == ["Characters / Aurelia", "Coast"] and s["rated"] == 2 and s["notes"] == 1
    assert {p: os.path.getmtime(p) for p in eag.rglob("*")} == before  # reading changed nothing


def test_apply_tags_albums_ratings_notes_and_undo(lib, tmp_path):
    media, f, ids, eag = setup(lib, tmp_path)
    lib.mark(ids["two.png"], rating=2)  # yours: Eagle's (none here) wouldn't replace it anyway
    plan = eagle.plan_import(lib.db, str(eag))
    r = eagle.apply_import(lib.db, plan)
    assert r["files"] == 2 and r["ratings"] == 1 and r["notes"] == 1
    one = lib.info(ids["one.png"])
    assert {t["name"] for t in one["tags"]} == {"dusk", "keeper"} and one["rating"] == 4 and one["note"]["note"] == "the good one"
    assert {a["name"] for a in one["albums"]} == {"Characters / Aurelia", "Coast"}
    assert q(lib, "tag:dusk") == ["one.png", "two.png"] and q(lib, "album:coast") == ["one.png"] and q(lib, "keeper") == ["one.png"]
    assert lib.info(ids["two.png"])["rating"] == 2
    fileops.undo(lib.db, r["op"])
    one = lib.info(ids["one.png"])
    assert one["tags"] == [] and one["rating"] == 0 and one["note"] is None and one["albums"] == []
    assert lib.albums() == [] and lib.tags("") == []


def test_your_rating_and_note_win(lib, tmp_path):
    media, f, ids, eag = setup(lib, tmp_path)
    lib.mark(ids["one.png"], rating=1)
    with lib.db.write() as c:
        c.execute("INSERT INTO file_notes (file_id, note, source) VALUES (?, 'mine', 'user')", (ids["one.png"],))
    eagle.apply_import(lib.db, eagle.plan_import(lib.db, str(eag)))
    one = lib.info(ids["one.png"])
    assert one["rating"] == 1 and one["note"]["note"] == "mine"


def test_copy_in_what_the_gallery_lacks(lib, tmp_path):
    media, f, ids, eag = setup(lib, tmp_path)
    plan = eagle.plan_import(lib.db, str(eag))
    n = eagle.copy_in(lib.db, plan, f["id"])
    assert n == 2 and (media / "From Eagle - Test" / "only in eagle.png").is_file()
    assert (eag / "images" / "AAA3.info" / "only in eagle.png").is_file()  # copied, not moved: Eagle keeps its own
    r = eagle.apply_import(lib.db, plan)
    assert r["files"] == 4
    assert lib.files()["total"] == 4 and q(lib, "tag:new") == ["only in eagle.png"] and q(lib, "rating:5") == ["only in eagle.png"]


def test_the_gallery_still_never_indexes_an_eagle_library(lib, tmp_path):
    eag = make_eagle(tmp_path, [{"id": "X", "file": "a.png"}])
    with pytest.raises(ValueError, match="Eagle"):
        lib.add_folder(str(eag))


# ---------------------------------------------------------------- Send to Eagle, against a stand-in for its API
def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def fake_eagle(token=None):
    got = []

    def ok(data):
        return web.json_response({"status": "success", "data": data})

    async def folders(request):
        if token and request.query.get("token") != token:
            return web.json_response({"status": "error", "message": "token required"})
        return ok([{"id": "E1", "name": "AI", "children": [{"id": "E2", "name": "Coast", "children": []}]}])

    async def add(request):
        got.append(await request.json())
        return ok(None)
    app = web.Application()
    app.router.add_get("/api/folder/list", folders)
    app.router.add_post("/api/item/addFromPaths", add)
    runner = web.AppRunner(app)
    await runner.setup()
    port = free_port()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner, f"http://127.0.0.1:{port}", got


def test_send_to_eagle():
    async def go():
        runner, url, got = await fake_eagle(token="secret-test-token")
        try:
            with pytest.raises(eagle.EagleError, match="token"):
                await eagle.eagle_folders(url, "")
            assert await eagle.eagle_folders(url, "secret-test-token") == [{"id": "E1", "name": "AI"}, {"id": "E2", "name": "AI / Coast"}]
            r = await eagle.send(url, "secret-test-token", [{"path": "C:/x/a.png", "name": "a.png", "tags": ["dusk"], "note": "a prompt"}], "E2")
            assert r == {"sent": 1}
            assert got == [{"items": [{"path": "C:/x/a.png", "name": "a", "tags": ["dusk"], "annotation": "a prompt"}], "folderId": "E2"}]
        finally:
            await runner.cleanup()
        with pytest.raises(eagle.EagleError, match="isn't running"):
            await eagle.eagle_folders(f"http://127.0.0.1:{free_port()}", "")
    asyncio.run(go())


def test_note_for_eagle():
    note = eagle.note_for({"generation": {"positive": "a lighthouse", "negative": "blurry", "seed": 1, "steps": 8, "cfg": 1.0, "sampler": "euler"},
                           "uses": [{"name": "sample_xl", "strength": None}, {"name": "aurelia", "strength": 0.7}]})
    assert note.splitlines() == ["a lighthouse", "Negative: blurry", "Seed 1, Steps 8, CFG 1.0, Sampler euler", "Models: sample_xl, aurelia 0.7"]
    assert eagle.note_for({}) == ""
