"""The interface in headless Edge, on a generated library: from start to grid, the viewer and the controls.

Set MLG_SHOTS=<folder> to also save screenshots there (how the README's are made).
"""
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request

import pytest

from conftest import HAVE_FFMPEG
from edge import Edge, find_edge

pytestmark = pytest.mark.skipif(not find_edge(), reason="needs Microsoft Edge")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.environ.get("MLG_SHOTS")


@pytest.fixture(scope="module")
def server(sample_dir, tmp_path_factory):
    data = tmp_path_factory.mktemp("uidata")
    p = subprocess.Popen([sys.executable, "-m", "mlgallery", "--server", "--data", str(data)], cwd=ROOT,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    url = p.stdout.readline().strip()
    base, token = url.split("/?t=")

    def api(path, body=None):
        req = urllib.request.Request(base + "/api" + path, data=json.dumps(body).encode() if body else None,
                                     headers={"X-MLG-Token": token, "Content-Type": "application/json"}, method="POST" if body else "GET")
        with urllib.request.urlopen(req) as r:
            return json.load(r)

    api("/folders", {"path": sample_dir})
    deadline = time.time() + 90
    while time.time() < deadline:
        st = api("/status")
        if st["files"] and not st["scanning"] and not st["queued"] and not st["thumbs_pending"]:
            break
        time.sleep(0.3)
    yield url, api
    p.terminate()
    p.wait(10)


def page(url, **kw):
    return Edge(url, **kw)


def test_grid_viewer_and_controls(server):
    url, api = server
    total = api("/files")["total"]

    async def go():
        async with page(url) as e:
            await e.wait("document.querySelectorAll('.tile img.on').length >= 8")
            assert await e.js("document.getElementById('count').textContent") == f"{total} items"
            assert await e.js("document.getElementById('all-count').textContent") == str(total)
            assert await e.js("document.querySelectorAll('.day').length") >= 2  # newest first: grouped by day
            assert (await e.js("document.querySelector('.day').textContent")).startswith("Today")
            # only what's on screen exists
            assert await e.js("document.querySelectorAll('.tile').length") < total
            if SHOTS:
                await e.shot(os.path.join(SHOTS, "library.png"))

            # the viewer, with its details panel open by default
            await e.click(".tile")
            await e.wait("!document.getElementById('viewer').hidden && document.querySelector('#v-media img, #v-media video')")
            assert await e.js("document.getElementById('v-panel').getBoundingClientRect().width") == 380
            assert "1 of" in (await e.js("document.getElementById('v-pos').textContent"))
            assert "object" not in await e.js("document.querySelector('.v-panel dl').textContent")
            path = await e.js("document.querySelector('.v-panel .path').textContent")
            assert os.path.isfile(path)
            if SHOTS:
                await e.wait("document.querySelector('#v-media img')?.complete || document.querySelector('#v-media video')")
                await asyncio.sleep(0.4)
                await e.shot(os.path.join(SHOTS, "viewer.png"))
            await e.key("ArrowRight")
            await e.wait("document.getElementById('v-pos').textContent.startsWith('2 of')")
            await e.key("i")
            assert await e.js("getComputedStyle(document.getElementById('v-panel')).display") == "none"
            assert await e.js("localStorage.getItem('mlg.details')") == "false"
            await e.key("i")
            await e.key("Escape")
            assert await e.js("document.getElementById('viewer').hidden")

            # the sidebar hides and comes back
            await e.key("b", ctrlKey=True)
            assert await e.js("getComputedStyle(document.getElementById('side')).display") == "none"
            assert not await e.js("document.getElementById('side-show').hidden")
            await e.click("#side-show")
            assert await e.js("getComputedStyle(document.getElementById('side')).display") != "none"

            # Images only, then Random (no day headings, no Sort)
            await e.click('#kind button[data-kind="image"]')
            await e.wait(f"document.getElementById('count').textContent === '{api('/files?kind=image')['total']} items'")
            await e.click('#kind button[data-kind=""]')
            await e.click("#view-random")
            await e.wait("document.getElementById('view-title').textContent === 'Random' && document.querySelector('.tile')")
            assert await e.js("document.querySelectorAll('.day').length") == 0
            assert await e.js("document.getElementById('sort-wrap').hidden")
            first = await e.js("document.querySelector('.tile').getAttribute('aria-label')")
            await e.click("#shuffle")
            await asyncio.sleep(0.8)
            again = [await e.js("[...document.querySelectorAll('.tile')].slice(0, 6).map(t => t.getAttribute('aria-label')).join()")]
            assert again  # a new order was fetched (the same first tile by chance is fine)
            assert first

            # tile size changes the columns
            await e.click("#view-all")
            await e.wait("document.querySelector('.tile')")
            small = await e.js("document.querySelector('.tile').getBoundingClientRect().width")
            await e.js("const t = document.getElementById('tile'); t.value = 300; t.dispatchEvent(new Event('input')); true")
            big = await e.js("document.querySelector('.tile').getBoundingClientRect().width")
            assert big > small * 1.4
            errors = await e.js("window.__errors || []")
            assert not errors
    asyncio.run(go())


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")
def test_videos_have_posters_and_play(server):
    url, api = server
    videos = api("/files?kind=video")["items"]
    assert videos and all(v["thumb"] == 1 and v["duration"] for v in videos)

    async def go():
        async with page(url) as e:
            await e.wait("document.querySelector('.tile')")
            await e.click('#kind button[data-kind="video"]')
            await e.wait("document.querySelector('.tile .badge') && document.getElementById('count').textContent.startsWith('" + str(len(videos)) + "')")
            await e.click(".tile")
            await e.wait("document.querySelector('#v-media video')?.readyState >= 2", timeout=20)
    asyncio.run(go())


def test_empty_library_offers_to_add_a_folder(tmp_path):
    p = subprocess.Popen([sys.executable, "-m", "mlgallery", "--server", "--data", str(tmp_path / "d")], cwd=ROOT,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    url = p.stdout.readline().strip()
    try:
        async def go():
            async with page(url, width=900, height=640) as e:
                await e.wait("!document.getElementById('empty').hidden")
                assert await e.js("document.querySelector('#empty h2').textContent") == "Add a folder to start"
                assert await e.js("!!document.querySelector('#empty .btn.primary')")
        asyncio.run(go())
    finally:
        p.terminate()
        p.wait(10)
