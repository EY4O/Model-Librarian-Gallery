"""Swipe review, smart albums, renaming by a pattern, where a picture is from as its details show it, notifications."""
import os
import time

import pytest

from conftest import image
from mlgallery import fileops, rename, scan
from test_artists import downloaded, illustrations, index
from test_photos import folder, items, live

T = time.mktime((2024, 6, 12, 14, 30, 0, 0, 0, -1))


def pictures(lib, media, n=4):
    for i in range(n):
        p = image(media / f"p{i}.png", seed=i)
        os.utime(p, (T + i * 60, T + i * 60))
    f = folder(lib, media)
    scan.scan_folder(lib.db, f)
    return f


# ---------------------------------------------------------------- swipe review
def test_a_review_goes_through_what_isnt_reviewed_and_remembers(lib, media):
    pictures(lib, media)
    q = lib.sort_queue()
    assert q["left"] == 4 and q["reviewed"] == 0 and [i["name"] for i in q["items"]] == ["p3.png", "p2.png", "p1.png", "p0.png"]
    ids = [i["id"] for i in q["items"]]
    lib.sort_decide(ids[0], "keep")
    r = lib.sort_decide(ids[1], "fav")
    lib.sort_decide(ids[2], "rate", 4)
    q = lib.sort_queue()
    assert q["left"] == 1 and q["reviewed"] == 3 and q["items"][0]["id"] == ids[3]
    assert items(lib)["p2.png"]["favorite"] == 1 and items(lib)["p1.png"]["rating"] == 4
    lib.sort_undo(ids[1], r["was"])  # Z: not reviewed, not a favourite, as it was
    assert items(lib)["p2.png"]["favorite"] == 0 and lib.sort_queue()["left"] == 2
    t = fileops.trash(lib.db, [ids[3]])  # A: to the Trash, from where Z brings it back
    assert lib.sort_queue()["left"] == 1
    fileops.undo(lib.db, t["op"])
    assert lib.sort_queue()["left"] == 2
    assert lib.sort_queue(q="p1")["reviewed"] == 1  # a search: its own pictures only
    assert lib.sort_reset() == 2 and lib.sort_queue()["left"] == 4


# ---------------------------------------------------------------- smart albums
def test_a_smart_album_holds_what_its_search_finds(lib, media):
    pictures(lib, media)
    a = lib.album_save(None, "Best", query="rating:4")
    assert a["query"] == "rating:4" and a["n"] == 0
    lib.mark(items(lib)["p1.png"]["id"], rating=5)
    deadline = time.time() + 5  # (counted again behind the scenes: the last count at once, marked stale meanwhile)
    while next(x for x in lib.albums() if x["name"] == "Best")["n"] != 1 and time.time() < deadline:
        time.sleep(0.05)
    assert next(x for x in lib.albums() if x["name"] == "Best")["n"] == 1
    assert [i["name"] for i in lib.files(q="album:Best")["items"]] == ["p1.png"]
    assert lib.files(q="-album:best")["total"] == 3
    from mlgallery import bulk
    with pytest.raises(fileops.OpError):
        bulk.apply(lib.db, [items(lib)["p0.png"]["id"]], "album", "Best")  # it takes no files: its search decides
    lib.album_save(a["id"], "Best", query="album:Best")  # a search that names itself: nothing, not forever
    assert lib.files(q="album:Best")["total"] == 0
    lib.album_save(a["id"], "Best", query=None)  # an ordinary album again
    assert lib.files(q="album:Best")["total"] == 0 and next(x for x in lib.albums() if x["name"] == "Best")["query"] is None


# ---------------------------------------------------------------- renaming by a pattern
def test_renaming_by_a_pattern_and_undo(lib, media):
    illustrations(lib)
    (media / "Artists" / "Ren").mkdir(parents=True)
    for i in range(3):
        p = image(media / "Artists" / "Ren" / f"IMG_{i}.png", seed=i)
        os.utime(p, (T, T))
    image(media / "other.png", seed=9)
    f = folder(lib, media)
    index(lib, f)
    ids = [items(lib)[f"IMG_{i}.png"]["id"] for i in range(3)] + [items(lib)["other.png"]["id"]]
    p = rename.plan(lib.db, ids, "{artist}_{date}_{n}")
    assert p["count"] == 4 and [x["to"] for x in p["preview"]] == ["Ren_2024-06-12_01.png", "Ren_2024-06-12_02.png", "Ren_2024-06-12_03.png",
                                                                   f"{time.strftime('%Y-%m-%d', time.localtime(os.path.getmtime(media / 'other.png')))}_04.png"]
    r = rename.apply(lib.db, ids, "{artist}_{date}_{n}")
    assert r["renamed"] == 4 and (media / "Artists" / "Ren" / "Ren_2024-06-12_02.png").is_file() and not (media / "Artists" / "Ren" / "IMG_1.png").exists()
    assert items(lib)["Ren_2024-06-12_02.png"]["id"] == ids[1]  # the same file: its marks with it
    fileops.undo(lib.db, r["op"])
    assert (media / "Artists" / "Ren" / "IMG_1.png").is_file() and items(lib)["IMG_1.png"]["id"] == ids[1]


def test_renaming_takes_care_of_names_taken_and_live_photos(lib, media):
    lib.set_kinds(["photos"])
    live(media)
    image(media / "a.png", seed=1)
    image(media / "b.png", seed=2)
    image(media / "x.png", seed=3)
    f = folder(lib, media)
    scan.scan_folder(lib.db, f)
    got = items(lib)
    with pytest.raises(fileops.OpError):
        rename.plan(lib.db, [got["a.png"]["id"], got["b.png"]["id"]], "x")  # many files, one name: say how to number them
    rename.apply(lib.db, [got["a.png"]["id"]], "x")  # one file: a name of its own (x.png is there: x (2))
    r = rename.apply(lib.db, [got["b.png"]["id"]], "x")
    assert r["renamed"] == 1 and (media / "x (2).png").is_file() and (media / "x (3).png").is_file()
    r = rename.apply(lib.db, [got["IMG_0010.HEIC"]["id"]], "trip_{n}")
    assert (media / "trip_01.HEIC").is_file() and (media / "trip_01.MOV").is_file()  # the video with its photo
    with pytest.raises(fileops.OpError):
        rename.plan(lib.db, [got["x.png"]["id"]], "{nope}")


# ---------------------------------------------------------------- site: and has:source go by the source shown
def test_where_its_from_is_the_source_its_details_show(lib, media):
    illustrations(lib)
    a = image(media / "12345678_p0.png", seed=1)
    downloaded(a, page="https://www.deviantart.com/someone/art/Thing-42")
    image(media / "plain.png", seed=2)
    index(lib, folder(lib, media))
    fid = items(lib)["12345678_p0.png"]["id"]
    assert lib.files(q="site:deviantart")["total"] == 1 and lib.files(q="site:pixiv")["total"] == 0  # the download wins over the name
    lib.set_credit(fid, url="")  # yours: no source
    assert lib.files(q="has:source")["total"] == 0 and lib.files(q="site:deviantart")["total"] == 0
    lib.set_credit(fid, url=None)
    assert lib.files(q="has:source")["total"] == 1


# ---------------------------------------------------------------- notifications
def test_a_notification_for_downloads_that_arrive(lib, media, tmp_path, monkeypatch):
    import threading
    illustrations(lib)
    dl = tmp_path / "dl"
    dl.mkdir()
    old = image(dl / "there.png", seed=1)
    os.utime(old, (time.time() - 600,) * 2)
    lib.settings.update({"inbox_downloads": False, "inbox_notify": True})
    folder(lib, media)
    lib.inbox.add_folder(str(dl))
    told = []
    from mlgallery import winshell
    monkeypatch.setattr(winshell, "notify", lambda title, text: told.append((title, text)) or True)
    stop = threading.Event()
    t = threading.Thread(target=lib.inbox.notify_loop, args=(stop, 0.05), daemon=True)
    t.start()
    time.sleep(0.3)
    assert told == []  # what was there already isn't news
    new = image(dl / "fresh.png", seed=2)
    os.utime(new, (time.time() - 600,) * 2)
    lib.inbox._listing = None
    deadline = time.time() + 5
    while not told and time.time() < deadline:
        time.sleep(0.05)
    stop.set()
    assert told and told[0][0] == "A new download to file" and "fresh.png" in told[0][1]


# ---------------------------------------------------------------- the QA pass
def test_renaming_keeps_where_a_picture_is_from(lib, media):
    illustrations(lib)
    image(media / "12345678_p0.png", seed=1)
    image(media / "__girl_drawn_by_ren__0123456789abcdef0123456789abcdef.png", seed=2)
    index(lib, folder(lib, media))
    ids = [it["id"] for it in lib.files(sort="name")["items"]]
    rename.apply(lib.db, ids, "{artist}_{n}")
    while lib.credits.run_once():
        pass
    assert lib.files(q="site:pixiv")["total"] == 1 and lib.files(q="artist:ren")["total"] == 1  # what the old names said still counts
    assert sorted(items(lib)) == ["01.png", "ren_02.png"]


def test_leaving_a_site_out_keeps_the_pictures_without_one(lib, media):
    illustrations(lib)
    image(media / "12345678_p0.png", seed=1)
    image(media / "plain.png", seed=2)
    index(lib, folder(lib, media))
    assert lib.files(q="-site:pixiv")["total"] == 1


def test_a_pattern_keeps_your_own_separators():
    f = {"name": "a__b.png", "shot": None, "artist": None}
    assert rename.render("{name}", f, 1, 2) == "a__b"
    assert rename.render("{artist}_{n}", f, 3, 2) == "03" and rename.render("my__set_{artist}_{n}", f, 3, 2) == "my__set_03"


def test_nothing_adds_files_to_a_smart_album(lib, media):
    from mlgallery import bulk
    pictures(lib, media)
    lib.album_save(None, "Best", query="rating:4")
    with lib.db.write() as c:
        assert bulk.album_id(c, "Best") is None  # (Eagle, bringing marks, a Gallery Info node: they all go through this)
