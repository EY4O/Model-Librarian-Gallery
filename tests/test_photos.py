"""Photos: the date a picture was taken, iPhone HEIC and other formats, Live Photos, and what a library is for.

On generated pictures and made-up phone files only.
"""
import asyncio
import os
import shutil
import subprocess
import time

import pytest
from PIL import Image

from conftest import HAVE_FFMPEG, image
from mlgallery import dates, fileops, scan

T2020 = time.mktime((2020, 1, 1, 12, 0, 0, 0, 0, -1))
T2021 = time.mktime((2021, 7, 4, 9, 30, 15, 0, 0, -1))


def photo(path, fmt="JPEG", taken=None, size=(96, 64), color=(200, 90, 40)):
    """A made-up camera picture, with an EXIF date taken when asked for."""
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    im = Image.new("RGB", size, color)
    kw = {}
    if taken:
        exif = Image.Exif()
        exif[0x8769] = {0x9003: time.strftime("%Y:%m:%d %H:%M:%S", time.localtime(taken))}
        kw["exif"] = exif.tobytes()
    im.save(str(path), fmt, **kw)
    return str(path)


def folder(lib, path):
    with lib.db.write() as c:
        fid = c.execute("INSERT INTO folders (path, added) VALUES (?, ?)", (str(path), time.time())).lastrowid
    return {"id": fid, "path": str(path)}


def items(lib, **kw):
    return {r["name"]: r for r in lib.files(**kw)["items"]}


# ---------------------------------------------------------------- the date taken
@pytest.mark.parametrize("name,want", [
    ("IMG_20240612_143005.jpg", (2024, 6, 12, 14, 30, 5)),
    ("PXL_20230615_101530123.jpg", (2023, 6, 15, 10, 15, 30)),  # Pixel: milliseconds after the seconds
    ("VID-20240612-WA0003.mp4", (2024, 6, 12, 12, 0, 0)),  # a day alone: noon
    ("Screenshot_2024-06-12-14-30-05_com.app.png", (2024, 6, 12, 14, 30, 5)),
    ("2019-02-03 21.04.59.jpg", (2019, 2, 3, 21, 4, 59)),
])
def test_dates_in_names(name, want):
    assert dates.from_name(name) == time.mktime(want + (0, 0, -1))


@pytest.mark.parametrize("name", ["ComfyUI_00001_.png", "IMG_1234.JPG", "711588397_17968533657099524_n.jpg", "seed_20241399.png", "img_19700101.jpg",
                                  "IMG_20240231_120000.jpg"])  # no 31 February rolled over into March
def test_names_without_a_date(name):
    assert dates.from_name(name) is None


def test_a_day_alone_in_a_name_keeps_the_files_own_time_that_day():
    made = time.mktime((2024, 6, 12, 17, 45, 0, 0, 0, -1))
    assert dates.from_name("20240612_ComfyUI_00001_.png", made) is None  # modified that day: its own time says when
    assert dates.from_name("20240612_ComfyUI_00001_.png", made + 86400 * 30) == time.mktime((2024, 6, 12, 12, 0, 0, 0, 0, -1))


def test_exif_dates(tmp_path):
    assert dates.from_image(photo(tmp_path / "a.jpg", taken=T2020)) == T2020
    assert dates.from_image(photo(tmp_path / "b.heic", "HEIF", taken=T2021)) == T2021  # iPhone
    assert dates.from_image(photo(tmp_path / "c.png", "PNG")) is None
    p = tmp_path / "zero.jpg"
    exif = Image.Exif()
    exif[0x8769] = {0x9003: "0000:00:00 00:00:00"}  # a camera that was never set
    Image.new("RGB", (8, 8)).save(p, "JPEG", exif=exif.tobytes())
    assert dates.from_image(str(p)) is None
    edited = Image.Exif()
    edited[0x0132] = "2023:01:02 03:04:05"  # DateTime: when it was last edited, not taken
    Image.new("RGB", (8, 8)).save(tmp_path / "edited.jpg", "JPEG", exif=edited.tobytes())
    assert dates.from_image(str(tmp_path / "edited.jpg")) is None


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")
def test_video_recorded_date(tmp_path):
    p = str(tmp_path / "clip.mp4")
    subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=64x48:r=10", "-t", "0.5", "-pix_fmt", "yuv420p",
                    "-metadata", "creation_time=2021-07-04T07:30:15Z", p], check=True)
    assert dates.from_video(p, shutil.which("ffmpeg")) == 1625383815.0  # UTC


def test_the_grid_goes_by_the_date_taken(lib, media):
    photo(media / "new.jpg", taken=None, color=(10, 200, 10))  # modified now, no date: today
    photo(media / "IMG_20200101_120000.jpg", color=(10, 10, 200))  # copied off a phone today: its name has the day
    photo(media / "camera.jpg", taken=T2021)  # its EXIF has it
    f = folder(lib, media)
    scan.scan_folder(lib.db, f)
    got = items(lib)
    assert got["IMG_20200101_120000.jpg"]["shot"] == T2020  # straight away, from its name
    assert got["camera.jpg"]["taken"] is None  # EXIF is read in the background
    while lib.dater.run_once():
        pass
    got = items(lib)
    assert got["camera.jpg"]["taken"] == T2021 and got["camera.jpg"]["shot"] == T2021 and got["new.jpg"]["taken"] is None
    assert [i["name"] for i in lib.files()["items"]] == ["new.jpg", "camera.jpg", "IMG_20200101_120000.jpg"]  # newest taken first
    assert [d["day"] for d in lib.days()] == [time.strftime("%Y-%m-%d"), "2021-07-04", "2020-01-01"]
    assert [i["name"] for i in lib.files(q="before:2021-01-01")["items"]] == ["IMG_20200101_120000.jpg"]
    assert [i["name"] for i in lib.files(q="on:2021-07-04")["items"]] == ["camera.jpg"]
    assert lib.dater.moved == 1 and lib.dater.pending() == 0


# ---------------------------------------------------------------- formats
def test_heic_tiff_bmp_and_avif_are_indexed_with_thumbnails(lib, media):
    for name, fmt in (("IMG_0001.HEIC", "HEIF"), ("scan.tif", "TIFF"), ("old.bmp", "BMP"), ("web.avif", "AVIF")):
        photo(media / name, fmt, size=(120, 80))
    scan.scan_folder(lib.db, folder(lib, media))
    got = items(lib)
    assert set(got) == {"IMG_0001.HEIC", "scan.tif", "old.bmp", "web.avif"}
    for name, r in got.items():
        assert (r["width"], r["height"]) == (120, 80), name
        assert lib.thumbs.get(r["id"]), name
    assert scan.kind_of("trip.avi") == scan.kind_of("old.3gp") == "video"


def test_the_viewer_gets_heic_and_tiff_as_webp(lib, media):
    from aiohttp.test_utils import TestClient, TestServer
    from mlgallery.server import make_app
    photo(media / "IMG_0002.HEIC", "HEIF", size=(200, 100))
    photo(media / "plain.jpg", size=(200, 100))
    scan.scan_folder(lib.db, folder(lib, media))
    got = items(lib)

    async def go():
        client = TestClient(TestServer(make_app(lib, "t"), host="127.0.0.1"))
        await client.start_server()
        try:
            r = await client.get(f"/api/file/{got['IMG_0002.HEIC']['id']}?t=t")
            assert r.status == 200 and r.headers["Content-Type"] == "image/webp"
            body = await r.read()
            assert body[:4] == b"RIFF" and body[8:12] == b"WEBP"
            raw = await client.get(f"/api/file/{got['IMG_0002.HEIC']['id']}?t=t&raw=1")  # the original itself, when asked
            assert raw.headers["Content-Type"] != "image/webp"
            jpg = await client.get(f"/api/file/{got['plain.jpg']['id']}?t=t")
            assert jpg.headers["Content-Type"] == "image/jpeg"  # what the window shows itself is sent as it is
        finally:
            await client.close()
    asyncio.run(go())


def test_saved_to_a_phone_heic_and_tiff_are_a_full_size_jpeg(lib, media):
    import io
    from aiohttp.test_utils import TestClient, TestServer
    from mlgallery.server import make_app
    photo(media / "IMG_0003.HEIC", "HEIF", taken=T2021, size=(4000, 3000))  # (bigger than the viewer's copy)
    photo(media / "scan.tif", "TIFF", size=(300, 200))
    photo(media / "plain.png", "PNG", size=(200, 100))
    scan.scan_folder(lib.db, folder(lib, media))
    got = items(lib)

    async def go():
        client = TestClient(TestServer(make_app(lib, "t"), host="127.0.0.1"))
        await client.start_server()
        try:
            for name, size in (("IMG_0003.HEIC", (4000, 3000)), ("scan.tif", (300, 200))):
                r = await client.get(f"/api/file/{got[name]['id']}?t=t&save=1")
                assert r.status == 200 and r.headers["Content-Type"] == "image/jpeg", name
                with Image.open(io.BytesIO(await r.read())) as im:
                    assert im.format == "JPEG" and im.size == size, name
            r = await client.get(f"/api/file/{got['IMG_0003.HEIC']['id']}?t=t&save=1")
            with Image.open(io.BytesIO(await r.read())) as im:  # when it was taken goes with it (the phone's photos sort by it)
                assert im.getexif().get_ifd(0x8769).get(0x9003) == time.strftime("%Y:%m:%d %H:%M:%S", time.localtime(T2021))
            png = await client.get(f"/api/file/{got['plain.png']['id']}?t=t&save=1")  # anything else: the original, as it is
            assert png.headers["Content-Type"] == "image/png" and await png.read() == (media / "plain.png").read_bytes()
        finally:
            await client.close()
    asyncio.run(go())


def test_a_16_bit_scan_isnt_white(tmp_path):
    from mlgallery import thumbs
    p = tmp_path / "scan16.tif"
    Image.new("I;16", (64, 48), 30000).save(p, "TIFF")
    im = thumbs._flatten(Image.open(p))
    assert im.mode == "RGB" and im.getpixel((5, 5)) != (255, 255, 255)


def test_tiff_edits_and_a_crop_made_on_a_preview(lib, media):
    from mlgallery import edit
    p = photo(media / "scan.tif", "TIFF", taken=T2021, size=(400, 200))
    scan.scan_folder(lib.db, folder(lib, media))
    fid = items(lib)["scan.tif"]["id"]
    edit.apply(lib.db, fid, rotate=90)  # a quarter turn, then a crop: the TIFF's own layout tags follow the new picture
    with Image.open(p) as im:
        assert im.size == (200, 400)
    edit.apply(lib.db, fid, crop=[0, 0, 50, 100], of=[100, 200])  # measured on a half-size preview: half the real picture
    with Image.open(p) as im:
        assert im.size == (100, 200) and im.format == "TIFF"
    assert dates.from_image(p) == T2021  # its date taken stays


def test_editing_a_heic_keeps_it_a_heic(lib, media):
    from mlgallery import edit
    p = photo(media / "IMG_0003.HEIC", "HEIF", taken=T2021, size=(120, 80))
    scan.scan_folder(lib.db, folder(lib, media))
    fid = items(lib)["IMG_0003.HEIC"]["id"]
    edit.apply(lib.db, fid, rotate=90)
    with Image.open(p) as im:
        assert im.format == "HEIF" and im.size == (80, 120)
    assert dates.from_image(p) == T2021  # its date taken stays


# ---------------------------------------------------------------- Live Photos
def live(media, stem="IMG_0010", sub=""):
    d = media / sub if sub else media
    photo(d / f"{stem}.HEIC", "HEIF", size=(90, 60))
    (d / f"{stem}.MOV").write_bytes(b"\x00\x00\x00\x18ftypqt  " + os.urandom(2048))  # stands in for the short video
    return d


def state(lib, *ids):
    with lib.db.read() as c:
        return [tuple(c.execute("SELECT missing, motion FROM files WHERE id=?", (i,)).fetchone()) for i in ids]


def test_a_live_photo_is_one_item(lib, media):
    lib.set_kind("photos")
    live(media)
    photo(media / "other.jpg")
    f = folder(lib, media)
    scan.scan_folder(lib.db, f)
    got = items(lib)
    assert set(got) == {"IMG_0010.HEIC", "other.jpg"}  # the video isn't on its own
    heic = got["IMG_0010.HEIC"]
    assert heic["motion"] and lib.status()["files"] == 2
    assert lib.file_path(heic["motion"]).endswith("IMG_0010.MOV")  # so it can play on hover
    scan.scan_folder(lib.db, f)  # a rescan leaves the pair as it is
    assert items(lib)["IMG_0010.HEIC"]["motion"] == heic["motion"] and lib.status()["files"] == 2
    os.remove(media / "IMG_0010.HEIC")  # the photo deleted by hand: the video shows on its own
    scan.scan_folder(lib.db, f)
    assert "IMG_0010.MOV" in items(lib)


def test_a_live_photo_goes_to_the_trash_and_back_whole(lib, media, monkeypatch):
    lib.set_kind("photos")
    live(media)
    scan.scan_folder(lib.db, folder(lib, media))
    heic = items(lib)["IMG_0010.HEIC"]
    mov = heic["motion"]
    r = fileops.trash(lib.db, [heic["id"]])
    assert r["trashed"] == 1 and not (media / "IMG_0010.MOV").exists()
    assert [s[0] for s in state(lib, heic["id"], mov)] == [2, 2]
    assert [i["id"] for i in lib.files(sort="trashed")["items"]] == [heic["id"]] and lib.status()["trash"] == 1  # one item in the Trash
    fileops.undo(lib.db, r["op"])
    assert state(lib, heic["id"], mov) == [(0, mov), (scan.PAIRED, None)] and (media / "IMG_0010.MOV").exists()
    fileops.trash(lib.db, [heic["id"]])
    import send2trash
    sent = []
    monkeypatch.setattr(send2trash, "send2trash", lambda p: (sent.append(p), os.remove(p)))  # never your Recycle Bin
    assert fileops.empty(lib.db, [heic["id"]])["emptied"] == 1  # one Live Photo, both files
    assert sorted(os.path.basename(p) for p in sent) == ["IMG_0010.HEIC", "IMG_0010.MOV"]


def test_a_live_photo_moves_whole_and_keeps_its_pair_through_a_rename(lib, media):
    lib.set_kind("photos")
    live(media)
    live(media, sub="Holiday")  # IMG_0010 is taken there already
    f = folder(lib, media)
    scan.scan_folder(lib.db, f)
    heic = next(i for i in lib.files()["items"] if i["rel"] == "IMG_0010.HEIC")
    plan = fileops.plan_move(lib.db, [heic["id"]], f["id"], "Holiday")
    assert plan["count"] == 1 and plan["renamed"] == 1 and len(plan["items"]) == 2
    assert sorted(it["to_rel"] for it in plan["items"]) == ["Holiday/IMG_0010 (2).HEIC", "Holiday/IMG_0010 (2).MOV"]
    r = fileops.move(lib.db, [heic["id"]], f["id"], "Holiday")
    assert r["moved"] == 1 and (media / "Holiday" / "IMG_0010 (2).MOV").exists()  # one Live Photo
    moved = next(i for i in lib.files()["items"] if i["id"] == heic["id"])
    assert moved["rel"] == "Holiday/IMG_0010 (2).HEIC" and moved["motion"] == heic["motion"]  # still a pair
    assert lib.status()["files"] == 2
    fileops.undo(lib.db, r["op"])
    assert next(i for i in lib.files()["items"] if i["id"] == heic["id"])["rel"] == "IMG_0010.HEIC" and (media / "IMG_0010.MOV").exists()


def test_a_mov_alone_or_beside_a_png_is_just_a_video(lib, media):
    lib.set_kind("photos")
    image(media / "clip.png")
    (media / "clip.MOV").write_bytes(os.urandom(1024))
    (media / "lonely.mov").write_bytes(os.urandom(1024))
    photo(media / "film.jpg")
    with open(media / "film.mov", "wb") as fh:  # too big to be a Live Photo's few seconds: a video of its own
        fh.truncate(scan.LIVE_MAX + 1)
    scan.scan_folder(lib.db, folder(lib, media))
    assert set(items(lib)) == {"clip.png", "clip.MOV", "lonely.mov", "film.jpg", "film.mov"}


def test_live_photos_only_in_a_photo_library(lib, media):
    live(media)
    scan.scan_folder(lib.db, folder(lib, media))
    assert set(items(lib)) == {"IMG_0010.HEIC", "IMG_0010.MOV"}  # not said yet, or AI generations: a video beside a picture
    lib.set_kind("photos")
    assert set(items(lib)) == {"IMG_0010.HEIC"} and items(lib)["IMG_0010.HEIC"]["motion"] and lib.status()["files"] == 1
    lib.set_kind("illustrations")
    assert set(items(lib)) == {"IMG_0010.HEIC", "IMG_0010.MOV"} and lib.status()["files"] == 2


def test_a_live_photo_keeps_its_pair_when_a_jpeg_of_it_appears(lib, media):
    lib.set_kind("photos")
    live(media, "IMG_7")
    f = folder(lib, media)
    scan.scan_folder(lib.db, f)
    mov = items(lib)["IMG_7.HEIC"]["motion"]
    photo(media / "IMG_7.JPG")  # exported beside it
    scan.scan_folder(lib.db, f)
    got = items(lib)
    assert got["IMG_7.HEIC"]["motion"] == mov and got["IMG_7.JPG"]["motion"] is None


def test_moving_a_live_photo_where_its_name_is_taken(lib, media):
    lib.set_kind("photos")
    live(media, "IMG_3")
    old = media / "Old"
    old.mkdir()
    photo(old / "IMG_3.JPG")
    (old / "IMG_3.MOV").write_bytes(os.urandom(1500))  # another Live Photo of that number, there already
    f = folder(lib, media)
    scan.scan_folder(lib.db, f)
    there = next(i for i in lib.files()["items"] if i["rel"] == "Old/IMG_3.JPG")
    heic = next(i for i in lib.files()["items"] if i["rel"] == "IMG_3.HEIC")
    plan = fileops.plan_move(lib.db, [heic["id"]], f["id"], "Old")
    assert sorted(it["to_rel"] for it in plan["items"]) == ["Old/IMG_3 (2).HEIC", "Old/IMG_3 (2).MOV"]  # one name, free for both
    fileops.move(lib.db, [heic["id"]], f["id"], "Old")
    got = {i["rel"]: i for i in lib.files()["items"]}
    assert set(got) == {"Old/IMG_3.JPG", "Old/IMG_3 (2).HEIC"}
    assert got["Old/IMG_3.JPG"]["motion"] == there["motion"] and got["Old/IMG_3 (2).HEIC"]["motion"] == heic["motion"]


def test_a_live_photo_never_comes_apart_when_a_half_cant_go(lib, media, monkeypatch):
    lib.set_kind("photos")
    live(media, "IMG_4")
    dest = media / "Dest"
    dest.mkdir()
    f = folder(lib, media)
    scan.scan_folder(lib.db, f)
    heic = items(lib)["IMG_4.HEIC"]
    real_move = fileops._move_file

    def busy_photo(src, dst):
        if src.endswith(".HEIC"):
            raise OSError("in use")
        real_move(src, dst)
    monkeypatch.setattr(fileops, "_move_file", busy_photo)
    r = fileops.move(lib.db, [heic["id"]], f["id"], "Dest")
    assert r["moved"] == 0 and [x["id"] for x in r["failed"]] == [heic["id"]]
    assert (media / "IMG_4.MOV").exists() and not (dest / "IMG_4.MOV").exists()  # its video didn't go alone
    monkeypatch.setattr(fileops, "_move_file", real_move)
    real_rename = os.rename

    def busy_video(src, dst):
        if src.endswith(".MOV"):
            raise OSError("in use")
        real_rename(src, dst)
    monkeypatch.setattr(os, "rename", busy_video)
    r = fileops.trash(lib.db, [heic["id"]])
    assert r["trashed"] == 0 and [x["id"] for x in r["failed"]] == [heic["id"]]
    assert (media / "IMG_4.HEIC").exists() and set(items(lib)) == {"IMG_4.HEIC"}  # the photo stays, still a Live Photo


# ---------------------------------------------------------------- what the library is for
def test_library_kinds(lib):
    assert lib.kinds is None and lib.status()["kinds"] is None  # not said yet: the Gallery asks
    assert lib.set_kinds(["photos"]) == ["photos"] and lib.kinds == ["photos"]
    assert lib.set_kinds(["illustrations", "ai"]) == ["ai", "illustrations"]  # one or more, kept in one order
    assert lib.status()["kinds"] == ["ai", "illustrations"]
    for bad in ([], ["music"], ["ai", "music"]):
        with pytest.raises(ValueError):
            lib.set_kinds(bad)


def test_library_kinds_route(lib):
    from aiohttp.test_utils import TestClient, TestServer
    from mlgallery.server import make_app

    async def go():
        client = TestClient(TestServer(make_app(lib, "t"), host="127.0.0.1"))
        await client.start_server()
        try:
            H = {"X-MLG-Token": "t"}
            assert (await (await client.get("/api/status", headers=H)).json())["kinds"] is None
            assert (await client.post("/api/library/kind", json={"kinds": ["nope"]}, headers=H)).status == 422
            assert (await client.post("/api/library/kind", json={"kinds": []}, headers=H)).status == 422
            r = await client.post("/api/library/kind", json={"kinds": ["photos", "ai"]}, headers=H)
            assert (await r.json()) == {"kinds": ["ai", "photos"]}
            assert (await (await client.get("/api/status", headers=H)).json())["kinds"] == ["ai", "photos"]
            assert (await (await client.post("/api/library/kind", json={"kind": "illustrations"}, headers=H)).json()) == {"kinds": ["illustrations"]}
        finally:
            await client.close()
    asyncio.run(go())


def test_what_each_file_is_in_a_mixed_library(lib, media):
    from conftest import image as gen_image  # noqa: F401
    from sample_media import generation, make_image
    import random
    lib.set_kinds(["ai", "photos", "illustrations"])
    make_image(str(media / "gen.png"), (64, 48), 3, generation(3, random.Random(3), (64, 48)))  # made in ComfyUI: generation data
    photo(media / "camera.jpg", taken=T2021)  # a camera's
    photo(media / "IMG_20200101_120000.jpg")  # a phone's, by its name
    photo(media / "drawing.png", "PNG")  # neither: an illustration
    scan.scan_folder(lib.db, folder(lib, media))
    while lib.meta.pending():
        lib.meta.read_one(next(iter(_meta_rows(lib))))
    while lib.dater.run_once():
        pass
    names = lambda q: sorted(i["name"] for i in lib.files(q=q)["items"])  # noqa: E731
    assert names("is:ai") == ["gen.png"]
    assert names("is:photo") == ["IMG_20200101_120000.jpg", "camera.jpg"]
    assert names("is:illustration") == ["drawing.png"]
    from mlgallery import stats
    t = stats.overview(lib, 0, None)["totals"]
    assert (t["with_data"], t["photos"], t["illustrations"]) == (1, 2, 1)
    # Statistics for some kinds only: each count, the timeline and the folders keep to them; all of them is every file
    one = stats.overview(lib, 0, None, fresh=True, parts=["photo"])
    assert (one["totals"]["n"], one["totals"]["photos"], one["totals"]["with_data"], one["parts"]) == (2, 2, 0, ["photo"])
    assert sum(p["images"] for p in one["timeline"]["points"]) == 2 and one["folders"][0]["n"] == 2
    two = stats.overview(lib, 0, None, fresh=True, parts=["illustration", "ai"])
    assert (two["totals"]["n"], two["totals"]["with_data"], two["totals"]["illustrations"], two["parts"]) == (2, 1, 1, ["ai", "illustration"])
    assert stats.overview(lib, 0, None, fresh=True, parts=["ai", "photo", "illustration"])["totals"]["n"] == 4 == stats.overview(lib, 0, None, fresh=True, parts=["nonsense"])["totals"]["n"]


def _meta_rows(lib):
    with lib.db.read() as c:
        return c.execute("SELECT f.id, f.rel, f.kind, d.path FROM files f JOIN folders d ON d.id=f.folder_id WHERE f.meta=0").fetchall()


def test_live_photos_in_a_library_for_photos_and_more(lib, media):
    lib.set_kinds(["ai", "photos"])
    live(media)
    scan.scan_folder(lib.db, folder(lib, media))
    assert set(items(lib)) == {"IMG_0010.HEIC"}


def test_people_and_characters(lib):
    lib.set_kinds(["photos"])
    gran = lib.character_save(None, "Gran")
    assert lib.character(gran)["who"] == "person"  # a photo library: people
    lib.set_kinds(["photos", "illustrations"])
    aurelia = lib.character_save(None, "Aurelia")
    assert lib.character(aurelia)["who"] == "character"  # more than photos: a character unless it's said
    lib.character_save(aurelia, "Aurelia", who="person")
    assert {c["name"]: c["who"] for c in lib.characters()["characters"]} == {"Gran": "person", "Aurelia": "person"}
    with pytest.raises(ValueError):
        lib.character_save(None, "Nobody", who="robot")


def test_people_and_characters_routes(lib):
    from aiohttp.test_utils import TestClient, TestServer
    from mlgallery.server import make_app
    lib.set_kinds(["photos", "ai"])

    async def go():
        client = TestClient(TestServer(make_app(lib, "t"), host="127.0.0.1"))
        await client.start_server()
        try:
            H = {"X-MLG-Token": "t"}
            c = await (await client.post("/api/characters", json={"name": "Mum", "who": "person"}, headers=H)).json()
            assert c["who"] == "person"
            c = await (await client.post(f"/api/characters/{c['id']}", json={"who": "character"}, headers=H)).json()
            assert c["who"] == "character" and c["name"] == "Mum"
            assert (await client.post("/api/characters", json={"name": "X", "who": "robot"}, headers=H)).status == 422
        finally:
            await client.close()
    asyncio.run(go())


# ---------------------------------------------------------------- from the second QA pass
def test_undoing_a_trashed_live_photo_brings_the_pair_back_together(lib, media):
    lib.set_kinds(["photos"])
    live(media)
    scan.scan_folder(lib.db, folder(lib, media))
    heic = items(lib)["IMG_0010.HEIC"]
    r = fileops.trash(lib.db, [heic["id"]])
    photo(media / "IMG_0010.HEIC", "HEIF")  # a new still takes its name meanwhile
    result = fileops.undo(lib.db, r["op"])
    assert result["undone"] == 1  # one Live Photo
    assert (media / "IMG_0010 (2).HEIC").exists() and (media / "IMG_0010 (2).MOV").exists()  # under one free name, together
    back = next(i for i in lib.files()["items"] if i["id"] == heic["id"])
    assert back["rel"] == "IMG_0010 (2).HEIC" and back["motion"] == heic["motion"]


def test_a_png_isnt_decoded_to_look_for_a_date(tmp_path, monkeypatch):
    from PIL import PngImagePlugin
    p = tmp_path / "gen.png"
    Image.new("RGB", (64, 64)).save(p)

    def no(*a, **k):
        raise AssertionError("decoded")
    monkeypatch.setattr(PngImagePlugin.PngImageFile, "load", no)
    assert dates.from_image(str(p)) is None


@pytest.mark.parametrize("name", ["IMG_20240612_0001.jpg", "Scan_2024-06-12_0130.png", "20240612_1234.jpg"])
def test_a_counter_after_a_date_isnt_a_time(name):
    assert dates.from_name(name) == time.mktime((2024, 6, 12, 12, 0, 0, 0, 0, -1))  # the day alone


def test_one_live_photo_to_another_drive_counts_once(lib, media, monkeypatch):
    lib.set_kinds(["photos"])
    live(media)
    (media / "Away").mkdir()
    f = folder(lib, media)
    scan.scan_folder(lib.db, f)
    monkeypatch.setattr(fileops, "_same_drive", lambda a, b: False)
    plan = fileops.plan_move(lib.db, [items(lib)["IMG_0010.HEIC"]["id"]], f["id"], "Away")
    assert plan["count"] == 1 and plan["copies"] == 1 and len(plan["items"]) == 2


def test_people_stay_people_when_marks_are_brought(tmp_path):
    from mlgallery import libraries
    from mlgallery.library import Library
    src = Library(str(tmp_path / "src"))
    src.set_kinds(["photos"])
    src.character_save(None, "Gran")
    dst = Library(str(tmp_path / "dst"))
    libraries.bring_marks(dst.db, os.path.join(str(tmp_path / "src"), "library.db"), "src")
    assert {c["name"]: c["who"] for c in dst.characters()["characters"]} == {"Gran": "person"}


def test_the_watcher_seeing_a_file_go_to_the_trash_leaves_it_in_the_trash(lib, media, monkeypatch):
    from contextlib import contextmanager
    image(media / "a.png", seed=1)
    image(media / "b.png", seed=2)
    f = folder(lib, media)
    scan.scan_folder(lib.db, f)
    fid = items(lib)["a.png"]["id"]
    read, trashed = lib.db.read, []

    @contextmanager
    def then_trash():  # the watcher has looked a.png up (still there) when it goes to the Trash
        with read() as c:
            yield c
        if not trashed:
            trashed.append(fid)
            fileops.trash(lib.db, [fid])
    monkeypatch.setattr(lib.db, "read", then_trash)
    scan.index_files(lib.db, f, ["a.png"])
    monkeypatch.undo()
    assert trashed and lib.files(sort="trashed")["total"] == 1
    fileops.restore(lib.db, [fid])
    assert lib.files()["total"] == 2


def test_two_indexers_finding_one_new_file_at_once(lib, media, monkeypatch):
    # an import (or a rescan) and the folder watcher both index a file just copied in: the second leaves it be
    from contextlib import contextmanager
    image(media / "a.png", seed=1)
    f = folder(lib, media)
    read, other = lib.db.read, []

    @contextmanager
    def then_the_watcher():
        with read() as c:
            yield c
        if not other:
            other.append(True)
            monkeypatch.setattr(lib.db, "read", read)
            scan.index_files(lib.db, f, ["a.png"])  # the watcher, in between
    monkeypatch.setattr(lib.db, "read", then_the_watcher)
    scan.index_files(lib.db, f, ["a.png"])  # used to fail: UNIQUE constraint failed: files.folder_id, files.rel
    with lib.db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM search").fetchone()[0] == 1


def test_a_rescan_doesnt_take_a_file_put_back_meanwhile_for_gone(lib, media, monkeypatch):
    # the walk passes while it's in the Trash; it's back before the rescan ends: still there, not missing
    image(media / "a.png", seed=1)
    image(media / "b.png", seed=2)
    f = folder(lib, media)
    scan.scan_folder(lib.db, f)
    walk = scan.walk
    monkeypatch.setattr(scan, "walk", lambda root, skip_eagle=True: (x for x in walk(root, skip_eagle) if not x[1].endswith("a.png")))
    scan.scan_folder(lib.db, f)
    with lib.db.read() as c:
        assert c.execute("SELECT missing FROM files WHERE name='a.png'").fetchone()[0] == 0
    # one in a folder the walk leaves out (an Eagle library) is gone from the library, as before
    lib2_media = media / "Art.library"
    image(lib2_media / "c.png", seed=3)
    monkeypatch.setattr(scan, "walk", walk)
    scan.scan_folder(lib.db, f, skip_eagle=False)
    scan.scan_folder(lib.db, f, skip_eagle=True)
    with lib.db.read() as c:
        assert c.execute("SELECT missing FROM files WHERE name='c.png'").fetchone()[0] == 1
