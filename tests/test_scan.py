"""The scanner: what it finds, skips and remembers as files change, move and go missing."""
import os
import shutil
import time

from conftest import image
from mlgallery import scan


def files(lib, **kw):
    return {r["rel"]: r for r in lib.files(limit=1000, **kw)["items"]}


def scan_now(lib, fid):
    folder = next(f for f in lib.folders() if f["id"] == fid)
    return scan.scan_folder(lib.db, folder)


def add(lib, path):
    """Add a folder without the background queue, then scan it here."""
    with lib.db.write() as c:
        fid = c.execute("INSERT INTO folders (path, added) VALUES (?, ?)", (str(path), time.time())).lastrowid
    return fid, scan_now(lib, fid)


def test_fingerprint_same_file_same_print(media):
    a = image(media / "a.png", (300, 300), 1)
    b = str(media / "b.png")
    shutil.copy(a, b)
    c = image(media / "c.png", (300, 300), 2)
    fp = lambda p: scan.fingerprint(p, os.path.getsize(p))  # noqa: E731
    assert fp(a) == fp(b)
    assert fp(a) != fp(c)


def test_fingerprint_reads_the_tail_of_big_files(media):
    p = media / "big.bin"
    p.write_bytes(b"x" * 300_000)
    before = scan.fingerprint(str(p), 300_000)
    with open(p, "r+b") as f:  # only the last bytes change
        f.seek(-1, os.SEEK_END)
        f.write(b"y")
    assert scan.fingerprint(str(p), 300_000) != before


def test_finds_media_and_skips_hidden_system_and_eagle(lib, media):
    image(media / "one.png")
    image(media / "sub" / "two.jpg")
    image(media / ".hidden" / "no.png")
    image(media / "$RECYCLE.BIN" / "no.png")
    image(media / "@eaDir" / "no.png")
    image(media / "Mine.library" / "images" / "X.info" / "no.png")
    (media / "notes.txt").write_text("not media")
    fid, counts = add(lib, media)
    assert counts["added"] == 2
    assert set(files(lib)) == {"one.png", "sub/two.jpg"}


def test_image_size_follows_exif_orientation(media):
    from PIL import Image
    p = str(media / "turned.jpg")
    im = Image.new("RGB", (40, 20), "red")
    exif = Image.Exif()
    exif[0x0112] = 6  # shown turned 90°
    im.save(p, exif=exif)
    assert scan.image_size(p) == (20, 40)


def test_rescan_unchanged_changed_missing_back(lib, media):
    a = image(media / "a.png", seed=1)
    image(media / "b.png", seed=2)
    fid, counts = add(lib, media)
    assert counts["added"] == 2

    counts = scan_now(lib, fid)
    assert counts["unchanged"] == 2 and counts["added"] == 0

    image(a, (80, 60), seed=3)  # rewritten: new size, new mtime
    os.utime(a, (time.time() + 5, time.time() + 5))
    counts = scan_now(lib, fid)
    assert counts["changed"] == 1
    assert files(lib)["a.png"]["width"] == 80

    gone = media / "b.png"
    kept = media.parent / "b.keep"
    shutil.move(str(gone), str(kept))
    counts = scan_now(lib, fid)
    assert counts["missing"] == 1
    assert "b.png" not in files(lib)

    shutil.move(str(kept), str(gone))
    counts = scan_now(lib, fid)
    assert "b.png" in files(lib)


def test_a_moved_file_keeps_its_record(lib, media):
    p = image(media / "old_name.png", seed=4)
    fid, _ = add(lib, media)
    before = files(lib)["old_name.png"]["id"]
    os.makedirs(media / "sorted", exist_ok=True)
    shutil.move(p, str(media / "sorted" / "new_name.png"))
    counts = scan_now(lib, fid)
    assert counts["moved"] == 1 and counts["added"] == 0 and counts["missing"] == 0
    assert files(lib)["sorted/new_name.png"]["id"] == before


def test_an_offline_folder_keeps_its_records(lib, media, tmp_path):
    image(media / "a.png")
    fid, _ = add(lib, media)
    away = tmp_path / "away"
    shutil.move(str(media), str(away))  # like a drive unplugged
    counts = scan_now(lib, fid)
    assert counts["offline"]
    assert lib.files()["total"] == 0  # not listed while offline...
    shutil.move(str(away), str(media))
    scan_now(lib, fid)
    assert lib.files()["total"] == 1  # ...and back, same record


def test_a_stopped_scan_marks_nothing_missing(lib, media):
    for i in range(5):
        image(media / f"{i}.png", seed=i)
    fid, _ = add(lib, media)
    for i in range(5):
        os.remove(media / f"{i}.png")
    folder = next(f for f in lib.folders() if f["id"] == fid)
    counts = scan.scan_folder(lib.db, folder, stop=lambda: True)
    assert counts["missing"] == 0
    assert lib.files()["total"] == 5


def test_overlapping_folders(tmp_path):
    a, b = str(tmp_path / "a"), str(tmp_path / "a" / "b")
    assert scan.overlapping([a], b) == a
    assert scan.overlapping([b], a) == b
    assert scan.overlapping([a], str(tmp_path / "ab")) is None  # a prefix of the name isn't inside it


def test_add_folder_refuses_eagle_overlaps_and_missing(lib, media):
    import pytest
    eagle = media / "Art.library"
    eagle.mkdir()
    with pytest.raises(ValueError, match="Eagle"):
        lib.add_folder(str(eagle))
    with pytest.raises(ValueError, match="exist"):
        lib.add_folder(str(media / "nope"))
    lib.add_folder(str(media))
    with pytest.raises(ValueError, match="overlaps"):
        lib.add_folder(str(media / "Art.library" / ".."))  # the same folder, spelt differently
    sub = media / "sub"
    sub.mkdir()
    with pytest.raises(ValueError, match="overlaps"):
        lib.add_folder(str(sub))


def test_remove_folder_forgets_only_the_index(lib, media):
    p = image(media / "a.png")
    fid, _ = add(lib, media)
    assert lib.remove_folder(fid)
    assert lib.files()["total"] == 0
    assert os.path.isfile(p)  # the file itself is untouched


def test_sorts_paging_days_and_random(lib, media):
    now = time.time()
    for i in range(10):
        p = image(media / f"f{i}.png", seed=i)
        t = now - i * 86400  # one a day
        os.utime(p, (t, t))
    add(lib, media)
    newest = [r["name"] for r in lib.files(sort="newest")["items"]]
    assert newest[:3] == ["f0.png", "f1.png", "f2.png"]
    assert [r["name"] for r in lib.files(sort="oldest")["items"]][0] == "f9.png"
    page = lib.files(offset=4, limit=3)
    assert page["total"] == 10 and [r["name"] for r in page["items"]] == newest[4:7]
    days = lib.days("newest")
    assert sum(d["n"] for d in days) == 10 and days == sorted(days, key=lambda d: d["day"], reverse=True)
    assert lib.days("random") == []
    r1 = [r["id"] for r in lib.files(sort="random", seed=11)["items"]]
    r2 = [r["id"] for r in lib.files(sort="random", seed=11)["items"]]
    r3 = [r["id"] for r in lib.files(sort="random", seed=12)["items"]]
    assert r1 == r2 and sorted(r1) == sorted(r3) and r1 != r3
    # pages of a shuffle don't repeat or skip anything
    paged = lib.files(sort="random", seed=11, limit=4)["items"] + lib.files(sort="random", seed=11, offset=4, limit=6)["items"]
    assert [r["id"] for r in paged] == r1


def test_kind_filter(lib, sample_dir):
    add(lib, sample_dir)
    every = lib.files()["total"]
    images = lib.files(kind="image")["total"]
    videos = lib.files(kind="video")["total"]
    assert images + videos == every and images == 24
