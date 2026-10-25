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
from mlgallery.library import Library
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
        meta = {"id": it["id"], "name": it.get("eagle_name", stem), "size": os.path.getsize(d / it["file"]), "ext": ext[1:], "tags": it.get("tags", []),
                "folders": it.get("folders", []), "isDeleted": it.get("deleted", False), "url": it.get("url", ""), "annotation": it.get("note", ""),
                "modificationTime": 1, "width": 64, "height": 64}
        if "star" in it:
            meta["star"] = it["star"]
        if "palettes" in it:
            meta["palettes"] = it["palettes"]
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
    # laid out by Eagle folder (an item in none goes to Unfiled), inside a folder named after the Eagle library
    assert n == 2 and (media / "From Eagle - Test" / "Coast" / "only in eagle.png").is_file() and (media / "From Eagle - Test" / "Unfiled" / "notes.txt.png").is_file()
    assert (eag / "images" / "AAA3.info" / "only in eagle.png").is_file()  # copied, not moved: Eagle keeps its own
    r = eagle.apply_import(lib.db, plan)
    assert r["files"] == 4
    assert lib.files()["total"] == 4 and q(lib, "tag:new") == ["only in eagle.png"] and q(lib, "rating:5") == ["only in eagle.png"]
    # Undo takes it all back: the copies go to the Gallery's Trash, in the same step
    u = fileops.undo(lib.db, r["op"])
    assert u["trashed"] == 2 and lib.files()["total"] == 2 and lib.status()["trash"] == 2
    assert not (media / "From Eagle - Test" / "Coast" / "only in eagle.png").exists() and (eag / "images" / "AAA3.info" / "only in eagle.png").is_file()
    assert [o["kind"] for o in fileops.history(lib.db)] == ["eagle"]


NESTED = [{"id": "S", "name": "Sample", "children": [{"id": "S2", "name": "Stola", "children": []}]},
          {"id": "W", "name": 'What? A "folder": yes', "children": []}]


def leaving(tmp_path):
    """An Eagle library like the sample made for this: nested folders, an item in two, names Eagle changed, links, colours."""
    return make_eagle(tmp_path / "eagle", [
        {"id": "B1", "file": "711588397_n.jpg", "seed": 11, "folders": ["S"], "eagle_name": "Estinien",
         "palettes": [{"color": [20, 13, 23], "ratio": 50, "$$hashKey": "object:1"}, {"color": [226, 209, 205], "ratio": 13}]},
        {"id": "B2", "file": "708952787_n.jpg", "seed": 12, "folders": ["S2", "W"], "star": 5, "url": "https://example.com/post/2"},
        {"id": "B3", "file": "708547164_n.jpg", "seed": 13, "note": "Estinien is cool", "url": "https://example.com/post/3"},
        {"id": "B4", "file": "other.jpg", "seed": 14, "folders": ["S"], "eagle_name": "Estinien"},  # the same name in the same folder
        {"id": "B5", "file": "con.png", "seed": 15, "folders": ["W"], "eagle_name": "CON"},  # a name Windows refuses
        {"id": "B6", "file": "in the trash.png", "seed": 16, "folders": ["S"], "deleted": True},
    ], NESTED, name="Testing Library")


def test_layout_names_links_and_colours(lib, tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    f = lib.add_folder(str(media))
    eag = leaving(tmp_path)
    before = {p: os.path.getmtime(p) for p in eag.rglob("*")}
    plan = eagle.plan_import(lib.db, str(eag))
    s = eagle.summary(plan)
    assert (s["all"], s["missing"], s["unfiled"], s["urls"]) == (5, 5, 1, 2) and "Sample / Stola" in s["albums"]
    assert eagle.copy_in(lib.db, plan, f["id"], "") == 5 and plan["failed"] == []
    got = sorted(p.relative_to(media).as_posix() for p in media.rglob("*") if p.is_file())
    assert got == ["Sample/Estinien (2).jpg", "Sample/Estinien.jpg", "Sample/Stola/708952787_n.jpg", "Unfiled/708547164_n.jpg",
                   "What_ A _folder__ yes/_CON.png"]  # by Eagle folder (the first of two), Eagle's names, made safe for Windows
    src = eag / "images" / "B1.info" / "711588397_n.jpg"
    assert os.path.getmtime(media / "Sample" / "Estinien.jpg") == pytest.approx(os.path.getmtime(src), abs=0.01)  # its date kept
    r = eagle.apply_import(lib.db, plan)
    assert r["files"] == 5
    ids = {x["name"]: x["id"] for x in lib.files()["items"]}
    two = lib.info(ids["708952787_n.jpg"])
    assert {a["name"] for a in two["albums"]} == {"Sample / Stola", 'What? A "folder": yes'} and two["rating"] == 5  # every folder an album too
    assert two["note"]["note"] == "https://example.com/post/2"  # a link alone is the note
    assert lib.info(ids["708547164_n.jpg"])["note"]["note"] == "Estinien is cool\nhttps://example.com/post/3"  # its own line, at the end
    one = lib.info(ids["Estinien.jpg"])
    assert [(c["hex"], c["ratio"]) for c in one["colors"]] == [("#140d17", 0.5), ("#e2d1cd", 0.13)]  # Eagle's colours, kept
    assert q(lib, "color:#140d17") == ["Estinien.jpg"]
    assert {p: os.path.getmtime(p) for p in eag.rglob("*")} == before  # Eagle's library: untouched


def test_a_copy_that_doesnt_match_is_left_out(lib, tmp_path, monkeypatch):
    media = tmp_path / "media"
    media.mkdir()
    f = lib.add_folder(str(media))
    plan = eagle.plan_import(lib.db, str(leaving(tmp_path)))
    real = shutil.copy2

    def bad(src, dst):
        real(src, dst)
        if "Estinien.jpg" in str(dst):
            with open(dst, "r+b") as fh:
                fh.write(b"broken")  # same size, other bytes
    monkeypatch.setattr(eagle.shutil, "copy2", bad)
    assert eagle.copy_in(lib.db, plan, f["id"], "") == 4
    assert plan["failed"] == ["Estinien.jpg"] and not (media / "Sample" / "Estinien.jpg").exists()


def test_bring_it_in_as_a_new_library(lib, tmp_path):
    from aiohttp.test_utils import TestClient, TestServer
    from mlgallery import libraries
    from mlgallery.db import Database
    from mlgallery.server import make_app
    eag = leaving(tmp_path)
    target = tmp_path / "Drive" / "From Eagle"
    target.mkdir(parents=True)
    H = {"X-MLG-Token": "t"}

    async def wait(c, states):
        for _ in range(200):
            st = await (await c.get("/api/eagle/read", headers=H)).json()
            if st["state"] in states:
                return st
            await asyncio.sleep(0.05)
        raise AssertionError(st)

    async def go():
        c = TestClient(TestServer(make_app(lib, "t"), host="127.0.0.1"))
        await c.start_server()
        try:
            assert (await c.post("/api/eagle/read", headers=H, json={"path": str(eag)})).status == 200
            assert (await wait(c, ("ready",)))["summary"]["all"] == 5
            r = await c.post("/api/eagle/apply", headers=H, json={"target": "new", "path": str(target), "name": "Leaving Eagle"})
            assert r.status == 200 and (await r.json())["total"] == 5
            done = await wait(c, ("done", "failed"))
            assert done["state"] == "done", done
            return done["result"]
        finally:
            await c.close()
    res = asyncio.run(go())
    assert res["copied"] == 5 and res["library"] == {"root": str(target), "name": "Leaving Eagle"} and res["failed"] == []
    assert libraries.info(str(target))["name"] == "Leaving Eagle" and libraries.info(str(target)).get("opened")
    assert (target / "Sample" / "Stola" / "708952787_n.jpg").is_file()
    db = Database(os.path.join(str(target), ".mlgallery", "library.db"))
    with db.read() as c:
        assert [r[0] for r in c.execute("SELECT path FROM folders")] == [str(target)]
        assert c.execute("SELECT COUNT(*) FROM files WHERE missing=0").fetchone()[0] == 5
        assert c.execute("SELECT kind FROM ops").fetchone()[0] == "eagle"  # one step in the new library's History
        assert c.execute("SELECT COUNT(*) FROM marks WHERE rating=5").fetchone()[0] == 1
    assert lib.files()["total"] == 0  # the open library wasn't touched
    # the library opens like any other, its files already indexed
    new = Library(libraries.data_dir(str(target)), libraries.home_dir(), str(target))
    libraries.prepare(new)
    assert [x["path"] for x in new.folders()] == [str(target)]
    assert scan.scan_folder(new.db, new.folders()[0])["unchanged"] == 5


def test_a_folder_that_is_a_library_already(lib, tmp_path):
    from aiohttp.test_utils import TestClient, TestServer
    from mlgallery import libraries
    from mlgallery.server import _EAGLE, make_app
    target = tmp_path / "Lib"
    target.mkdir()
    libraries.create(str(target))
    _EAGLE.clear()
    _EAGLE.update(state="ready", plan=eagle.plan_import(lib.db, str(leaving(tmp_path))), summary={})

    async def go():
        c = TestClient(TestServer(make_app(lib, "t"), host="127.0.0.1"))
        await c.start_server()
        try:
            r = await c.post("/api/eagle/apply", headers={"X-MLG-Token": "t"}, json={"target": "new", "path": str(target)})
            return r.status, (await r.json())["error"]
        finally:
            await c.close()
    status, error = asyncio.run(go())
    _EAGLE.clear()
    assert status == 422 and "library already" in error


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
