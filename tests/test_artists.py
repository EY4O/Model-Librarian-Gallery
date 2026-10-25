"""Artists and sources: what a file, its name, its folder and its download say; yours winning; the Artists list; New downloads."""
import os
import time

import pytest
from PIL import Image, PngImagePlugin

from conftest import image
from mlgallery import credits, fileops, scan
from test_photos import folder, items

OLD = time.time() - 3600


def illustrations(lib):
    lib.set_kinds(["illustrations"])
    return lib


def index(lib, f):
    """Scan, read the metadata (none here) and look for the credits, as the background readers would."""
    scan.scan_folder(lib.db, f)
    with lib.db.read() as c:
        rows = c.execute("SELECT f.id, f.rel, f.kind, d.path FROM files f JOIN folders d ON d.id=f.folder_id WHERE f.meta=0").fetchall()
    for r in rows:
        lib.meta.read_one(r)
    while lib.credits.run_once():
        pass


def downloaded(path, page=None, picture=None):
    """As a browser leaves a download: where it came from, in its Zone.Identifier."""
    lines = ["[ZoneTransfer]", "ZoneId=3"] + ([f"ReferrerUrl={page}"] if page else []) + ([f"HostUrl={picture}"] if picture else [])
    with open(str(path) + ":Zone.Identifier", "w", encoding="utf-8") as f:
        f.write("\r\n".join(lines) + "\r\n")


def artist_of(lib, name):
    return lib.info(items(lib)[name]["id"])["credit"]


# ---------------------------------------------------------------- what names, addresses and files say
@pytest.mark.parametrize("name,want", [
    ("12345678_p0.png", {"site": "pixiv", "post": "12345678"}),
    ("98765432_p3_master1200.jpg", {"site": "pixiv", "post": "98765432"}),
    ("__hatsune_miku_vocaloid_drawn_by_mika_art__0123456789abcdef0123456789abcdef.jpg", {"site": "danbooru", "artist": "mika art"}),
    ("sunset_study_by_someartist_d1a2b3c.png", {"site": "deviantart", "artist": "someartist", "post": str(int("1a2b3c", 36))}),
    ("my_piece_by_some-one_dg1h2i3-fullview.jpg", {"site": "deviantart", "artist": "some-one"}),
    ("yande.re 123456 tag1 tag2.jpg", {"site": "yandere", "post": "123456"}),
    ("Konachan.com - 98765 sample.png", {"site": "konachan", "post": "98765"}),
    ("inkling_jp-1712345678901234567-20240405_101112-img1.jpg", {"site": "x", "artist": "inkling_jp", "post": "1712345678901234567"}),
    ("1621234567.fox_artist_campfire.png", {"site": "furaffinity", "artist": "fox"}),
])
def test_names_sites_give_their_downloads(name, want):
    got = credits.from_name(name)
    assert {k: got.get(k) for k in want} == want
    assert got.get("url", "").startswith("https://") or want["site"] == "furaffinity"


@pytest.mark.parametrize("name", ["ComfyUI_00042_.png", "IMG_20240612_143005.jpg", "sunset.png", "12345_p.png"])
def test_ordinary_names_say_nothing(name):
    assert credits.from_name(name) == {}


@pytest.mark.parametrize("url,want", [
    ("https://www.pixiv.net/en/artworks/12345678", {"site": "pixiv", "post": "12345678"}),
    ("https://x.com/inkling_jp/status/1712345678901234567", {"site": "x", "artist": "inkling_jp", "post": "1712345678901234567"}),
    ("https://twitter.com/i/status/1712345678901234567", {"site": "x", "artist": None}),
    ("https://www.deviantart.com/someartist/art/Sunset-Study-987654321", {"site": "deviantart", "artist": "someartist", "post": "987654321"}),
    ("https://bsky.app/profile/inkling.bsky.social/post/3kabc", {"site": "bluesky", "artist": "inkling", "post": "3kabc"}),
    ("https://danbooru.donmai.us/posts/7654321?q=x", {"site": "danbooru", "post": "7654321"}),
    ("https://example.com/gallery/1", {"site": "example.com"}),
])
def test_web_addresses(url, want):
    got = credits.from_url(url)
    assert {k: got.get(k) for k in want} == want


def test_names_tidied():
    assert credits.clean_name("© 2024 Jane Doe") == "Jane Doe"
    assert credits.clean_name("Jane Doe, 2023. All rights reserved.") == "Jane Doe"
    assert credits.clean_name("artist2024") == "artist2024"  # a year is only taken off when it's set apart
    assert credits.clean_name("  Camera Owner ") is None
    assert credits.clean_name("x" * 200) is None


def test_a_files_own_details(tmp_path):
    im = Image.new("RGB", (40, 30), (10, 120, 200))
    exif = Image.Exif()
    exif[0x013B] = "Jane Doe"
    im.save(tmp_path / "a.jpg", "JPEG", exif=exif.tobytes())
    info = PngImagePlugin.PngInfo()
    info.add_text("Author", "Mika")
    info.add_text("Source", "https://www.pixiv.net/artworks/555")
    im.save(tmp_path / "b.png", "PNG", pnginfo=info)
    info = PngImagePlugin.PngInfo()
    info.add_text("Source", "Stable Diffusion XL C1E1DE52")  # NovelAI's: a model, not a page
    im.save(tmp_path / "c.png", "PNG", pnginfo=info)
    assert credits.from_file(str(tmp_path / "a.jpg")) == {"artist": "Jane Doe"}
    assert credits.from_file(str(tmp_path / "b.png")) == {"artist": "Mika", "site": "pixiv", "post": "555", "url": "https://www.pixiv.net/artworks/555"}
    assert credits.from_file(str(tmp_path / "c.png")) == {}


def test_where_windows_says_it_was_downloaded_from(tmp_path):
    p = image(tmp_path / "pic.png")
    downloaded(p, page="https://www.pixiv.net/", picture="https://i.pximg.net/img-original/img/2024/01/01/00/00/00/12345678_p0.png")
    got = credits.from_download(p)
    assert got["site"] == "pixiv" and got["post"] == "12345678" and got["url"] == "https://www.pixiv.net/artworks/12345678"
    downloaded(p, page="https://x.com/inkling_jp/status/1712345678901234567", picture="https://pbs.twimg.com/media/F1a2B3c.jpg")
    assert credits.from_download(p) == {"site": "x", "post": "1712345678901234567", "artist": "inkling_jp", "url": "https://x.com/inkling_jp/status/1712345678901234567"}


# ---------------------------------------------------------------- a library for illustrations
def test_who_made_each_and_the_artists_list(lib, media):
    illustrations(lib)
    image(media / "Artists" / "Mika" / "one.png", seed=1)
    image(media / "Artists" / "Mika" / "Sketches" / "two.png", seed=2)
    image(media / "12345678_p0.png", seed=3)
    image(media / "__girl_drawn_by_mika__0123456789abcdef0123456789abcdef.png", seed=4)
    image(media / "plain.png", seed=5)
    downloaded(media / "12345678_p0.png", page="https://www.pixiv.net/artworks/12345678")
    f = folder(lib, media)
    index(lib, f)
    assert artist_of(lib, "one.png")["artist"]["name"] == "Mika" and artist_of(lib, "one.png")["artist_from"] == "folder"
    assert artist_of(lib, "two.png")["artist"]["name"] == "Mika"  # a folder inside the artist's
    danbooru = artist_of(lib, "__girl_drawn_by_mika__0123456789abcdef0123456789abcdef.png")
    assert danbooru["artist"]["name"] == "Mika" and danbooru["site"] == "danbooru"  # "mika" is the same artist, compared loosely
    pixiv = artist_of(lib, "12345678_p0.png")
    assert pixiv["artist"] is None and pixiv["site"] == "pixiv" and pixiv["source_from"] == "download"
    assert artist_of(lib, "plain.png")["artist"] is None
    listed = lib.artists()["artists"]
    assert [(a["name"], a["n"]) for a in listed] == [("Mika", 3)] and listed[0]["sites"] == ["Danbooru"]
    assert lib.files(q='artist:mika')["total"] == 3 and lib.files(q="site:pixiv")["total"] == 1
    assert lib.files(q="has:artist")["total"] == 3 and lib.files(q="no:artist")["total"] == 2 and lib.files(q="has:source")["total"] == 2
    page = lib.artist(listed[0]["id"])
    assert page["n"] == 3 and [d["sub"] for d in page["folders"]] == ["Artists/Mika", "Artists/Mika/Sketches", ""]  # a tie: named folders first


def test_the_files_own_artist_beats_its_folder_and_yours_beats_both(lib, media):
    illustrations(lib)
    im = Image.new("RGB", (40, 30), (200, 10, 10))
    exif = Image.Exif()
    exif[0x013B] = "Jane Doe"
    os.makedirs(media / "Artists" / "Someone")
    im.save(media / "Artists" / "Someone" / "a.jpg", "JPEG", exif=exif.tobytes())
    f = folder(lib, media)
    index(lib, f)
    fid = items(lib)["a.jpg"]["id"]
    got = lib.info(fid)["credit"]
    assert got["artist"]["name"] == "Jane Doe" and got["artist_from"] == "file"
    assert [r["src"] for r in got["found"]] == ["file", "folder"]
    assert lib.set_credit(fid, artist="Typed Name")["artist"]["name"] == "Typed Name"
    assert lib.set_credit(fid, artist="")["artist"] is None  # yours: no artist at all
    assert lib.set_credit(fid, artist=None)["artist"]["name"] == "Jane Doe"  # back to what was found
    assert {a["name"] for a in lib.artists()["artists"]} == {"Jane Doe"}  # Typed Name went with its last picture
    got = lib.set_credit(fid, url="https://www.deviantart.com/janedoe/art/Red-123")
    assert got["site"] == "deviantart" and got["source_from"] == "user"
    with pytest.raises(ValueError):
        lib.set_credit(fid, url="not a link")


def test_two_names_one_artist(lib, media):
    illustrations(lib)
    image(media / "Artists" / "Mika" / "one.png", seed=1)
    image(media / "inkling_jp-1712345678901234567-20240405_101112-img1.png", seed=2)
    f = folder(lib, media)
    index(lib, f)
    by = {a["name"]: a for a in lib.artists()["artists"]}
    assert set(by) == {"Mika", "inkling_jp"}
    lib.artist_save(by["Mika"]["id"], "Mika", aliases=["inkling_jp"], links=["https://x.com/inkling_jp"])
    listed = lib.artists()["artists"]
    assert [(a["name"], a["n"], a["aliases"]) for a in listed] == [("Mika", 2, ["inkling_jp"])]
    assert lib.files(q="artist:inkling_jp")["total"] == 2
    # found again later (moved, say): still Mika's
    with lib.db.write() as c:
        c.execute("UPDATE files SET credited=0")
    while lib.credits.run_once():
        pass
    assert [(a["name"], a["n"]) for a in lib.artists()["artists"]] == [("Mika", 2)]
    with pytest.raises(ValueError):
        lib.artist_save(None, "mika")  # there's one called that


def test_the_artist_of_many_at_once_and_undo(lib, media):
    illustrations(lib)
    for i in range(3):
        image(media / f"p{i}.png", seed=i)
    f = folder(lib, media)
    index(lib, f)
    ids = [it["id"] for it in lib.files()["items"]]
    from mlgallery import bulk
    r = bulk.apply(lib.db, ids, "artist", "Ren")
    assert lib.files(q="artist:ren")["total"] == 3
    fileops.undo(lib.db, r["op"])
    assert lib.files(q="has:artist")["total"] == 0 and lib.artists()["artists"] == []


def test_not_in_a_library_without_illustrations_nor_for_generations(lib, media):
    lib.set_kinds(["ai"])
    image(media / "Artists" / "Mika" / "one.png", seed=1)
    f = folder(lib, media)
    index(lib, f)
    assert lib.credits.pending() == 0 and lib.files(q="has:artist")["total"] == 0
    lib.set_kinds(["ai", "illustrations"])
    with lib.db.write() as c:
        c.execute("UPDATE files SET meta=1")  # it turns out to be a generation: you made it
    while lib.credits.run_once():
        pass
    assert lib.files(q="has:artist")["total"] == 0


def test_moving_into_an_artists_folder_names_them(lib, media):
    illustrations(lib)
    image(media / "loose.png", seed=1)
    (media / "Artists" / "Ren").mkdir(parents=True)
    f = folder(lib, media)
    index(lib, f)
    fid = items(lib)["loose.png"]["id"]
    fileops.move(lib.db, [fid], f["id"], "Artists/Ren")
    while lib.credits.run_once():
        pass
    assert lib.info(fid)["credit"]["artist"]["name"] == "Ren"


# ---------------------------------------------------------------- New downloads
@pytest.fixture
def inbox(lib, media, tmp_path):
    illustrations(lib)
    dl = tmp_path / "Downloads"
    dl.mkdir()
    lib.settings.update({"inbox_downloads": False})
    f = folder(lib, media)
    lib.inbox.add_folder(str(dl))
    return lib, f, dl


def settled(*paths):
    for p in paths:
        os.utime(p, (OLD, OLD))


def test_downloads_are_offered_with_who_made_them(inbox, media):
    lib, f, dl = inbox
    image(media / "kept.png", seed=9)
    index(lib, f)
    a = image(dl / "sunset_by_someartist_d1a2b3c.png", seed=1)
    b = image(dl / "copy.png", seed=9)  # the same as kept.png
    downloaded(a, page="https://www.deviantart.com/someartist/art/Sunset-987654321")
    (dl / "notes.txt").write_text("not a picture")
    image(dl / "still-coming.png", seed=3)  # just written: not offered yet
    settled(a, b)
    got = {it["name"]: it for it in lib.inbox.items()["items"]}
    assert set(got) == {"sunset_by_someartist_d1a2b3c.png", "copy.png"}
    s = got["sunset_by_someartist_d1a2b3c.png"]
    assert s["artist"] == "someartist" and s["site"] == "deviantart" and s["url"] == "https://www.deviantart.com/someartist/art/Sunset-987654321"
    assert s["suggest"] == {"folder": f["id"], "sub": "Artists/someartist", "why": "a folder of their own"}
    assert got["copy.png"]["have"]["id"] == items(lib)["kept.png"]["id"]
    assert lib.status()["inbox"] == 2


def test_filing_a_download_and_undoing_it(inbox, media):
    lib, f, dl = inbox
    a = image(dl / "sunset_by_someartist_d1a2b3c.png", seed=1)
    downloaded(a, page="https://www.deviantart.com/someartist/art/Sunset-987654321")
    settled(a)
    key = lib.inbox.items()["items"][0]["key"]
    r = lib.inbox.file([key], f["id"], "Artists/someartist")
    assert r["filed"] == 1 and not os.path.exists(a)
    there = media / "Artists" / "someartist" / "sunset_by_someartist_d1a2b3c.png"
    assert there.is_file()
    fid = items(lib)["sunset_by_someartist_d1a2b3c.png"]["id"]
    got = lib.info(fid)["credit"]
    assert got["artist"]["name"] == "someartist" and got["url"].endswith("Sunset-987654321")
    assert lib.inbox.items()["total"] == 0
    # the next one of theirs goes where this one went
    b = image(dl / "dawn_by_someartist_d9z8y7x.png", seed=2)
    settled(b)
    assert lib.inbox.items()["items"][0]["suggest"] == {"folder": f["id"], "sub": "Artists/someartist", "why": "where their other pictures are"}
    fileops.undo(lib.db, r["op"])
    assert os.path.isfile(a) and not there.exists() and "sunset_by_someartist_d1a2b3c.png" not in items(lib)


def test_filing_with_your_artist_and_dismissing(inbox, media):
    lib, f, dl = inbox
    a, b = image(dl / "one.png", seed=1), image(dl / "two.png", seed=2)
    settled(a, b)
    keys = {it["name"]: it["key"] for it in lib.inbox.items()["items"]}
    lib.inbox.file([keys["one.png"]], f["id"], "", artist="Ren")
    assert lib.info(items(lib)["one.png"]["id"])["credit"]["artist"]["name"] == "Ren"
    assert lib.inbox.dismiss([keys["two.png"]]) == 1 and lib.inbox.items()["total"] == 0
    assert lib.inbox.undismiss() == 1 and lib.inbox.items()["total"] == 1


def test_a_library_folder_isnt_a_downloads_folder(inbox, media):
    lib, f, dl = inbox
    with pytest.raises(ValueError):
        lib.inbox.add_folder(str(media))
    lib.inbox.remove_folder(str(dl))
    assert lib.inbox.folders() == []


def test_your_artists_come_along_with_your_other_marks(tmp_path):
    from mlgallery import libraries
    from mlgallery.library import Library
    media = tmp_path / "media"
    image(media / "a.png", seed=1)
    image(media / "b.png", seed=2)
    src = illustrations(Library(str(tmp_path / "src")))
    f = folder(src, media)
    index(src, f)
    src.set_credit(items(src)["a.png"]["id"], artist="Ren", url="https://www.pixiv.net/artworks/42")
    aid = src.artists()["artists"][0]["id"]
    src.artist_save(aid, "Ren", aliases=["ren_draws"], links=["https://x.com/ren_draws"])
    dst = illustrations(Library(str(tmp_path / "dst")))
    index(dst, folder(dst, media))
    r = libraries.bring_marks(dst.db, os.path.join(str(tmp_path / "src"), "library.db"), "src")
    assert r["artists"] == 1
    got = dst.info(items(dst)["a.png"]["id"])["credit"]
    assert got["artist"]["name"] == "Ren" and got["url"] == "https://www.pixiv.net/artworks/42"
    assert [(a["name"], a["aliases"], a["links"]) for a in dst.artists()["artists"]] == [("Ren", ["ren_draws"], ["https://x.com/ren_draws"])]
    fileops.undo(dst.db, r["op"])
    assert dst.files(q="has:artist")["total"] == 0 and dst.artists()["artists"] == []


# ---------------------------------------------------------------- the QA pass
def test_undoing_a_filing_gives_back_the_record_it_took_over(inbox, media):
    lib, f, dl = inbox
    import shutil
    kept = image(media / "keep.png", seed=4)
    index(lib, f)
    fid = items(lib)["keep.png"]["id"]
    lib.mark(fid, rating=5)
    lib.set_tags(fid, add=["sea"])
    shutil.copy2(kept, dl / "again.png")
    os.remove(kept)
    scan.scan_folder(lib.db, f)  # gone from the library folder: missing, ratings kept
    settled(dl / "again.png")
    r = lib.inbox.file([lib.inbox.items()["items"][0]["key"]], f["id"], "")
    with lib.db.read() as c:
        assert c.execute("SELECT id FROM files WHERE name='again.png'").fetchone()[0] == fid  # the same picture: it took the record over
    fileops.undo(lib.db, r["op"])
    with lib.db.read() as c:
        row = c.execute("SELECT name, missing FROM files WHERE id=?", (fid,)).fetchone()
        assert tuple(row) == ("keep.png", 1)
        assert c.execute("SELECT rating FROM marks WHERE file_id=?", (fid,)).fetchone()[0] == 5
    assert (dl / "again.png").is_file()


def test_an_address_that_isnt_one_holds_nothing_up(lib, media):
    illustrations(lib)
    a, b = image(media / "a.png", seed=1), image(media / "Artists" / "Ren" / "b.png", seed=2)
    downloaded(a, page="http://[broken.example/x")
    index(lib, folder(lib, media))
    assert lib.credits.pending() == 0 and lib.files(q="artist:ren")["total"] == 1
    assert credits.from_url("http://[broken.example/x") == {}


def test_leaving_an_artist_out_keeps_the_pictures_without_one(lib, media):
    illustrations(lib)
    image(media / "Artists" / "Ren" / "a.png", seed=1)
    image(media / "b.png", seed=2)
    image(media / "c.png", seed=3)
    index(lib, folder(lib, media))
    assert lib.files(q="-artist:ren")["total"] == 2


def test_a_new_artist_spelled_like_one_found_is_the_same_one(lib, media):
    illustrations(lib)
    image(media / "__girl_drawn_by_mika_art__0123456789abcdef0123456789abcdef.png", seed=1)
    index(lib, folder(lib, media))
    lib.artist_save(None, "Mika-Art")
    assert [(a["name"], a["n"]) for a in lib.artists()["artists"]] == [("Mika-Art", 1)]
    assert lib.files(q='artist:"mika art"')["total"] == 1


def test_any_name_you_type_is_yours(lib, media):
    illustrations(lib)
    image(media / "a.png", seed=1)
    index(lib, folder(lib, media))
    from mlgallery import bulk
    r = bulk.apply(lib.db, [items(lib)["a.png"]["id"]], "artist", "Unknown")  # a placeholder when found, a name when you type it
    assert lib.files(q="artist:unknown")["total"] == 1
    assert fileops.history(lib.db)[0]["summary"] == "Set the artist of 1 file to Unknown" and r["op"]
