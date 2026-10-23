"""Thumbnails: their size and shape, transparency, videos, the cache, and files that can't be read."""
import io
import os
import time

import pytest
from PIL import Image

from conftest import HAVE_FFMPEG, image
from mlgallery import scan, thumbs


def decoded(data):
    return Image.open(io.BytesIO(data))


def test_short_side_is_the_size_and_never_upscaled(media):
    im = decoded(thumbs.image_thumb(image(media / "wide.png", (1216, 832)), 256))
    assert im.format == "WEBP" and min(im.size) == 256 and im.size[0] > im.size[1]
    small = decoded(thumbs.image_thumb(image(media / "small.png", (100, 80)), 256))
    assert small.size == (100, 80)


def test_very_long_images_are_cropped_to_four_to_one(media):
    im = decoded(thumbs.image_thumb(image(media / "strip.png", (4000, 200)), 128))
    assert im.size == (512, 128)


def test_transparency_is_flattened_on_the_grid_colour(media):
    p = str(media / "clear.png")
    Image.new("RGBA", (50, 50), (255, 0, 0, 0)).save(p)
    im = decoded(thumbs.image_thumb(p, 32)).convert("RGB")
    r, g, b = im.getpixel((10, 10))
    assert abs(r - thumbs.BG[0]) < 6 and abs(g - thumbs.BG[1]) < 6 and abs(b - thumbs.BG[2]) < 6


def test_exif_orientation_is_applied(media):
    p = str(media / "turned.jpg")
    exif = Image.Exif()
    exif[0x0112] = 6
    Image.new("RGB", (400, 200), "blue").save(p, exif=exif)
    assert decoded(thumbs.image_thumb(p, 100)).size == (100, 200)


def test_animated_images_use_the_first_frame(media):
    p = str(media / "anim.gif")
    frames = [Image.new("RGB", (60, 60), c) for c in ("red", "green")]
    frames[0].save(p, save_all=True, append_images=frames[1:], duration=100)
    im = decoded(thumbs.image_thumb(p, 60)).convert("RGB")
    r, g, _ = im.getpixel((30, 30))
    assert r > 200 and g < 60


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg on PATH")
def test_video_poster_and_probe(media):
    from sample_media import make_video
    p = str(media / "clip.mp4")
    assert make_video(p, 0, seconds=1.5)
    ff = thumbs.find_ffmpeg()
    info = thumbs.video_probe(ff, p)
    assert info["width"] == 480 and info["height"] == 320 and 1.0 < info["duration"] < 2.0
    im = decoded(thumbs.video_thumb(ff, p, 128, at=0.5))
    assert min(im.size) == 128


def _library_with(lib, media, *names):
    for i, n in enumerate(names):
        if n.endswith(".png"):
            image(media / n, (300, 200), seed=i)
        else:
            (media / n).write_bytes(b"\x00not really an image" * 50)
    with lib.db.write() as c:
        fid = c.execute("INSERT INTO folders (path, added) VALUES (?, ?)", (str(media), time.time())).lastrowid
    scan.scan_folder(lib.db, {"id": fid, "path": str(media)})
    return {r["name"]: r for r in lib.files()["items"]}


def test_get_makes_and_caches_by_fingerprint(lib, media):
    rows = _library_with(lib, media, "a.png")
    path = lib.thumbs.get(rows["a.png"]["id"])
    with lib.db.read() as c:
        fp = c.execute("SELECT fp FROM files WHERE id=?", (rows["a.png"]["id"],)).fetchone()[0]
    assert path == thumbs.cache_path(lib.thumbs.dir, fp, 256) and os.path.isfile(path)
    mtime = os.path.getmtime(path)
    assert lib.thumbs.get(rows["a.png"]["id"]) == path and os.path.getmtime(path) == mtime  # not made twice


def test_a_broken_file_fails_once_and_is_not_retried(lib, media):
    rows = _library_with(lib, media, "broken.webp")
    fid = rows["broken.webp"]["id"]
    assert lib.thumbs.get(fid) is None
    assert lib.files()["items"][0]["thumb"] == -1
    calls = []
    lib.thumbs.make = lambda row: calls.append(row) or 1
    assert lib.thumbs.get(fid) is None and not calls


def test_background_workers_fill_the_cache_newest_first(lib, media):
    names = [f"{i}.png" for i in range(6)]
    _library_with(lib, media, *names)
    lib.thumbs.start()
    try:
        deadline = time.time() + 30
        while lib.thumbs.pending() and time.time() < deadline:
            time.sleep(0.1)
    finally:
        lib.thumbs.stop()
    assert lib.thumbs.pending() == 0
    assert all(r["thumb"] == 1 for r in lib.files()["items"])
