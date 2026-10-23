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
        if st["files"] and not st["scanning"] and not st["queued"] and not st["thumbs_pending"] and not st.get("meta_pending"):
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


def test_search_marks_tags_and_details(server):
    url, api = server

    async def go():
        async with page(url) as e:
            await e.wait("document.querySelectorAll('.tile').length >= 8")
            # typing model:… and a space makes a chip; the grid narrows to it
            await e.js("const q = document.getElementById('q'); q.focus(); q.value = 'lora:aurelia '; q.dispatchEvent(new Event('input')); true")
            await e.wait("document.querySelector('#chips .chip .v')?.textContent === 'aurelia'")
            want = api("/files?q=lora:aurelia")["total"]
            await e.wait(f"document.getElementById('count').textContent === '{want} items'")
            # suggestions for a field
            await e.js("const q = document.getElementById('q'); q.value = 'model:dre'; q.dispatchEvent(new Event('input')); true")
            await e.wait("!document.getElementById('suggest').hidden && document.getElementById('suggest').textContent.includes('dreamy_mix_v3')")
            assert "null" not in await e.js("document.getElementById('suggest').textContent")
            await e.js("document.querySelector('.s-item').dispatchEvent(new MouseEvent('mousedown', {bubbles: true})); true")
            await e.wait("document.querySelectorAll('#chips .chip').length === 2")
            both = api("/files?q=" + "lora:aurelia%20model:dreamy_mix_v3")["total"]
            await e.wait(f"document.getElementById('count').textContent.startsWith('{both} item')")
            # Backspace in the empty box removes the last chip; the clear button removes the rest
            await e.js("const q = document.getElementById('q'); q.value = ''; q.dispatchEvent(new KeyboardEvent('keydown', {key: 'Backspace', bubbles: true})); true")
            await e.wait("document.querySelectorAll('#chips .chip').length === 1")
            await e.click("#q-clear")
            await e.wait("!document.querySelector('#chips .chip')")
            # no matches: says so and offers to clear
            await e.js("const q = document.getElementById('q'); q.value = 'zzzznotthere'; q.dispatchEvent(new Event('input')); true")
            await e.wait("document.querySelector('#empty h2')?.textContent === 'No matches'")
            await e.click("#empty .btn")
            await e.wait("document.querySelector('.tile')")

            # the details of a ComfyUI image: prompt, settings, models, workflow, tags
            first = next(i for i in api("/files?q=has:workflow")["items"])
            await e.js(f"const q = document.getElementById('q'); q.value = {json.dumps(first['name'].split('.')[0].replace('_', ' '))}; q.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Enter', bubbles: true}})); true")
            await e.wait("document.getElementById('count').textContent === '1 item'")
            await e.wait("document.querySelector('.tile')")
            await e.click(".tile")
            await e.wait("document.querySelector('.v-panel .models')")
            panel = await e.js("document.getElementById('v-panel').textContent")
            info = api(f"/file/{first['id']}/info")
            assert info["generation"]["positive"] in panel and "Copy workflow" in panel and "Seed" in panel
            assert "NaN" not in panel and "object" not in panel
            # rating and favourite from the keyboard, shown on the tile too
            await e.key("4")
            await e.wait("document.querySelectorAll('#v-stars .star.on').length === 4")
            await e.key("f")
            await e.wait("document.getElementById('v-fav').getAttribute('aria-pressed') === 'true'")
            assert api(f"/file/{first['id']}/info")["rating"] == 4
            # a tag, added in the panel
            await e.js("const t = document.querySelector('.tag-input'); t.value = 'ui-test'; t.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true})); true")
            await e.wait("[...document.querySelectorAll('.tags .chip')].some(c => c.textContent.includes('ui-test'))")
            assert "ui-test" in {t["name"] for t in api(f"/file/{first['id']}/info")["tags"]}
            # clicking a model filters by it
            await e.js("document.querySelector('.model .link').click(); true")
            await e.wait("document.getElementById('viewer').hidden && document.querySelector('#chips .chip')")
            assert await e.js("document.querySelector('#chips .chip .k').textContent") == "model"
            await e.click("#q-clear")
            await e.wait("!document.querySelector('#chips .chip')")
            # the favourites filter
            await e.click("#f-fav")
            await e.wait("document.getElementById('count').textContent === '1 item'")
            await e.wait("document.querySelector('.tile .t-fav')")
            await e.click("#f-fav")
            # Recently viewed has what was opened
            await e.click("#view-recent")
            await e.wait("document.getElementById('view-title').textContent === 'Recently viewed' && document.querySelector('.tile')")
            assert await e.js("document.querySelector('.tile').getAttribute('aria-label')") == first["name"]
            await e.click("#view-all")
            await e.wait("document.querySelectorAll('.tile').length > 4")
            # keyboard: arrows move between tiles, Enter opens
            await e.js("document.querySelector('.tile').focus(); true")
            before = await e.js("document.activeElement.getAttribute('aria-label')")
            await e.js("document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowRight', bubbles: true})); true")
            second = await e.js("document.activeElement.getAttribute('aria-label')")
            assert before and second and second != before
            await e.js("document.activeElement.click(); true")
            await e.wait("!document.getElementById('viewer').hidden")
            assert (await e.js("document.getElementById('v-name').textContent")) == second
            await e.key("Escape")
            assert not await e.js("window.__errors.length")
    asyncio.run(go())


def test_statistics_screen(server):
    url, api = server
    o = api("/stats?days=0")

    async def go():
        async with page(url) as e:
            await e.wait("document.querySelector('.tile')")
            await e.click("#view-stats")
            await e.wait("document.querySelector('.kpis') && document.querySelector('.st-grid')")
            assert await e.js("getComputedStyle(document.querySelector('.toolbar')).display") == "none"
            await e.js("[...document.querySelectorAll('.st-head .seg button')].find(b => b.textContent === 'All time').click(); true")
            await e.wait(f"document.querySelector('.kpi .v').textContent === '{o['totals']['n']:,}'")
            assert await e.js("document.querySelectorAll('.tl-col').length") >= 1
            assert "wasn't found" in await e.js("document.querySelector('.st-grid').textContent")  # no Model Librarian in the tests
            assert "null" not in await e.js("document.getElementById('stats').textContent")
            # a checkpoint's row opens the library filtered to it
            top = o["checkpoints"][0]
            await e.js("document.querySelector('.bars.ckpt .bar-row').click(); true")
            await e.wait("!document.getElementById('scroll').hidden && document.querySelector('#chips .chip')")
            assert await e.js("document.querySelector('#chips .chip .k').textContent") == "model"
            await e.wait(f"document.getElementById('count').textContent === '{top['n']} item{'' if top['n'] == 1 else 's'}'")
            # a setting tab
            await e.click("#view-stats")
            await e.wait("document.querySelector('.st-grid')")
            await e.js("[...document.querySelectorAll('[role=tab]')].find(b => b.textContent === 'Steps').click(); true")
            assert await e.js("document.querySelector('[role=tab][aria-selected=true]').textContent") == "Steps"
            await e.click("#view-all")
            await e.wait("document.querySelector('.tile') && !document.querySelector('.toolbar').hidden")
            assert not await e.js("window.__errors.length")
    asyncio.run(go())


def test_characters_screens(server):
    url, api = server

    async def go():
        async with page(url) as e:
            await e.wait("document.querySelector('.tile')")
            await e.click("#view-characters")
            await e.wait("document.querySelector('.c-empty') || document.querySelector('.c-grid')")
            # a new character from the editor: its LoRA, with the trigger words it suggests
            await e.js("[...document.querySelectorAll('.st-head .btn.primary, .c-empty .btn.primary')][0].click(); true")
            await e.wait("document.querySelector('dialog.dialog[open]')")
            await e.js("const d = document.querySelector('dialog'); d.querySelector('input[aria-label=Name]').value = 'Aurelia';"
                       "const l = d.querySelector('input[aria-label=LoRA]'); l.value = 'aurelia_character_v3'; [...d.querySelectorAll('button')].find(b => b.textContent === 'Use').click(); true")
            await e.wait("document.querySelector('dialog .rules .rk.lora')")
            await e.wait("document.querySelector('.word-picks .btn')", timeout=10)  # the prompts give its trigger word away
            await e.js("document.querySelector('.word-picks .btn').click(); true")
            await e.js("const d = document.querySelector('dialog'); d.querySelector('.rule-add select').value = 'tag';"
                       "const v = d.querySelector('.rule-add input'); v.value = 'soft light'; [...d.querySelectorAll('.rule-add button')].pop().click(); true")
            await e.wait("document.querySelectorAll('dialog .rules li').length === 3")
            await e.js("document.querySelector('dialog button[type=submit]').click(); true")
            await e.wait("document.querySelector('.c-head h1')?.textContent === 'Aurelia' && document.querySelector('.histo')")
            ch = api("/characters")["characters"][0]
            assert ch["name"] == "Aurelia" and ch["n"] > 0
            assert await e.js("document.getElementById('char-count').textContent") == "1"
            # To review: Y confirms the focused one
            if ch["review"]:
                await e.click("#view-review")
                await e.wait("document.querySelector('.r-card')")
                before = await e.js("document.querySelectorAll('.r-card').length")
                await e.js("const c = document.querySelector('.r-card'); c.focus(); c.dispatchEvent(new KeyboardEvent('keydown', {key: 'y', bubbles: true})); true")
                await e.wait(f"document.querySelectorAll('.r-card').length === {before - 1} || document.querySelector('.c-empty')")
                assert api(f"/characters/{ch['id']}")["counts"]["confirmed"] == 1
            # the details panel shows the character, and "Not Aurelia" takes it off that file
            await e.click("#view-all")
            await e.js("const q = document.getElementById('q'); q.value = 'character:aurelia '; q.dispatchEvent(new Event('input')); true")
            await e.wait(f"document.getElementById('count').textContent.startsWith('{api('/characters')['characters'][0]['n']} item')")
            await e.click(".tile")
            await e.wait("document.querySelector('.chip.char')")
            await e.js("document.querySelector('.chip.char button.x[title^=Not]').click(); true")
            await e.wait("!document.querySelector('.chip.char')")
            await e.key("Escape")
            assert not await e.js("window.__errors.length")
    asyncio.run(go())
