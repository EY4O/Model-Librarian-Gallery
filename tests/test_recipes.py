"""Recipes: Model Librarian's recipes folder read and written as its own, bringing recipes in (pictures, recipe files,
links against a stand-in for CivitAI, what the Chrome extension leaves), the routes, and opening one in ComfyUI.

All on a made-up Model Librarian folder (MLG_ML_DATA) with generated pictures; nothing reaches the network.
"""
import asyncio
import io
import json
import os
import random

import pytest
from aiohttp import web

from conftest import image
from mlgallery import recipes
from mlgallery.meta import recipe_parse
from sample_media import generation, make_image
from test_comfy import fake_comfy, free_port
from test_server import H, indexed, run


@pytest.fixture
def ml(tmp_path, monkeypatch):
    d = tmp_path / "models" / ".sm_model_fetcher"
    d.mkdir(parents=True)
    (d / "registry.json").write_text("{}", encoding="utf-8")  # (what makes it Model Librarian's)
    monkeypatch.setenv("MLG_ML_DATA", str(d))
    return str(d)


def comfy_png(tmp_path, seed=7, name="lighthouse.png"):
    p = str(tmp_path / name)
    g = generation(seed, random.Random(seed), (320, 256))
    make_image(p, (320, 256), seed, g)
    return p, g


def png_bytes(color=(40, 90, 160)):
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (96, 64), color).save(out, "PNG")
    return out.getvalue()


# ---------------------------------------------------------------- the store
def test_store_round_trip_and_ids_never_replaced(ml, tmp_path):
    p, g = comfy_png(tmp_path)
    rec, thumb = recipes.from_file(p, "image", "lighthouse.png")
    assert rec["prompt"]["positive"] == g["graph"]["10"]["inputs"]["text"] and thumb[:4] == b"RIFF"
    rec["id"] = "aaaaaaaaaaaa"  # (one carrying an id of its own)
    a = recipes.save(ml, rec, thumb)
    b = recipes.save(ml, {**rec, "id": a["id"]}, None)
    assert a["id"] != "aaaaaaaaaaaa" and b["id"] != a["id"] and recipes.ID.match(a["id"])
    assert sorted(os.listdir(os.path.join(ml, "recipes"))) == sorted([f"{a['id']}.json", f"{a['id']}.webp", f"{b['id']}.json"])
    got = recipes.get(ml, a["id"])
    assert got["title"] == "lighthouse" and got["schema"] == "recipe/1" and got["comfy"]["workflow"] == g["workflow"]
    assert recipe_parse.normalize(got)["prompt"] == got["prompt"]  # (Model Librarian reads it as its own)
    s = recipes.summary(ml, got)
    assert s["model"] and len(s["loras"]) == 2 and s["workflow"] and s["thumb_v"] and not s["fav"]
    up = recipes.update(ml, a["id"], {"title": " Coast ", "tags": ["sea", "sea", " dusk "], "fav": 1, "notes": "keep", "comfy": None, "id": "x"})
    assert (up["title"], up["tags"], up["fav"], up["notes"]) == ("Coast", ["sea", "dusk"], True, "keep") and up["comfy"]  # (only those four)
    with pytest.raises(recipes.RecipeError, match="name"):
        recipes.update(ml, a["id"], {"title": "  "})
    assert [r["id"] for r in recipes.list_all(ml)][0] == b["id"] and recipes.count(ml) == 2  # (newest first)
    assert recipes.delete(ml, a["id"]) and not recipes.delete(ml, a["id"])
    assert sorted(os.listdir(os.path.join(ml, "recipes", "_deleted"))) == [f"{a['id']}.json", f"{a['id']}.webp"]
    assert recipes.get(ml, a["id"]) is None and recipes.count(ml) == 1
    for bad in ("../x", "ZZZZZZZZZZZZ", ""):
        with pytest.raises(recipes.RecipeError):
            recipes.get(ml, bad)
    with pytest.raises(recipes.RecipeError, match="wasn't found"):
        recipes.list_all(str(tmp_path / "nowhere"))


def test_reading_pictures_and_recipe_files(ml, tmp_path):
    p, g = comfy_png(tmp_path)
    rec, thumb = recipes.from_media(open(p, "rb").read(), "lighthouse.png")
    assert rec["format"] == "comfyui" and thumb
    with pytest.raises(recipe_parse.NoMetadata):
        recipes.from_media(png_bytes(), "plain.png")
    # recipe/1, alone, in a list, and as {recipe, thumb} the way the extension and share cards send it
    one = recipes.from_json(json.dumps(rec).encode(), "mine.json")
    assert len(one) == 1 and one[0][0]["prompt"] == rec["prompt"] and one[0][0]["id"] is None
    import base64
    card = {"recipe": {**rec, "title": "Card"}, "thumb": "data:image/png;base64," + base64.b64encode(png_bytes()).decode()}
    many = recipes.from_json(json.dumps([rec, card, {"nothing": 1}]).encode(), "two.json")
    assert [r["title"] for r, _ in many] == ["lighthouse", "Card"] and many[1][1][:4] == b"RIFF"
    lm = {"id": "lm-1", "title": "From LM", "file_path": "x.webp", "base_model": "SDXL 1.0",
          "gen_params": {"prompt": "a lighthouse at dusk", "negative_prompt": "blurry", "steps": 28, "cfg_scale": 6, "size": "832x1216", "sampler": "euler"},
          "loras": [{"file_name": "paper_cut", "modelName": "Paper Cut", "strength": 0.7, "hash": "abc"}, {"file_name": "gone", "exclude": True}]}
    (r, _), = recipes.from_json(json.dumps(lm).encode("utf-8-sig"), "lm.json")
    assert r["format"] == "lora-manager" and r["lm_id"] is None and r["settings"]["width"] == 832
    assert [(l["file"], l["weight"]) for l in r["loras"]] == [("paper_cut", 0.7)]
    for bad, why in ((b"not json", "isn't JSON"), (b'{"a": 1}', "no recipe")):
        with pytest.raises(recipe_parse.NoMetadata, match=why):
            recipes.from_json(bad, "x.json")


# ---------------------------------------------------------------- links, against a stand-in for CivitAI
async def fake_civitai(items):
    """CivitAI's image API and its pictures, on this PC. items: imageId or postId -> the items it answers with."""
    seen = []

    async def images(request):
        seen.append(dict(request.query))
        key = request.query.get("imageId") or request.query.get("postId")
        return web.json_response({"items": items.get(key, [])})

    async def pic(request):
        return web.Response(body=png_bytes(), content_type="image/png")

    async def hop(request):
        raise web.HTTPFound("http://192.168.1.10/secret.png")  # (a public link mustn't lead into the home network)
    app = web.Application()
    app.router.add_get("/api/v1/images", images)
    app.router.add_get("/pic/{rest:.*}", pic)
    app.router.add_get("/hop", hop)
    runner = web.AppRunner(app)
    await runner.setup()
    port = free_port()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner, f"http://127.0.0.1:{port}", seen


CIVITAI_ITEM = {"id": 4242, "type": "image", "baseModel": "Pony", "width": 832, "height": 1216,
                "meta": {"prompt": "score_9, a lighthouse <lora:paper_cut:0.6>", "negativePrompt": "blurry", "steps": 30, "cfgScale": 5,
                         "sampler": "Euler a", "seed": 77, "Size": "832x1216", "Model": "ponyRealism",
                         "resources": [{"type": "lora", "name": "paper_cut", "weight": 0.6}]}}


def test_links_from_civitai(monkeypatch):
    async def go():
        items = {}
        runner, base, seen = await fake_civitai(items)
        item = {**CIVITAI_ITEM, "url": f"{base}/pic/xG1/width=1024/4242.jpeg"}
        items.update({"4242": [item], "77": [item, {**item, "id": 4243, "meta": None}]})
        monkeypatch.setenv("MLG_CIVITAI", base)
        try:
            r = await recipes.from_url("https://civitai.com/images/4242")
            rec = r["recipe"]
            assert rec["format"] == "civitai" and rec["base_model"] == "Pony" and rec["source"]["image_id"] == 4242
            assert rec["prompt"]["negative"] == "blurry" and [l["weight"] for l in rec["loras"]] == [0.6]
            assert r["thumb"][:4] == b"RIFF"  # (its picture, fetched and made small)
            assert {"imageId": "4242", "withMeta": "true"}.items() <= seen[0].items()
            r = await recipes.from_url("https://civitai.com/posts/77")
            assert [c["url"] for c in r["choices"]] == ["https://civitai.com/images/4242", "https://civitai.com/images/4243"]
            assert [c["has_meta"] for c in r["choices"]] == [True, False] and "anim=false,width=256" in r["choices"][0]["thumb"]
            for bad in ("http://civitai.com/images/1", "https://127.0.0.1/x.png", "https://192.168.0.2/x.png", "https://nas.local/x.png", "ftp://x"):
                with pytest.raises(recipe_parse.NoMetadata, match="public https"):
                    await recipes.from_url(bad)
            with pytest.raises(recipe_parse.NoMetadata, match="no public image"):
                await recipes.from_url("https://civitai.com/images/999")
            with pytest.raises(recipe_parse.NoMetadata, match="image .* or post"):
                await recipes.from_url("https://civitai.com/models/12")
        finally:
            await runner.cleanup()
    asyncio.run(go())
    assert recipes.civitai_still("https://image.civitai.com/x/original=true/a.mp4") == "https://image.civitai.com/x/anim=false,width=512/a.mp4"


def test_redirects_into_the_home_network_are_refused():
    async def go():
        runner, base, _ = await fake_civitai({})
        try:
            async with recipes._session() as s:
                assert await recipes.fetch_image(s, base + "/hop") is None
                assert await recipes.fetch_image(s, base + "/pic/a.png") == png_bytes()
        finally:
            await runner.cleanup()
    asyncio.run(go())


# ---------------------------------------------------------------- what the Chrome extension leaves
def test_pickup_of_what_the_extension_left(ml, tmp_path, monkeypatch):
    inbox = tmp_path / "Downloads" / "Model Librarian Gallery"
    inbox.mkdir(parents=True)
    p, _ = comfy_png(tmp_path)
    rec, _ = recipes.from_file(p, "image", "lighthouse.png")
    (inbox / "a.mlrecipe.json").write_text(json.dumps({"v": 1, "url": "", "page": "https://civitai.com/images/1", "recipe": rec}), encoding="utf-8")
    (inbox / "b.mlrecipe.json").write_text(json.dumps({"v": 1, "url": "https://civitai.com/images/4242", "page": "https://civitai.com/images/4242"}), encoding="utf-8")
    (inbox / "c.mlrecipe.json").write_text("not a note", encoding="utf-8")
    (inbox / "d.mlrecipe.json").write_text(json.dumps({"v": 1, "url": "https://civitai.com/images/999"}), encoding="utf-8")
    (inbox / "keep.png").write_bytes(png_bytes())  # (a picture saved for New downloads: not ours)
    assert recipes.pickup(None, str(inbox)) == [] and len(recipes.waiting(str(inbox))) == 4  # (no Model Librarian: they wait)

    async def stand_in():
        return await fake_civitai({"4242": [CIVITAI_ITEM]})
    loop = asyncio.new_event_loop()
    runner, base, _ = loop.run_until_complete(stand_in())
    import threading
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    monkeypatch.setenv("MLG_CIVITAI", base)
    try:
        saved = recipes.pickup(ml, str(inbox))
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(5)
    assert sorted(r["title"] for r in saved) == ["CivitAI image 4242", "lighthouse"]
    assert recipes.waiting(str(inbox)) == [] and os.listdir(inbox) == ["keep.png"]  # (the bad and the missing ones go too)
    assert recipes.count(ml) == 2


# ---------------------------------------------------------------- the routes
def test_routes_while_recipes_are_on(lib, ml, tmp_path, media):
    fid = indexed(lib, media, 2)
    p, g = comfy_png(media, name="made.png")
    from mlgallery import scan
    scan.scan_folder(lib.db, {"id": fid, "path": str(media)})
    ids = {r["name"]: r["id"] for r in lib.files()["items"]}
    data = open(p, "rb").read()

    async def fn(c):
        r = await c.get("/api/recipes", headers=H)
        assert r.status == 409 and "turn them on" in (await r.json())["error"]
        assert (await (await c.get("/api/status", headers=H)).json())["recipes"] is None
        lib.settings.update({"recipes": True})
        assert (await (await c.get("/api/status", headers=H)).json())["recipes"] == 0
        import aiohttp
        form = aiohttp.FormData()
        form.add_field("file", data, filename="made.png", content_type="image/png")
        form.add_field("file", png_bytes(), filename="plain.png", content_type="image/png")
        form.add_field("file", json.dumps({"recipe": {"prompt": {"positive": "from a file"}, "title": "Filed"}}).encode(), filename="r.json", content_type="application/json")
        r = await (await c.post("/api/recipes/import", data=form, headers=H)).json()
        assert sorted(s["title"] for s in r["saved"]) == ["Filed", "made"] and [s["name"] for s in r["skipped"]] == ["plain.png"]
        r = await (await c.post("/api/recipes/import", json={"recipe": {"title": "Sent", "prompt": {"positive": "sent"}}}, headers=H)).json()
        sent = r["saved"][0]["id"]
        r = await c.post("/api/recipes/import", json={"url": "https://10.0.0.5/x.png"}, headers=H)
        assert r.status == 422
        r = await (await c.post("/api/recipes/from-files", json={"selection": [ids["made.png"], ids["0.png"]]}, headers=H)).json()
        assert [s["title"] for s in r["saved"]] == ["made"] and [s["name"] for s in r["skipped"]] == ["0.png"]
        lst = await (await c.get("/api/recipes", headers=H)).json()
        assert len(lst["recipes"]) == 4 and (await (await c.get("/api/status", headers=H)).json())["recipes"] == 4
        made = next(x for x in lst["recipes"] if x["title"] == "made" and x["thumb_v"])
        r = await (await c.post(f"/api/recipes/{made['id']}", json={"tags": ["coast"], "fav": True}, headers=H)).json()
        assert r["recipe"]["tags"] == ["coast"] and r["recipe"]["fav"]
        assert (await (await c.get("/api/recipes", headers=H)).json())["tags"] == ["coast"]
        one = await (await c.get(f"/api/recipes/{made['id']}", headers=H)).json()
        assert one["recipe"]["comfy"]["workflow"] == g["workflow"] and one["summary"]["workflow"]
        r = await c.get(f"/api/recipes/{made['id']}/thumb", headers=H)
        assert r.status == 200 and (await r.read())[:4] == b"RIFF"
        assert (await c.get(f"/api/recipes/{sent}/thumb", headers=H)).status == 404
        assert (await c.get("/api/recipes/zzz", headers=H)).status == 404
        # into ComfyUI: Model Librarian's Recipes tab, or the workflow in a new tab
        runner, url, got = await fake_comfy(clients=1)
        lib.settings.update({"comfy_url": url})
        try:
            r = await (await c.post(f"/api/recipes/{made['id']}/comfy", json={"mode": "recipe"}, headers=H)).json()
            assert r == {"clients": 1} and got[-1]["recipe"]["id"] == made["id"] and got[-1]["thumb"].startswith("data:image/webp;base64,")
            r = await (await c.post(f"/api/recipes/{made['id']}/comfy", json={"mode": "workflow"}, headers=H)).json()
            assert r == {"clients": 1} and got[-1]["workflow"] == g["workflow"] and "recipe" not in got[-1]
            r = await c.post(f"/api/recipes/{sent}/comfy", json={"mode": "workflow"}, headers=H)
            assert r.status == 422 and "no ComfyUI workflow" in (await r.json())["error"]
        finally:
            await runner.cleanup()
        runner, url, got = await fake_comfy(status=400)  # (a Model Librarian older than 1.29.0: "Send a workflow or a prompt.")
        lib.settings.update({"comfy_url": url})
        try:
            r = await c.post(f"/api/recipes/{made['id']}/comfy", json={"mode": "recipe"}, headers=H)
            assert r.status == 502 and "1.29.0" in (await r.json())["error"]
        finally:
            await runner.cleanup()
        r = await (await c.delete(f"/api/recipes/{sent}", headers=H)).json()
        assert r == {"ok": True} and (await c.delete(f"/api/recipes/{sent}", headers=H)).status == 404
        lib.settings.update({"recipes": False})
        assert (await c.get(f"/api/recipes/{made['id']}", headers=H)).status == 409
    run(lib, fn)


def test_recipes_need_model_librarian(lib, monkeypatch, tmp_path):
    monkeypatch.setenv("MLG_ML_DATA", str(tmp_path / "not-there"))
    lib.settings.update({"recipes": True})

    async def fn(c):
        r = await c.get("/api/recipes", headers=H)
        assert r.status == 409 and "wasn't found" in (await r.json())["error"]
        assert (await (await c.get("/api/status", headers=H)).json())["recipes"] is None
    run(lib, fn)


def test_phones_cant_reach_recipes():
    from mlgallery import remote
    for m, path in (("GET", "/api/recipes"), ("GET", "/api/recipes/abcdefabcdef"), ("GET", "/api/recipes/abcdefabcdef/thumb"),
                    ("POST", "/api/recipes/import"), ("POST", "/api/recipes/from-files"), ("POST", "/api/recipes/abcdefabcdef"),
                    ("DELETE", "/api/recipes/abcdefabcdef"), ("POST", "/api/recipes/abcdefabcdef/comfy")):
        assert not remote.allowed(m, path)
