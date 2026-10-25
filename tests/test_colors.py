"""Colour sets: a picture's main colours worked out from its thumbnail, kept, found by colour, and counted in Statistics."""
import os
import time

import pytest
from PIL import Image

from mlgallery import colors, scan, stats


def flat(path, parts, size=(120, 120)):
    """An image of solid bands: [(rgb, share of the width)]."""
    im = Image.new("RGB", size)
    x = 0
    for rgb, share in parts:
        w = round(size[0] * share)
        im.paste(rgb, (x, 0, min(size[0], x + w), size[1]))
        x += w
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    im.save(str(path))
    return str(path)


def indexed(lib, media):
    with lib.db.write() as c:
        fid = c.execute("INSERT INTO folders (path, added) VALUES (?, ?)", (str(media), time.time())).lastrowid
    scan.scan_folder(lib.db, {"id": fid, "path": str(media)})
    with lib.db.read() as c:
        return {r["name"]: r["id"] for r in c.execute("SELECT id, name FROM files")}


def make_thumbs(lib):
    rows = lib.thumbs._next(100)
    lib.thumbs._record([(lib.thumbs.make(r), r["id"]) for r in rows])
    lib.thumbs._claimed.clear()  # as the background worker does once it's done with them


# ---------------------------------------------------------------- working them out
def test_palette_of_a_two_colour_picture(tmp_path):
    with Image.open(flat(tmp_path / "rb.png", [((220, 30, 40), 0.7), ((30, 60, 220), 0.3)])) as im:
        pal = colors.palette(im)
    assert len(pal) == 2
    (h1, r1), (h2, r2) = pal
    assert colors.family(colors.rgb_of(h1)) == "red" and colors.family(colors.rgb_of(h2)) == "blue"
    assert abs(r1 - 0.7) < 0.05 and abs(r2 - 0.3) < 0.05


def test_near_twins_are_one_colour(tmp_path):
    with Image.open(flat(tmp_path / "t.png", [((200, 40, 40), 0.5), ((204, 42, 41), 0.5)])) as im:
        assert len(colors.palette(im)) == 1


@pytest.mark.parametrize("rgb,name", [((0, 0, 0), "black"), ((255, 255, 255), "white"), ((128, 128, 128), "gray"), ((230, 30, 30), "red"),
                                      ((250, 150, 20), "orange"), ((240, 220, 40), "yellow"), ((40, 180, 60), "green"), ((30, 200, 210), "cyan"),
                                      ((30, 80, 230), "blue"), ((140, 40, 200), "purple"), ((240, 80, 170), "pink"), ((120, 70, 30), "brown"),
                                      ((255, 192, 203), "pink"), ((200, 30, 50), "red")])
def test_families(rgb, name):
    assert colors.family(rgb) == name


def test_hex_and_lab():
    assert colors.rgb_of("#140D17") == (20, 13, 23) and colors.rgb_of("fff") == (255, 255, 255) and colors.rgb_of("nope") is None
    assert colors.hex_of((20, 13, 23)) == "#140d17"
    assert colors.lab((255, 255, 255))[0] == pytest.approx(100, abs=0.1) and colors.lab((0, 0, 0))[0] == pytest.approx(0, abs=0.1)


def test_eagles_palette():
    pal = colors.from_eagle([{"color": [20, 13, 23], "ratio": 50, "$$hashKey": "x"}, {"color": [226, 209, 205], "ratio": 13}, {"color": [1, 2, 3], "ratio": 0.5}, {"bad": 1}])
    assert pal == [("#140d17", 0.5), ("#e2d1cd", 0.13)]  # under 1% isn't one of its colours


# ---------------------------------------------------------------- kept for every file, in the background
def test_colours_come_with_the_thumbnail(lib, media):
    ids = indexed(lib, media) if flat(media / "red.png", [((220, 30, 40), 1)]) and flat(media / "blue.png", [((30, 60, 220), 0.8), ((250, 250, 250), 0.2)]) else None
    make_thumbs(lib)
    info = lib.info(ids["blue.png"])
    main = [c["family"] for c in info["colors"] if c["ratio"] >= 0.05]  # (a compressed thumbnail adds a few faint edge colours)
    assert main == ["blue", "white"] and abs(info["colors"][0]["ratio"] - 0.8) < 0.06
    with lib.db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM files WHERE colors=1").fetchone()[0] == 2


def test_backfill_and_a_changed_file(lib, media):
    p = flat(media / "a.png", [((220, 30, 40), 1)])
    ids = indexed(lib, media)
    make_thumbs(lib)
    with lib.db.write() as c:  # as a library from before colour sets: thumbnails, no colours
        c.execute("DELETE FROM colors")
        c.execute("UPDATE files SET colors=0")
    lib.thumbs.backfill_fingerprints()
    assert lib.info(ids["a.png"])["colors"][0]["family"] == "red"
    time.sleep(0.05)
    flat(p, [((30, 180, 60), 1)], size=(130, 120))  # the picture changes: its colours are worked out again
    scan.scan_folder(lib.db, lib.folders()[0])
    with lib.db.read() as c:
        assert c.execute("SELECT colors FROM files WHERE id=?", (ids["a.png"],)).fetchone()[0] == 0
    make_thumbs(lib)
    assert lib.info(ids["a.png"])["colors"][0]["family"] == "green"


# ---------------------------------------------------------------- finding by colour
def test_find_by_colour(lib, media):
    from test_meta import q
    flat(media / "red.png", [((220, 30, 40), 1)])
    flat(media / "mostly blue.png", [((30, 60, 220), 0.93), ((220, 30, 40), 0.07)])
    flat(media / "green.png", [((40, 180, 60), 1)])
    indexed(lib, media)
    make_thumbs(lib)
    assert q(lib, "color:red") == ["red.png"]  # a family: 10% of the picture or more
    assert q(lib, "color:#dc1e28") == ["mostly blue.png", "red.png"]  # a colour that looks alike: 5% or more
    assert q(lib, "colour:#e0241e") == ["mostly blue.png", "red.png"]  # spelt either way
    assert q(lib, "color:#00ffff") == [] and q(lib, "color:nonsense") == []
    assert q(lib, "-color:blue") == ["green.png", "red.png"]
    assert [s["name"] for s in lib.suggest("color", "b")] == ["blue", "brown", "black"]
    assert lib.suggest("color", "")[0] == {"name": "red", "swatch": colors.FAMILIES["red"]}


def test_colours_in_statistics(lib, media):
    flat(media / "red.png", [((220, 30, 40), 1)])
    flat(media / "half.png", [((220, 30, 40), 0.5), ((30, 60, 220), 0.5)])
    indexed(lib, media)
    make_thumbs(lib)
    o = stats.overview(lib, days=0, fresh=True)["colors"]
    assert [s["name"] for s in o["series"]][:2] == ["red", "blue"] and o["series"][0]["n"] == pytest.approx(75, abs=4)
    assert o["series"][0]["color"] == colors.FAMILIES["red"] and sum(s["n"] for s in o["series"]) == pytest.approx(100, abs=0.5)
    pt = o["points"][0]
    assert sum(v for k, v in pt.items() if k != "at") == pytest.approx(100, abs=0.5)
