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
    files = {"checkpoints": ["sample_xl_base", "dreamy_mix_v3", "turbo_sample_int4", "retro_anime_v5", "photoreal_mix_v2"],
             "diffusion_models": ["video_sample_14b_fp8"],
             "loras": ["paper_cut_style_v2", "aurelia_character_v3", "soft_light_v1", "old_ink_style_v1", "neon_city_v2", "detail_slider"]}
    for folder, names in files.items():
        os.makedirs(os.path.join(root, folder), exist_ok=True)
        for i, n in enumerate(names):
            with open(os.path.join(root, folder, n + ".safetensors"), "wb") as f:
                f.truncate((i + 2) * 211 * 1024 * 1024)  # sparse: takes no space
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--url")
    ap.add_argument("--media")
    a = ap.parse_args()
    proc = None
    url = a.url
    if not url:
        tmp = tempfile.mkdtemp(prefix="mlg-shots-")
        media = a.media or os.path.join(tmp, "Samples")
        if not a.media:
            make_library(media, images=140, videos=3, days=6)
        env = dict(os.environ, MLG_ML_DATA=fake_model_library(os.path.join(tmp, "models")))
        proc = subprocess.Popen([sys.executable, "-m", "mlgallery", "--server", "--data", os.path.join(tmp, "data")], cwd=ROOT,
                                stdout=subprocess.PIPE, text=True, env=env)
        url = proc.stdout.readline().strip()
        api_for(url)("/folders", {"path": media})
    api = api_for(url)
    wait_idle(api)
    # a few marks, so the grid shows them
    items = api("/files?limit=40")["items"]
    for it, (r, fav) in zip(items[1:12:2], [(5, True), (4, False), (3, False), (5, False), (4, True), (2, False)]):
        api(f"/file/{it['id']}/mark", {"rating": r, "favorite": fav})

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
        ("stats", [lambda e: js(e, "document.getElementById('v-close').click(); true"),
                   lambda e: js(e, "document.getElementById('view-stats').click(); true"),
                   lambda e: e.wait("document.querySelector('.kpis') && document.querySelector('.tl-col')")]),
        ("stats-more", [lambda e: js(e, "const s = document.getElementById('stats'); s.scrollTop = s.scrollHeight; true")]),
    ]
    os.makedirs(a.out, exist_ok=True)
    try:
        asyncio.run(capture(url, a.out, scenes))
    finally:
        if proc:
            proc.terminate()


if __name__ == "__main__":
    main()
