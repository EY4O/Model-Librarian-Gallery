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

    api.base, api.token = base, token  # (for a request api() doesn't make: DELETE)
    api("/library/kind", {"kind": "ai"})  # said once: these tests don't meet the question (test_empty_library does)
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
            await e.wait("document.querySelectorAll('.tile img.on').length >= 5")  # (the screen holds 7 at the least: just after midnight, one or two a day)
            assert await e.js("document.getElementById('count').textContent") == f"{total} items"
            assert await e.js("document.getElementById('all-count').textContent") == str(total)
            assert await e.js("document.querySelectorAll('.day').length") >= 2  # newest first: grouped by day
            # the newest is up to an hour old: just after midnight, that's yesterday
            assert (await e.js("document.querySelector('.day').textContent")).startswith("Today" if time.localtime().tm_hour >= 1 else ("Today", "Yesterday"))
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
            await e.click("#view-btn")
            await e.js("document.querySelector('.view-panel .seg button[data-v=\"image\"]').click(); true")
            await e.wait(f"document.getElementById('count').textContent === '{api('/files?kind=image')['total']} items'")
            assert await e.js("document.getElementById('view-label').textContent") == "Images only"  # the filter shows on the button
            await e.js("document.querySelector('.view-panel .seg button[data-v=\"\"]').click(); true")
            await e.js("document.querySelector('.menu')?._close(); true")
            await e.click("#view-random")
            await e.wait("document.getElementById('view-title').textContent === 'Random' && document.querySelector('.tile')")
            assert await e.js("document.querySelectorAll('.day').length") == 0
            await e.click("#view-btn")
            assert "Sort" not in await e.js("document.querySelector('.menu').textContent")  # a shuffle has no sort
            await e.key("Escape")
            await e.js("document.querySelector('.menu')?.remove(); true")
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
            await e.js("const t = document.getElementById('zoom-range'); t.value = 300; t.dispatchEvent(new Event('input')); true")
            big = await e.js("document.querySelector('.tile').getBoundingClientRect().width")
            assert big > small * 1.4
            await e.click("#zoom-out")  # the - button, and Ctrl+-
            await e.key("-", ctrlKey=True)
            assert await e.js("document.querySelector('.tile').getBoundingClientRect().width") < big
            # the View panel: Waterfall, and names under the tiles
            await e.click("#view-btn")
            await e.js("document.querySelector('.view-panel .seg button[data-v=\"waterfall\"]').click(); true")
            await e.wait("document.querySelectorAll('.tile').length > 4")
            await e.js("[...document.querySelectorAll('.view-panel .vp-row')].find(r => r.textContent.startsWith('Name')).querySelector('.switch').click(); true")
            await e.wait("document.querySelector('.tile .t-cap .c1')")
            names = await e.js("[...document.querySelectorAll('.tile .t-cap .c1')].slice(0, 3).map(c => c.textContent)")
            assert all(n.endswith(('.png', '.jpg', '.webp', '.mp4')) for n in names), names
            await e.js("[...document.querySelectorAll('.view-panel .vp-row')].find(r => r.textContent.startsWith('Name')).querySelector('.switch').click(); true")
            await e.js("document.querySelector('.view-panel .seg button[data-v=\"grid\"]').click(); true")
            await e.js("document.querySelector('.menu')?._close(); true")
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
            await e.click("#view-btn")
            await e.js("document.querySelector('.view-panel .seg button[data-v=\"video\"]').click(); true")
            await e.wait("document.querySelector('.tile .badge') && document.getElementById('count').textContent.startsWith('" + str(len(videos)) + "')")
            # resting on a video's tile plays it there, silently; leaving it stops
            await e.js("document.querySelector('.tile').dispatchEvent(new MouseEvent('mouseenter')); true")
            await e.wait("document.querySelector('.tile video.t-hover')?.readyState >= 2", timeout=20)
            assert await e.js("document.querySelector('.tile video.t-hover').muted")
            await e.js("document.querySelector('.tile').dispatchEvent(new MouseEvent('mouseleave')); true")
            await e.wait("!document.querySelector('.tile video.t-hover')")
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
                # a new library asks once what it's for (one or more); for photos and illustrations: People & characters,
                # and a shortcut in the sidebar to each part
                await e.wait("document.querySelector('dialog.kind-ask')?.open")
                assert await e.js("[...document.querySelectorAll('.kind-choice b')].map(b => b.textContent).join()") == "AI generations,Photos,Illustrations"
                assert await e.js("document.querySelector('.kind-ask button[type=submit]').disabled")  # nothing ticked yet
                await e.js("for (const v of ['photos', 'illustrations']) document.querySelector(`.kind-choice input[value=${v}]`).click(); true")
                await e.js("document.querySelector('.kind-ask button[type=submit]').click(); true")
                await e.wait("!document.querySelector('dialog.kind-ask') && document.getElementById('characters-label').textContent === 'People & characters'")
                assert await e.js("document.body.dataset.kinds") == "photos illustrations"
                assert await e.js("!document.getElementById('kind-nav').hidden")
                assert await e.js("[...document.querySelectorAll('#kind-nav [data-is]')].filter(b => !b.hidden).map(b => b.dataset.is).join()") == "photo,illustration"
                await e.wait("!document.getElementById('empty').hidden")
                assert await e.js("document.querySelector('#empty h2').textContent") == "Add a folder to start"
                assert await e.js("!!document.querySelector('#empty .btn.primary')")
            async with page(url, width=900, height=640) as e:  # asked once: not again
                await e.wait("document.getElementById('characters-label').textContent === 'People & characters'")
                await asyncio.sleep(1.5)
                assert not await e.js("!!document.querySelector('dialog.kind-ask')")
        asyncio.run(go())
    finally:
        p.terminate()
        p.wait(10)


def test_colours(server):
    url, api = server
    first = api("/files")["items"][0]
    want = api(f"/file/{first['id']}/info")["colors"]
    assert want and all(0 < c["ratio"] <= 1 for c in want)

    async def go():
        async with page(url) as e:
            await e.wait("document.querySelectorAll('.tile img.on').length >= 1")
            await e.click(".tile")
            await e.wait("document.querySelector('.v-panel .swatches .swatch')")
            # a swatch per colour, like Eagle's: its value and share on hover
            assert await e.js("document.querySelectorAll('.v-panel .swatch').length") == len(want)
            tip = await e.js("document.querySelector('.v-panel .swatch').title")
            assert tip == f"{want[0]['hex'].upper()} ({want[0]['ratio'] * 100:.1f}%)"
            # click: find files with it, or copy it
            await e.js("document.querySelector('.v-panel .swatch').click(); true")
            await e.wait("document.querySelector('.menu')")
            items = await e.js("[...document.querySelectorAll('.menu button')].map(b => b.textContent)")
            assert items[0] == "Files with this colour" and items[-1] == f"Copy {want[0]['hex'].upper()}"
            await e.js("document.querySelector('.menu button').click(); true")
            await e.wait("document.getElementById('viewer').hidden && document.querySelector('#chips .chip .chip-sw')")
            assert await e.js("document.querySelector('#chips .chip .k').textContent") == "color"
            await e.wait(f"document.getElementById('count').textContent.length")
            # and Statistics shows the colours over time
            await e.js("document.getElementById('view-stats').click(); true")
            await e.wait("[...document.querySelectorAll('.st-card h2')].some(x => x.textContent === 'Colours, over time')")
            assert await e.js("document.querySelectorAll('.lg-pick').length") >= 2
    asyncio.run(go())


def test_settings_shows_the_library(tmp_path):
    from mlgallery import libraries
    root = tmp_path / "My Generations"
    root.mkdir()
    libraries.create(str(root))
    p = subprocess.Popen([sys.executable, "-m", "mlgallery", "--server", "--library", str(root)], cwd=ROOT,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    url = p.stdout.readline().strip()
    try:
        async def go():
            async with page(url, width=1200, height=900) as e:
                await e.wait("document.querySelector('#folders .nav-item')")  # started: its own folder is listed
                await e.js("document.getElementById('view-settings').click(); true")
                await e.wait("document.querySelector('section[aria-label=Library]')")
                text = await e.js("document.querySelector('section[aria-label=Library]').textContent")
                assert "My Generations" in text and str(root) in text and "This PC's library" in text
                # a server alone can't switch: Open library is off, and says how instead
                assert await e.js("[...document.querySelectorAll('section[aria-label=Library] button')].find(b => b.textContent === 'Open library…').disabled")
                assert "--library" in text
                # a new library indexes its own folder
                assert str(root) in await e.js("document.querySelector('section[aria-label=Folders]').textContent")
                # nor can a server alone keep running in a tray: those switches are off, and say why
                assert await e.js("document.getElementById('set-tray').disabled && document.getElementById('set-tray-login').disabled")
                assert "own window, on Windows" in await e.js("document.querySelector('section[aria-label=\"In the background\"]').textContent")
        asyncio.run(go())
    finally:
        p.terminate()
        p.wait(10)


def test_search_marks_tags_and_details(server):
    url, api = server

    async def go():
        async with page(url) as e:
            await e.wait("document.querySelectorAll('.tile').length >= 5")
            # typing model:… and a space makes a chip; the grid narrows to it
            await e.js("const q = document.getElementById('q'); q.focus(); q.value = 'lora:aurelia '; q.dispatchEvent(new Event('input')); true")
            await e.wait("document.querySelector('#chips .chip .v')?.textContent === 'aurelia'")
            want = api("/files?q=lora:aurelia")["total"]
            await e.wait(f"document.getElementById('count').textContent === '{want} matches'")
            # suggestions for a field
            await e.js("const q = document.getElementById('q'); q.value = 'model:dre'; q.dispatchEvent(new Event('input')); true")
            await e.wait("!document.getElementById('suggest').hidden && document.getElementById('suggest').textContent.includes('dreamy_mix_v3')")
            assert "null" not in await e.js("document.getElementById('suggest').textContent")
            await e.js("document.querySelector('.s-item').dispatchEvent(new MouseEvent('mousedown', {bubbles: true})); true")
            await e.wait("document.querySelectorAll('#chips .chip').length === 2")
            both = api("/files?q=" + "lora:aurelia%20model:dreamy_mix_v3")["total"]
            await e.wait(f"document.getElementById('count').textContent.startsWith('{both} match')")
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
            await e.wait("document.getElementById('count').textContent === '1 match'")
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
            await e.js("const t = document.querySelector('input.tag-input'); t.value = 'ui-test'; t.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true})); true")
            await e.wait("[...document.querySelectorAll('.tags .chip')].some(c => c.textContent.includes('ui-test'))")
            assert "ui-test" in {t["name"] for t in api(f"/file/{first['id']}/info")["tags"]}
            # a note of your own, saved with Ctrl+Enter (or when the box loses focus)
            await e.js("const n = document.querySelector('textarea.note-input'); n.value = 'keep for the poster'; n.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', ctrlKey: true, bubbles: true})); true")
            await e.wait("document.querySelector('.note-hint').textContent === 'Saved'")
            assert api(f"/file/{first['id']}/info")["note"]["note"] == "keep for the poster"
            # clicking a model filters by it
            await e.js("document.querySelector('.model .link').click(); true")
            await e.wait("document.getElementById('viewer').hidden && document.querySelector('#chips .chip')")
            assert await e.js("document.querySelector('#chips .chip .k').textContent") == "model"
            await e.click("#q-clear")
            await e.wait("!document.querySelector('#chips .chip')")
            # the favourites filter
            await e.click("#f-fav")
            await e.wait("document.getElementById('count').textContent === '1 match'")
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
            # ? lists the keys and the search words
            await e.wait("document.getElementById('viewer').hidden")
            await e.key("?")
            await e.wait("document.querySelector('dialog.help[open]')")
            text = await e.js("document.querySelector('dialog.help').textContent")
            assert "Ctrl+Z" in text and "lora:" in text
            await e.js("document.querySelector('dialog.help').close(); true")
            await e.wait("!document.querySelector('dialog.help[open]')")
            assert not await e.js("window.__errors.length")
    asyncio.run(go())


def test_statistics_screen(server):
    url, api = server
    o = api("/stats?days=0")

    async def go():
        async with page(url) as e:
            await e.wait("document.querySelector('.tile')")
            await e.click("#view-stats")
            await e.wait("document.querySelector('.kpis') && document.querySelector('.st-stacks')")
            assert await e.js("getComputedStyle(document.querySelector('.toolbar')).display") == "none"
            await e.js("[...document.querySelectorAll('.st-head .seg button')].find(b => b.textContent === 'All time').click(); true")
            await e.wait(f"document.querySelector('.kpi .v').textContent === '{o['totals']['n']:,}'")
            assert await e.js("document.querySelectorAll('.tl-col').length") >= 1
            assert "wasn't found" in await e.js("document.querySelector('.st-stacks').textContent")  # no Model Librarian in the tests
            assert "null" not in await e.js("document.getElementById('stats').textContent")
            # a checkpoint's row opens the library filtered to it
            top = o["checkpoints"][0]
            await e.js("document.querySelector('.bars.ckpt .bar-row').click(); true")
            await e.wait("!document.getElementById('scroll').hidden && document.querySelector('#chips .chip')")
            assert await e.js("document.querySelector('#chips .chip .k').textContent") == "model"
            await e.wait(f"document.getElementById('count').textContent === '{top['n']} match{'' if top['n'] == 1 else 'es'}'")
            # a setting tab
            await e.click("#view-stats")
            await e.wait("document.querySelector('.st-stacks')")
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
                await e.wait("document.querySelector('.stats h1')?.textContent === 'To review' && document.querySelector('.r-card')")
                before = await e.js("document.querySelectorAll('.r-card').length")
                await e.js("const c = document.querySelector('.r-card'); c.focus(); c.dispatchEvent(new KeyboardEvent('keydown', {key: 'y', bubbles: true})); true")
                await e.wait(f"document.querySelectorAll('.r-card').length === {before - 1} || document.querySelector('.c-empty')")
                assert api(f"/characters/{ch['id']}")["counts"]["confirmed"] == 1
            # the details panel shows the character, and "Not Aurelia" takes it off that file
            await e.click("#view-all")
            await e.js("const q = document.getElementById('q'); q.value = 'character:aurelia '; q.dispatchEvent(new Event('input')); true")
            await e.wait(f"document.getElementById('count').textContent.startsWith('{api('/characters')['characters'][0]['n']} match')")
            await e.click(".tile")
            await e.wait("document.querySelector('.chip.char')")
            await e.js("document.querySelector('.chip.char button.x[title^=Not]').click(); true")
            await e.wait("!document.querySelector('.chip.char')")
            await e.key("Escape")
            assert not await e.js("window.__errors.length")
    asyncio.run(go())


def test_artists_and_new_downloads(server, tmp_path):
    url, api = server
    from conftest import image
    api("/library/kind", {"kinds": ["ai", "illustrations"]})  # (back to AI generations alone at the end)
    dl = tmp_path / "Downloads"
    got = image(dl / "harbour_by_painter_d1a2b3c.png", (96, 72), seed=7)
    os.utime(got, (time.time() - 600, time.time() - 600))
    api("/inbox/folders", {"downloads": False, "add": str(dl)})
    first = api("/files?limit=1")["items"][0]
    api(f"/file/{first['id']}/credit", {"artist": "Painter"})
    total = api("/files")["total"]
    op = None

    async def go():
        nonlocal op
        async with page(url) as e:
            await e.wait("!document.getElementById('view-artists').hidden && !document.getElementById('inbox-count').hidden")
            # the Artists list, and one artist's page
            await e.click("#view-artists")
            await e.wait("[...document.querySelectorAll('.c-card .c-name')].some(n => n.textContent === 'Painter')")
            await e.js("[...document.querySelectorAll('.c-card')].find(c => c.textContent.includes('Painter')).click(); true")
            await e.wait("document.querySelector('.c-head h1')?.textContent === 'Painter'")
            # New downloads: who made it (from its name), filed where Painter's other picture is, in one click
            await e.click("#view-downloads")
            await e.wait("document.querySelector('.dl-card .dl-artist')?.textContent.startsWith('painter')")
            await e.js("document.querySelector('.dl-card .dl-acts .btn.primary').click(); true")
            await e.wait("document.querySelector('.c-empty h2')?.textContent === 'Nothing new'")
            assert api("/files")["total"] == total + 1
            filed = api("/files?q=" + urllib.parse.quote('artist:painter'))["total"]
            assert filed == 2
            op = api("/ops")["ops"][0]
            # its details: the artist, from its name, and the source
            await e.click("#view-all")
            await e.js("const q = document.getElementById('q'); q.value = 'site:deviantart '; q.dispatchEvent(new Event('input')); true")
            await e.wait("document.getElementById('count').textContent.startsWith('1 match')")
            await e.click(".tile")
            await e.wait("document.querySelector('.credit .chip')?.textContent.includes('Painter')")  # the artist it found: Painter, the one you named
            assert "DeviantArt" in await e.js("document.querySelector('.credit').textContent")
            await e.key("Escape")
            assert not await e.js("window.__errors.length")
    try:
        asyncio.run(go())
    finally:  # the shared sample library as it was
        if op and op["kind"] == "file":
            api(f"/ops/{op['id']}/undo", {"x": 1})
        api(f"/file/{first['id']}/credit", {"artist": None})
        api("/library/kind", {"kinds": ["ai"]})
    assert os.path.isfile(got) and api("/files")["total"] == total


def test_swipe_review_and_smart_albums(server):
    url, api = server
    total = api("/files")["total"]

    async def go():
        async with page(url) as e:
            await e.wait("document.querySelectorAll('.tile').length > 4")
            # Swipe (in the sidebar): D keeps, A deletes (to the Trash), Z takes the last one back
            api("/albums", {"name": "Swiped"})
            pos = "document.querySelector('.sw-where')?.textContent"
            await e.click("#view-swipe")
            await e.wait(f"{pos}?.startsWith('1 / {total} ·')")
            await e.key("Enter")  # its keys are its own: the grid behind (and the sidebar's Swipe) get none
            await e.key("Delete")
            assert await e.js("document.querySelectorAll('.swipe').length") == 1 and api("/status")["trash"] == 0
            first = await e.js("document.querySelector('.sw-card:not(.under) .sw-name').textContent")
            await e.key("d")
            await e.wait(f"{pos}?.startsWith('2 / {total} ·')")
            assert await e.js("document.querySelector('.sw-card:not(.under) .sw-name').textContent") != first
            await e.key("a")
            await e.wait(f"{pos}?.startsWith('3 / {total} ·') && !document.querySelector('.sw-card.out-left')")
            assert api("/status")["trash"] == 1
            await e.key("z")
            await e.wait(f"{pos}?.startsWith('2 / {total} ·')")
            assert api("/status")["trash"] == 0
            # a star rates it and moves on; an album puts it there and moves on (Z takes it out again)
            rated = await e.js("document.querySelector('.sw-card:not(.under) img')?.alt")
            await e.js("document.querySelectorAll('.sw-star')[3].click(); true")
            await e.wait(f"{pos}?.startsWith('3 / {total} ·')")
            assert any(i["name"] == rated and i["rating"] == 4 for i in api("/files?q=rating:4&limit=500")["items"])
            await e.wait("[...document.querySelectorAll('.sw-album')].some(b => b.textContent === 'Swiped')")
            await e.js("[...document.querySelectorAll('.sw-album')].find(b => b.textContent === 'Swiped').click(); true")
            await e.wait(f"{pos}?.startsWith('4 / {total} ·')")
            assert api("/files?q=album:Swiped")["total"] == 1
            await e.key("z")
            await e.wait(f"{pos}?.startsWith('3 / {total} ·')")
            assert api("/files?q=album:Swiped")["total"] == 0
            # the picker: something else to go through, here a recent stretch of days
            await e.js("document.querySelector('.sw-pick').click(); true")
            await e.wait("document.querySelector('.sw-menu')")
            await e.js("[...document.querySelectorAll('.sw-menu .menu-item')].find(b => b.textContent === 'Last 30 days').click(); true")
            await e.wait("document.querySelector('.sw-pick').textContent === 'Last 30 days' && document.querySelector('.sw-where').textContent.startsWith('1 / ')")
            await e.key("Escape")
            await e.wait("!document.querySelector('.swipe') && document.querySelector('.toast')?.textContent.startsWith('Swipe review: 2 kept')")
            # a search kept as a smart album: in Albums with a search icon, holding what the search finds
            await e.js("const q = document.getElementById('q'); q.value = 'lora:aurelia '; q.dispatchEvent(new Event('input')); true")
            await e.wait("!document.getElementById('save-search').hidden")
            want = api("/files?q=lora:aurelia")["total"]
            await e.js("document.getElementById('save-search').click(); true")
            await e.wait("document.querySelector('dialog[open] input')")
            await e.js("const i = document.querySelector('dialog[open] input'); i.value = 'Aurelia picks'; i.form.requestSubmit(); true")
            await e.wait(f"document.getElementById('view-title').textContent === 'Aurelia picks' && document.getElementById('count').textContent === '{want} items'")
            assert await e.js("[...document.querySelectorAll('#albums .nav-item')].some(b => b.textContent.includes('Aurelia picks') && b.querySelector('[title^=\"Smart album\"]'))")
            assert not await e.js("window.__errors.length")
    try:
        asyncio.run(go())
    finally:  # the shared library as it was
        api("/sort/reset", {"view": {"sort": "newest"}})
        for a in api("/albums")["albums"]:
            if a["name"] in ("Aurelia picks", "Swiped"):
                req = urllib.request.Request(api.base + f"/api/albums/{a['id']}", method="DELETE", headers={"X-MLG-Token": api.token})
                urllib.request.urlopen(req).close()


def test_organising(server):
    url, api = server
    total = api("/files")["total"]

    async def go():
        async with page(url) as e:
            await e.wait("document.querySelectorAll('.tile').length > 4")
            # Ctrl+click picks two; the bar rates them
            await e.js("const t = document.querySelectorAll('.tile'); for (const i of [0, 1]) t[i].dispatchEvent(new MouseEvent('click', {bubbles: true, ctrlKey: true})); true")
            await e.wait("!document.getElementById('selbar').hidden && document.querySelector('.sel-n').textContent.startsWith('2 selected')")
            ids = await e.js("[...document.querySelectorAll('.tile.picked')].map(t => t.getAttribute('aria-label'))")
            assert len(ids) == 2
            await e.js("[...document.querySelectorAll('#selbar .btn')].find(b => b.textContent === 'Rate').click(); true")
            await e.wait("document.querySelector('.menu')")
            await e.js("document.querySelector('.menu [aria-label=\"Rate 3\"]').click(); true")
            await e.wait("document.querySelector('.toast')?.textContent.includes('Rated 2 files 3')")
            assert api("/files?q=rating:3")["total"] >= 2
            # into an album, from the dialog
            await e.js("[...document.querySelectorAll('#selbar .btn')].find(b => b.textContent.startsWith('Album')).click(); true")
            await e.wait("document.querySelector('dialog[open] input[aria-label=Album]')")
            await e.js("const d = document.querySelector('dialog[open]'); d.querySelector('input[aria-label=Album]').value = 'UI picks'; d.querySelector('button[type=submit]').click(); true")
            await e.wait("[...document.querySelectorAll('#albums .nav-item')].some(a => a.textContent.includes('UI picks'))")
            assert api("/files?q=album:%22UI%20picks%22")["total"] == 2
            # and to a character, a new one (made as it's named)
            await e.js("[...document.querySelectorAll('#selbar .btn')].find(b => b.textContent.startsWith('Character')).click(); true")
            await e.wait("document.querySelector('dialog[open] input[aria-label=Character]')")
            await e.js("const d = document.querySelector('dialog[open]'); d.querySelector('input[aria-label=Character]').value = 'UI pal'; d.querySelector('button[type=submit]').click(); true")
            await e.wait("document.querySelector('.toast')?.textContent.includes('added to UI pal')")
            assert api("/files?q=character:%22UI%20pal%22")["total"] == 2
            await e.key("Escape")
            await e.wait("document.getElementById('selbar').hidden")
            # Delete one, then Ctrl+Z puts it back
            await e.js("document.querySelector('.tile').focus(); document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {key: 'Delete', bubbles: true})); true")
            await e.wait(f"document.getElementById('count').textContent === '{total - 1} items'")
            assert api("/status")["trash"] == 1
            await e.key("z", ctrlKey=True)
            await e.wait(f"document.getElementById('count').textContent === '{total} items'")
            assert api("/status")["trash"] == 0
            # and from the Trash view: Put back (waiting for the grid, not the Trash's badge: it may still say 1 from the first)
            await e.js("document.querySelector('.tile').focus(); document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {key: 'Delete', bubbles: true})); true")
            await e.wait(f"document.getElementById('count').textContent === '{total - 1} items'")
            await e.click("#view-trash")
            await e.wait("document.getElementById('view-title').textContent === 'Trash' && document.querySelector('.tile')")
            assert not await e.js("document.getElementById('empty-trash').hidden")
            await e.js("document.querySelector('.tile').dispatchEvent(new MouseEvent('click', {bubbles: true, ctrlKey: true})); true")
            await e.js("[...document.querySelectorAll('#selbar .btn')].find(b => b.textContent.includes('Put back')).click(); true")
            await e.wait("document.querySelector('.toast')?.textContent.startsWith('Put back 1 file')")  # (only once the Gallery has done it)
            await e.wait("!document.getElementById('empty').hidden && document.querySelector('#empty h2')?.textContent === 'The Trash is empty'")
            assert api("/files")["total"] == total
            # History lists all of it; Duplicates opens
            await e.click("#view-history")
            await e.wait("document.querySelectorAll('.history li').length >= 4")
            await e.click("#view-duplicates")
            await e.wait("document.querySelector('.stats h1')?.textContent === 'Duplicates'")
            assert not await e.js("window.__errors.length")
    asyncio.run(go())


def test_delete_in_the_viewer_and_select_all(server):
    url, api = server
    total = api("/files")["total"]

    async def go():
        async with page(url) as e:
            await e.wait("document.querySelectorAll('.tile').length > 4")
            # Delete in the viewer: the file on screen goes, the next one is shown, and a second Delete takes that one
            await e.click(".tile")
            await e.wait("!document.getElementById('viewer').hidden")
            first = await e.js("document.getElementById('v-name').textContent")
            await e.key("Delete")
            await e.wait(f"document.getElementById('v-name').textContent !== {json.dumps(first)}"
                         f" && document.getElementById('v-pos').textContent.endsWith('of {total - 1}')")
            second = await e.js("document.getElementById('v-name').textContent")
            await e.key("Delete")
            await e.wait(f"document.getElementById('v-name').textContent !== {json.dumps(second)}")
            trashed = {i["name"] for i in api("/files?sort=trashed")["items"]}
            assert trashed == {first, second}
            await e.key("z", ctrlKey=True)
            await e.wait("document.querySelector('.toast')?.textContent === 'Undone.'")
            await e.js("document.querySelector('.toast').remove(); true")
            await e.key("z", ctrlKey=True)
            await e.wait("document.querySelector('.toast')?.textContent === 'Undone.'")
            assert api("/status")["trash"] == 0
            await e.key("Escape")
            await e.wait("document.getElementById('viewer').hidden")
            # Select all, then a search: the selection goes, so the bar can't act on files the grid doesn't show
            await e.key("a", ctrlKey=True)
            await e.wait("!document.getElementById('selbar').hidden && document.querySelector('.sel-n').textContent.startsWith('All')")
            assert "null" not in await e.js("document.getElementById('selbar').textContent")
            await e.js("const q = document.getElementById('q'); q.value = 'lora:aurelia '; q.dispatchEvent(new Event('input')); true")
            await e.wait("document.getElementById('selbar').hidden && document.querySelector('#chips .chip')")
            assert not await e.js("window.__errors.length")
    asyncio.run(go())


def test_rotate_in_the_viewer_and_undo(server):
    url, api = server
    png = next(i for i in api("/files?q=has:workflow&limit=200")["items"] if i["name"].endswith(".png") and i["width"] != i["height"])

    async def go():
        async with page(url) as e:
            await e.js(f"const q = document.getElementById('q'); q.value = {json.dumps(png['name'].split('.')[0].replace('_', ' '))}; q.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Enter', bubbles: true}})); true")
            pick = f"[...document.querySelectorAll('.tile')].find(t => t.getAttribute('aria-label') === {json.dumps(png['name'])})"
            await e.wait(pick)
            await e.js(f"{pick}.click(); true")
            await e.wait("document.querySelector('#v-media img.on') && document.getElementById('v-zoom-pct').textContent.endsWith('%')")
            await e.key("R", shiftKey=True)  # shown turned at once; written only on Save
            await e.wait("!document.getElementById('v-edit').hidden")
            assert api(f"/file/{png['id']}/info")["width"] == png["width"]
            assert await e.js("document.querySelector('#v-edit .btn.primary').textContent") == "Save as copy"  # keeping the original comes first
            await e.js("[...document.querySelectorAll('#v-edit .btn')].find(b => b.textContent === 'Replace original').click(); true")
            await e.wait("document.querySelector('.toast')?.textContent.startsWith('Saved')")
            info = api(f"/file/{png['id']}/info")
            assert (info["width"], info["height"]) == (png["height"], png["width"])
            assert info["generation"]["positive"]  # the prompt and workflow came through
            await e.js("document.querySelector('.toast .btn').click(); true")  # Undo
            await e.wait("document.querySelector('.toast')?.textContent === 'Undone.'")
            assert api(f"/file/{png['id']}/info")["width"] == png["width"]
            # Enter saves a copy beside it; the original stays as it was, and Undo moves the copy to the Trash
            total = api("/files")["total"]
            await e.js("document.querySelector('.toast')?.remove(); true")
            await e.key("R", shiftKey=True)
            await e.wait("!document.getElementById('v-edit').hidden")
            await e.key("Enter")
            await e.wait("document.querySelector('.toast')?.textContent.includes('(edited)')")
            assert api("/files")["total"] == total + 1 and api(f"/file/{png['id']}/info")["width"] == png["width"]
            await e.js("document.querySelector('.toast .btn').click(); true")
            await e.wait("document.querySelector('.toast')?.textContent === 'Undone.'")
            assert api("/files")["total"] == total
            # crop mode: the box and its size; Esc leaves it
            await e.key("C", shiftKey=True)
            await e.wait("document.querySelector('.v-crop') && document.querySelectorAll('#v-edit .v-num').length === 2")
            await e.key("Escape")
            await e.wait("!document.querySelector('.v-crop') && document.getElementById('v-edit').hidden && !document.getElementById('viewer').hidden")
            assert not await e.js("window.__errors.length")
    asyncio.run(go())


def test_albums_and_subfolders_nest_in_the_sidebar(server):
    url, api = server
    ids = [i["id"] for i in api("/files?limit=3")["items"]]
    api("/ops/bulk", {"selection": ids[:1], "action": "album", "value": "Nest / Inner"})
    api("/ops/bulk", {"selection": ids[1:3], "action": "album", "value": "Nest/Inner/Deep"})

    async def go():
        async with page(url) as e:
            rows = "[...document.querySelectorAll('#albums .nav-item')].map(b => b.title)"
            await e.wait(f"{rows}.includes('Nest')")
            assert "Nest / Inner" not in await e.js(rows)  # closed until opened
            await e.js("[...document.querySelectorAll('#albums .nav-item')].find(b => b.title === 'Nest').click(); true")
            await e.wait("document.getElementById('view-title').textContent === 'Nest' && document.querySelectorAll('.tile').length === 3")
            await e.js("[...document.querySelectorAll('#albums .nav-item')].find(b => b.title === 'Nest').querySelector('.twist').click(); true")
            await e.wait(f"{rows}.includes('Nest / Inner')")
            n = await e.js("[...document.querySelectorAll('#albums .nav-item')].find(b => b.title === 'Nest').querySelector('.n').textContent")
            assert n == "3"
            # a move (as a drag or Move to... makes) takes the album inside with it, and the sidebar follows
            await e.js("[...document.querySelectorAll('#albums .nav-item')].find(b => b.title === 'Nest / Inner').click(); true")
            await e.wait("document.getElementById('view-title').textContent === 'Nest / Inner'")
            # a parent that's only a name can be deleted with what's inside it, from its right-click menu
            await e.js("[...document.querySelectorAll('#albums .nav-item')].find(b => b.title === 'Nest').dispatchEvent(new MouseEvent('contextmenu', {bubbles: true})); true")
            await e.wait("document.querySelector('.menu')?.innerText.includes('Delete the 2 albums inside')")
            await e.key("Escape")
            # Tidy up: Nest's albums hold a few of a folder's pictures, not all of them: not offered
            await e.js("document.getElementById('album-tidy').click(); true")
            await e.wait("document.querySelector('.tidy-list') || document.querySelector('.toast')?.textContent.startsWith('No album only repeats')")
            assert not await e.js("[...document.querySelectorAll('.tidy-list .tl-name')].some(n => n.title.startsWith('Nest'))")
            await e.js("document.querySelector('dialog[open]')?.close(); true")
            # a folder's subfolders: opened from its arrow, each a view of what's in it and deeper
            await e.js("document.querySelector('#folders .twist:not(.none)').click(); true")
            await e.wait("document.querySelectorAll('#folders .nav-item').length > 1")
            want = await e.js("+document.querySelectorAll('#folders .nav-item')[1].querySelector('.n').textContent")
            await e.js("document.querySelectorAll('#folders .nav-item')[1].click(); true")
            await e.wait(f"document.getElementById('view-title').textContent.includes(' / ') && document.querySelectorAll('.tile').length === {want}")
            assert not await e.js("window.__errors.length")
    asyncio.run(go())
    r = api("/albums/move", {"path": "Nest / Inner", "to": "Inner"})
    assert r["moved"] == 2
    names = [a["name"] for a in api("/albums")["albums"]]
    assert "Inner / Deep" in names and not any(n.startswith("Nest") for n in names)
    for a in api("/albums")["albums"]:
        if a["name"].startswith("Inner"):
            urllib.request.urlopen(urllib.request.Request(f"{api.base}/api/albums/{a['id']}", headers={"X-MLG-Token": api.token}, method="DELETE"))


def test_the_phone_layout(server):
    url, api = server
    total = api("/files")["total"]
    phone = url.replace("/?t=", "/m?t=")

    async def go():
        async with page(phone, width=390, height=844) as e:
            await e.wait("document.querySelectorAll('.p-tile').length > 4")
            assert await e.js("document.documentElement.scrollWidth <= innerWidth")  # nothing wider than the phone
            assert await e.js("document.querySelector('.p-title').textContent") == f"All{total:,}"
            # a long press selects, and the bar becomes what can be done with the selection
            await e.js("const t = document.querySelectorAll('.p-tile')[1]; t.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true, clientX: 5, clientY: 5})); true")
            await asyncio.sleep(0.6)
            await e.js("const t = document.querySelectorAll('.p-tile')[1]; t.dispatchEvent(new PointerEvent('pointerup', {bubbles: true})); t.click(); true")
            await e.wait("document.querySelector('.p-title').textContent === '1 selected'")
            assert "Delete" in await e.js("document.querySelector('.p-bar').innerText")
            await e.js("document.querySelector('.p-top .p-icon').click(); true")
            await e.wait("!document.querySelector('.p-app').classList.contains('selecting')")
            # a tap opens the viewer; a swipe to the left shows the next one
            await e.js("document.querySelector('.p-tile').click(); true")
            await e.wait("document.querySelector('.pv-pos')?.textContent.startsWith('1 of')")
            await e.js("""const s = document.querySelector('.pv-stage'), ev = (t, x) => s.dispatchEvent(new PointerEvent(t, {bubbles: true, pointerId: 7, clientX: x, clientY: 400}));
                ev('pointerdown', 320); ev('pointermove', 200); ev('pointermove', 60); ev('pointerup', 60); true""")
            await e.wait("document.querySelector('.pv-pos')?.textContent.startsWith('2 of')")
            await e.js("document.querySelector('.pv-top [aria-label=Details]').click(); true")
            await e.wait("document.querySelector('.pv-sheet')?.innerText.includes('TAGS')")
            assert "[object" not in await e.js("document.querySelector('.pv-sheet').innerText")  # (each part drawn, none as text)
            await e.js("document.querySelector('.pv-top [aria-label=Close]').click(); true")
            await e.wait("!document.querySelector('.p-viewer')")
            # Browse: the folders and albums, as in the sidebar
            await e.js("[...document.querySelectorAll('.p-bar .p-tab')].find(b => b.innerText === 'Browse').click(); true")
            await e.wait("document.querySelector('.p-browse')?.innerText.includes('FOLDERS')")
            assert not await e.js("window.__errors.length"), await e.js("window.__errors")
    asyncio.run(go())


def test_the_sidebar_tree_by_keyboard_and_overlays_holding_the_focus(server):
    url, api = server

    async def go():
        async with page(url) as e:
            await e.wait("document.querySelectorAll('.tile').length > 4")
            # the folder tree: a tree of rows (no button inside one), one of them in the Tab order
            assert await e.js("document.getElementById('folders').getAttribute('role')") == "tree"
            assert await e.js("document.querySelectorAll('[role=treeitem] button, [role=treeitem] [role=button]').length") == 0
            assert await e.js("[...document.querySelectorAll('#folders [role=treeitem]')].filter(r => r.tabIndex === 0).length") == 1
            await e.js("document.querySelector('#folders [role=treeitem]').focus(); true")
            key = lambda k: e.js(f"document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {{key: '{k}', bubbles: true}})); true")
            if await e.js("document.querySelector('#folders [role=treeitem]').getAttribute('aria-expanded') === 'true'"):
                await key("ArrowLeft")  # (open from an earlier test: closed first)
                await e.wait("document.querySelector('#folders [role=treeitem]').getAttribute('aria-expanded') === 'false'")
            await key("ArrowRight")  # opens it
            await e.wait("document.querySelector('#folders [role=treeitem]').getAttribute('aria-expanded') === 'true'")
            await key("ArrowRight")  # then into it
            await e.wait("document.activeElement?.getAttribute('aria-level') === '2'")
            await key("Enter")  # shows that subfolder
            await e.wait("document.getElementById('view-title').textContent.includes(' / ') && document.activeElement?.getAttribute('aria-level') === '2'")
            await key("ArrowLeft")  # back to the folder it's in
            await e.wait("document.activeElement?.getAttribute('aria-level') === '1'")
            await key("ArrowLeft")  # and closed
            await e.wait("document.querySelector('#folders [role=treeitem]').getAttribute('aria-expanded') === 'false'")
            await e.js("document.getElementById('view-all').click(); true")
            # the viewer holds Tab: the grid behind it is out of reach, the sidebar beside it isn't; closed, focus is back on the tile
            await e.wait("document.querySelectorAll('.tile').length > 4")
            await e.js("document.querySelector('.tile').focus(); document.querySelector('.tile').click(); true")
            await e.wait("!document.getElementById('viewer').hidden")
            assert await e.js("document.querySelector('main.main').inert && !document.getElementById('side').closest('[inert]')")
            await e.key("Escape")
            await e.wait("document.getElementById('viewer').hidden")
            assert await e.js("!document.querySelector('main.main').inert && document.activeElement.classList.contains('tile')")
            # Swipe holds it too, all of the page behind
            await e.click("#view-swipe")
            await e.wait("document.querySelector('.swipe')")
            assert await e.js("document.getElementById('app').inert && document.querySelector('.swipe').contains(document.activeElement)")
            await e.key("Escape")
            await e.wait("!document.querySelector('.swipe') && !document.getElementById('app').inert")
            assert not await e.js("window.__errors.length"), await e.js("window.__errors")
    asyncio.run(go())
