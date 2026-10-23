"""Open in ComfyUI (against a stand-in for Model Librarian's route) and Save as recipe (into a made-up library)."""
import asyncio
import json
import os
import random
import socket

import pytest
from aiohttp import web

from mlgallery import comfy
from mlgallery.meta import recipe_parse
from sample_media import generation, make_image


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def fake_comfy(clients=1, status=200):
    got = []

    async def open_(request):
        got.append(await request.json())
        if status != 200:
            return web.json_response({"error": "nope"}, status=status)
        return web.json_response({"clients": clients, "token": "t"})
    app = web.Application()
    app.router.add_post("/sm-fetcher/gallery/open", open_)
    runner = web.AppRunner(app)
    await runner.setup()
    port = free_port()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner, f"http://127.0.0.1:{port}", got


WF = {"nodes": [{"id": 1, "type": "KSampler"}], "links": []}


def test_open_in_comfy_sends_the_workflow():
    async def go():
        runner, url, got = await fake_comfy()
        try:
            r = await comfy.open_in_comfy(url + "/", WF, {"1": {}}, "lighthouse")
            assert r == {"clients": 1} and got == [{"name": "lighthouse", "workflow": WF}]
            await comfy.open_in_comfy(url, None, {"1": {"class_type": "X"}}, "api only")
            assert got[-1] == {"name": "api only", "prompt": {"1": {"class_type": "X"}}}
        finally:
            await runner.cleanup()
    asyncio.run(go())


@pytest.mark.parametrize("clients,status,msg", [(0, 200, "not open in a browser"), (1, 404, "Model Librarian 1.23"), (1, 500, "nope")])
def test_open_in_comfy_says_what_went_wrong(clients, status, msg):
    async def go():
        runner, url, _ = await fake_comfy(clients, status)
        try:
            with pytest.raises(comfy.ComfyError, match=msg):
                await comfy.open_in_comfy(url, WF, None, "x")
        finally:
            await runner.cleanup()
    asyncio.run(go())


def test_open_in_comfy_when_it_isnt_running_or_isnt_set():
    async def go():
        with pytest.raises(comfy.ComfyError, match="isn't running"):
            await comfy.open_in_comfy(f"http://127.0.0.1:{free_port()}", WF, None, "x")
        with pytest.raises(comfy.ComfyError, match="Settings"):
            await comfy.open_in_comfy("", WF, None, "x")
        with pytest.raises(comfy.ComfyError, match="no workflow"):
            await comfy.open_in_comfy("http://127.0.0.1:1", None, None, "x")
    asyncio.run(go())


def test_save_recipe_into_model_librarians_folder(tmp_path):
    ml = tmp_path / "models" / ".sm_model_fetcher"
    ml.mkdir(parents=True)
    p = str(tmp_path / "lighthouse.png")
    g = generation(7, random.Random(7), (320, 256))
    make_image(p, (320, 256), 7, g)
    r = comfy.save_recipe(str(ml), p, "image", "lighthouse.png")
    files = sorted(os.listdir(ml / "recipes"))
    assert files == [f"{r['id']}.json", f"{r['id']}.webp"] and len(r["id"]) == 12
    rec = json.loads((ml / "recipes" / f"{r['id']}.json").read_text(encoding="utf-8"))
    assert rec["schema"] == "recipe/1" and rec["title"] == "lighthouse" and rec["format"] == "comfyui"
    assert rec["prompt"]["positive"] == g["graph"]["10"]["inputs"]["text"] and len(rec["loras"]) == 2
    assert rec["comfy"]["workflow"] == g["workflow"]
    assert recipe_parse.normalize(rec)["prompt"] == rec["prompt"]  # Model Librarian reads it as its own
    r2 = comfy.save_recipe(str(ml), p, "image", "lighthouse.png", title="Mine")
    assert r2["id"] != r["id"] and r2["title"] == "Mine"  # a new one each time, never replacing


def test_save_recipe_needs_data_and_model_librarian(tmp_path):
    p = str(tmp_path / "plain.png")
    make_image(p, (64, 64), 1)
    ml = tmp_path / "ml"
    ml.mkdir()
    with pytest.raises(comfy.ComfyError, match="No generation data"):
        comfy.save_recipe(str(ml), p, "image", "plain.png")
    with pytest.raises(comfy.ComfyError, match="wasn't found"):
        comfy.save_recipe("", p, "image", "plain.png")


@pytest.mark.skipif(os.name != "nt", reason="Windows shortcuts")
def test_start_menu_shortcut(tmp_path, monkeypatch):
    from mlgallery import shortcut
    monkeypatch.setenv("MLG_SHORTCUT_DIR", str(tmp_path))  # never the real Start menu
    assert not shortcut.exists()
    lnk = shortcut.make()
    assert lnk == str(tmp_path / "Model Librarian Gallery.lnk") and os.path.getsize(lnk) > 100 and shortcut.exists()
    prog, args, wd = shortcut.target()
    assert prog.lower().endswith(("pythonw.exe", "python.exe")) and args == "-m mlgallery" and os.path.isdir(os.path.join(wd, "mlgallery"))
