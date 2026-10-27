"""Screenshots of the Gallery on generated sample media, for the README and for checking the look.

    python tools/shots.py OUT_DIR [--url URL] [--media DIR]

Without --url it starts its own server on a throwaway data folder and a fresh sample library (so nothing of
yours is ever in a screenshot). Needs Microsoft Edge.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))

from edge import Edge  # noqa: E402
from sample_media import make_library  # noqa: E402


def fake_model_library(root: str) -> str:
    """A made-up Model Librarian library (invented names, empty files), so "Never used" has something to show."""
    data = os.path.join(root, ".sm_model_fetcher")
    os.makedirs(data, exist_ok=True)
    with open(os.path.join(data, "registry.json"), "w") as f:
        json.dump({"version": 1, "entries": {}}, f)
    with open(os.path.join(data, "usage.json"), "w") as f:
        json.dump({"last_used": {"loras:old_ink_style_v1": time.time() - 40 * 86400}}, f)
    # in family folders, as Model Librarian's organizer files them (that's how the Gallery knows a model's family)
    files = {"checkpoints": ["SDXL/sample_xl_base", "Illustrious/dreamy_mix_v3", "Qwen/turbo_sample_int4", "Moss/retro_anime_v5", "Flux/photoreal_mix_v2"],
             "diffusion_models": ["video_sample_14b_fp8"],
             "loras": ["paper_cut_style_v2", "aurelia_character_v3", "soft_light_v1", "old_ink_style_v1", "neon_city_v2", "detail_slider"]}
    for folder, names in files.items():
        for i, n in enumerate(names):
            os.makedirs(os.path.dirname(os.path.join(root, folder, n)), exist_ok=True)
            with open(os.path.join(root, folder, n + ".safetensors"), "wb") as f:
                f.write(b"\0" * 1024 * (i + 1))  # tiny placeholders: never real sizes (Windows doesn't make sparse files)
    return data


def api_for(url):
    base, token = url.split("/?t=")

    def api(path, body=None, method=None):
        req = urllib.request.Request(base + "/api" + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-MLG-Token": token, "Content-Type": "application/json"},
                                     method=method or ("POST" if body is not None else "GET"))
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    return api


def wait_idle(api, timeout=180):
    t = time.time()
    while time.time() - t < timeout:
        st = api("/status")
        if st["files"] and not (st["scanning"] or st["queued"] or st["thumbs_pending"] or st.get("meta_pending")):
            return st
        time.sleep(0.4)
    raise TimeoutError("the library didn't finish indexing")


async def capture(url, out, scenes):
    async with Edge(url, 1440, 900) as e:
        await e.wait("document.querySelectorAll('.tile img.on').length >= 12", 60)
        await asyncio.sleep(0.6)
        for name, steps in scenes:
            for s in steps:
                await s(e)
            await asyncio.sleep(0.5)
            await e.shot(os.path.join(out, f"{name}.png"))
            print("wrote", name)


# a phone's share sheet, stood in for: its Save shows, as it does over HTTPS
PHONE_SHARE = "Object.defineProperty(navigator, 'canShare', {value: () => true}); Object.defineProperty(navigator, 'share', {value: async () => {}});"


async def capture_phone(url, out):
    """The phone layout (/m on this PC shows it) at an iPhone's size: the grid, and a picture in the viewer."""
    async with Edge(url.replace("/?t=", "/m?t="), 390, 844, init=PHONE_SHARE) as e:
        await e.wait("[...document.querySelectorAll('.p-tile img')].filter(i => i.complete && i.naturalWidth).length >= 9", 60)
        await asyncio.sleep(0.6)
        await e.shot(os.path.join(out, "phone-grid.png"))
        print("wrote phone-grid")
        await e.js("document.querySelectorAll('.p-tile')[2].click(); true")
        await e.wait("document.querySelector('.pv-stage img')?.complete")
        await asyncio.sleep(0.6)
        await e.shot(os.path.join(out, "phone-viewer.png"))
        print("wrote phone-viewer")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--url")
    ap.add_argument("--media")
    a = ap.parse_args()
    proc = None
    tmp = None
    url = a.url
    if not url:
        tmp = tempfile.mkdtemp(prefix="mlg-shots-")
        media = a.media or os.path.join(tmp, "Samples")
        if not a.media:
            made = make_library(media, images=140, videos=3, days=6)
            # a copy and a picture saved again, so Duplicates has something to show
            import shutil
            from PIL import Image
            pngs = [m for m in made if m.endswith(".png")]
            shutil.copy2(pngs[3], os.path.join(media, "batch_b", "copy of " + os.path.basename(pngs[3])))
            Image.open(pngs[5]).convert("RGB").save(os.path.join(media, os.path.splitext(os.path.basename(pngs[5]))[0] + "_again.jpg"), quality=90)
        env = dict(os.environ, MLG_ML_DATA=fake_model_library(os.path.join(tmp, "models")))
        proc = subprocess.Popen([sys.executable, "-m", "mlgallery", "--server", "--data", os.path.join(tmp, "data")], cwd=ROOT,
                                stdout=subprocess.PIPE, text=True, env=env)
        url = proc.stdout.readline().strip()
        api_for(url)("/folders", {"path": media})
        api_for(url)("/library/kind", {"kinds": ["ai"]})  # the sample library is AI generations: answers the first-run question
    api = api_for(url)
    wait_idle(api)
    # a few marks, so the grid shows them
    items = api("/files?limit=40")["items"]
    for it, (r, fav) in zip(items[1:12:2], [(5, True), (4, False), (3, False), (5, False), (4, True), (2, False)]):
        api(f"/file/{it['id']}/mark", {"rating": r, "favorite": fav})

    # two characters: one its LoRA gives away, one only a weak tag suggests (so To review has something)
    api("/characters", {"name": "Aurelia", "aliases": ["Aury"], "rules": [{"kind": "lora", "value": "aurelia_character"}, {"kind": "word", "value": "aurelia"}]})
    glow = api("/characters", {"name": "Glow", "rules": [{"kind": "tag", "value": "soft light", "strength": "weak"}]})
    # a folder and an album with an icon and colour of their own, as you'd set from the sidebar
    for f in api("/folders")["folders"][:1]:
        api(f"/folders/{f['id']}/look", {"icon": "svg:sparkle", "color": "#a78bfa"})
    for al in api("/albums")["albums"][:1]:
        api(f"/albums/{al['id']}/look", {"icon": "svg:mountain", "color": "#22d3ee"})

    async def js(e, code):
        await e.js(code)

    first_png = next(i for i in items if i["name"].endswith(".png"))
    scenes = [
        ("library", []),
        ("search", [lambda e: js(e, "document.getElementById('q').focus(); true"),
                    lambda e: js(e, "const q = document.getElementById('q'); q.value = 'lora:aurelia glacier'; q.dispatchEvent(new Event('input')); true"),
                    lambda e: e.wait("document.querySelector('.chip')"),
                    lambda e: js(e, "document.getElementById('q').blur(); true"),
                    lambda e: e.wait("document.querySelector('.tile img.on')")]),
        ("viewer", [lambda e: js(e, "document.getElementById('q-clear').click(); true"),
                    lambda e: e.wait("!document.querySelector('.chip') && document.querySelector('.tile')"),
                    lambda e: js(e, f"([...document.querySelectorAll('.tile')].find(t => t.getAttribute('aria-label') === {json.dumps(first_png['name'])}) || document.querySelector('.tile')).click(); true"),
                    lambda e: e.wait("document.querySelector('.v-panel .models') && document.querySelector('#v-media img')?.complete")]),
        ("crop", [lambda e: e.key("C", shiftKey=True), lambda e: e.wait("document.querySelector('.v-crop')"), lambda e: asyncio.sleep(0.3)]),
        ("stats", [lambda e: js(e, "document.getElementById('v-close').click(); true"),
                   lambda e: js(e, "document.getElementById('view-stats').click(); true"),
                   lambda e: e.wait("document.querySelector('.kpis') && document.querySelector('.tl-col')")]),
        ("stats-more", [lambda e: js(e, "const s = document.getElementById('stats'); s.scrollTop = s.scrollHeight; true")]),
        ("selection", [lambda e: js(e, "document.getElementById('view-all').click(); true"),
                       lambda e: e.wait("document.querySelectorAll('.tile img.on').length > 8"),
                       lambda e: js(e, "const t = document.querySelectorAll('.tile'); for (const i of [1, 2, 4, 7]) t[i].dispatchEvent(new MouseEvent('click', {bubbles: true, ctrlKey: true})); true"),
                       lambda e: asyncio.sleep(0.4)]),
        ("duplicates", [lambda e: js(e, "document.querySelector('#selbar .icon-btn').click(); document.getElementById('view-duplicates').click(); true"),
                        lambda e: e.wait("document.querySelector('.d-file img')"), lambda e: asyncio.sleep(0.8)]),
        ("settings", [lambda e: js(e, "document.getElementById('view-settings').click(); true"),
                      lambda e: e.wait("document.querySelector('.set-row')"), lambda e: asyncio.sleep(0.4)]),
        ("characters", [lambda e: js(e, "document.getElementById('view-characters').click(); true"),
                        lambda e: e.wait("document.querySelectorAll('.c-card').length >= 2 && [...document.querySelectorAll('.c-card img')].every(i => i.complete)")]),
        ("character", [lambda e: js(e, "document.querySelector('.c-card').click(); true"),
                       lambda e: e.wait("document.querySelector('.rules') && document.querySelector('.histo')"),
                       lambda e: asyncio.sleep(0.8)]),
        ("review", [lambda e: js(e, "document.getElementById('view-review').click(); true"),
                    lambda e: e.wait("document.querySelector('.r-card img, .rf-stage img')?.complete"),  # (one at a time, since 1.12)
                    lambda e: asyncio.sleep(0.8)]),
        ("review-next", [lambda e: e.key("ArrowRight"), lambda e: e.wait("document.querySelector('.rf-n')?.textContent.startsWith('2 of') && document.querySelector('.rf-stage img')?.complete"),
                         lambda e: asyncio.sleep(0.6)]),  # (the next one: the sample library's first is its test-pattern video)
        ("waterfall", [lambda e: js(e, "document.getElementById('view-all').click(); true"),
                       lambda e: e.wait("document.querySelectorAll('.tile img.on').length > 8"),
                       lambda e: js(e, "document.getElementById('view-btn').click(); true"),
                       lambda e: js(e, "document.querySelector('.view-panel .seg button[data-v=waterfall]').click(); true"),
                       lambda e: js(e, "[...document.querySelectorAll('.view-panel .vp-row')].find(r => r.textContent.startsWith('Name')).querySelector('.switch').click(); true"),
                       lambda e: js(e, "[...document.querySelectorAll('.view-panel .vp-row')].find(r => r.textContent.startsWith('Info')).querySelector('.switch').click(); true"),
                       lambda e: js(e, "document.querySelector('.menu')._close(); true"),
                       lambda e: e.wait("document.querySelector('.tile .t-cap') && document.querySelectorAll('.tile img.on').length > 8"),
                       lambda e: asyncio.sleep(0.6)]),
        ("view-panel", [lambda e: js(e, "document.getElementById('view-btn').click(); true"), lambda e: asyncio.sleep(0.2)]),
        ("folder-menu", [lambda e: js(e, "document.querySelector('.menu')?._close(); document.querySelector('#folders .nav-item').dispatchEvent(new MouseEvent('contextmenu', {bubbles: true})); true"),
                         lambda e: e.wait("document.querySelector('.look-colors')")]),
        ("icon-picker", [lambda e: js(e, "document.querySelector('.menu .menu-item').click(); true"), lambda e: e.wait("document.querySelector('.look-grid')")]),
        ("grid-captions", [lambda e: js(e, "document.querySelector('.menu')?._close(); document.getElementById('view-btn').click(); true"),
                           lambda e: js(e, "document.querySelector('.view-panel .seg button[data-v=grid]').click(); document.querySelector('.menu')._close(); true"),
                           lambda e: e.wait("document.querySelectorAll('.tile img.on').length > 8"), lambda e: asyncio.sleep(0.4)]),
        ("swipe", [lambda e: js(e, "document.getElementById('view-swipe').click(); true"),
                   lambda e: e.wait("document.querySelector('.sw-card:not(.under) img')?.complete"), lambda e: asyncio.sleep(0.8)]),
    ]
    os.makedirs(a.out, exist_ok=True)
    try:
        asyncio.run(capture(url, a.out, scenes))
        asyncio.run(capture_phone(url, a.out))
    finally:
        if proc:
            proc.terminate()
            proc.wait(15)
        if tmp:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)  # the sample library, its index and the fake models


if __name__ == "__main__":
    main()
