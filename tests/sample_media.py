"""Generated sample media for tests and README screenshots: never anyone's real outputs.

    python tests/sample_media.py OUT_DIR [--images N] [--videos N]

Images are soft abstract "generations" (gradients, hills, a sun) in the sizes image models make, with
modified times spread over the last few weeks so the grid has days to group by. Videos (when ffmpeg is
on PATH) are short clips made with ffmpeg's own test sources.
"""
from __future__ import annotations

import argparse
import math
import os
import random
import shutil
import subprocess
import time

SIZES = [(896, 1152), (1152, 896), (1024, 1024), (832, 1216), (1216, 832), (768, 1344)]
PALETTES = [
    ("#f7dccf", "#f4a7c0", "#6d4a8f"), ("#0f2027", "#2c5364", "#e0a96d"), ("#fbd3e9", "#bb377d", "#2b1a3a"),
    ("#c9d6ff", "#e2e2e2", "#36454f"), ("#ffe29f", "#ff719a", "#3b1f47"), ("#a1c4fd", "#c2e9fb", "#1d3557"),
    ("#d4fc79", "#96e6a1", "#1b4332"), ("#f6d365", "#fda085", "#5c2a1d"), ("#8fcfe3", "#b9a3e3", "#22223b"),
]


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def make_image(path: str, size: tuple[int, int], seed: int) -> None:
    from PIL import Image, ImageDraw, ImageFilter
    rnd = random.Random(seed)
    w, h = size
    top, mid, ink = (_rgb(c) for c in rnd.choice(PALETTES))
    small = (w // 3, h // 3)  # drawn smaller and scaled up: soft, and quick
    im = Image.new("RGB", small)
    px = im.load()
    for y in range(small[1]):
        t = y / max(1, small[1] - 1)
        c = tuple(int(top[i] * (1 - t) + mid[i] * t) for i in range(3))
        for x in range(small[0]):
            px[x, y] = c
    d = ImageDraw.Draw(im)
    sx, sy, sr = rnd.uniform(.2, .8) * small[0], rnd.uniform(.15, .45) * small[1], rnd.uniform(.06, .14) * min(small)
    d.ellipse([sx - sr, sy - sr, sx + sr, sy + sr], fill=tuple(min(255, v + 40) for v in top))
    for layer in range(rnd.randint(2, 4)):
        base = small[1] * (0.55 + 0.12 * layer)
        amp, freq, phase = rnd.uniform(16, 60), rnd.uniform(1, 3), rnd.uniform(0, 6.3)
        k = (layer + 1) / 5
        col = tuple(int(mid[i] * (1 - k) + ink[i] * k) for i in range(3))
        pts = [(x, base - amp * math.sin(freq * x / small[0] * 6.28 + phase)) for x in range(0, small[0] + 3, 3)]
        d.polygon(pts + [(small[0], small[1]), (0, small[1])], fill=col)
    im = im.resize((w, h), Image.BICUBIC).filter(ImageFilter.GaussianBlur(2.2))
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        im.save(path, quality=88)
    elif ext == ".webp":
        im.save(path, quality=85)
    else:
        im.save(path, optimize=False, compress_level=1)


def make_video(path: str, seed: int, seconds: float = 2.0) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    src = ["testsrc2", "mandelbrot", "life=s=320x240:mold=10:r=24:ratio=0.1"][seed % 3]
    if "=" not in src:
        src += "=s=480x320:r=24"
    r = subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", src, "-t", str(seconds), "-pix_fmt", "yuv420p",
                        "-c:v", "libx264" if path.endswith(".mp4") else "libvpx-vp9", path], capture_output=True)
    return r.returncode == 0


def make_library(out: str, images: int = 60, videos: int = 3, days: int = 21, seed: int = 7) -> list[str]:
    """Images and videos under out/ (with a subfolder and a hidden folder that must be skipped). -> the files made."""
    rnd = random.Random(seed)
    now = time.time()
    made = []
    for i in range(images):
        sub = "batch_b" if i % 5 == 4 else ""
        folder = os.path.join(out, sub)
        os.makedirs(folder, exist_ok=True)
        ext = [".png", ".png", ".png", ".jpg", ".webp"][i % 5]
        p = os.path.join(folder, f"sample_{i:05d}_{ext[1:]}{ext}")
        make_image(p, rnd.choice(SIZES) if i % 7 else (1024, 1024), seed * 1000 + i)
        t = now - (i / max(1, images)) * days * 86400 - rnd.uniform(0, 3600)
        os.utime(p, (t, t))
        made.append(p)
    for i in range(videos):
        p = os.path.join(out, f"clip_{i:03d}.mp4")
        if make_video(p, i):
            t = now - i * 3 * 86400 - 1800
            os.utime(p, (t, t))
            made.append(p)
    hidden = os.path.join(out, ".cache")
    os.makedirs(hidden, exist_ok=True)
    make_image(os.path.join(hidden, "not_indexed.png"), (256, 256), 1)
    return made


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--images", type=int, default=60)
    ap.add_argument("--videos", type=int, default=3)
    a = ap.parse_args()
    files = make_library(a.out, a.images, a.videos)
    print(f"{len(files)} files in {a.out}")
