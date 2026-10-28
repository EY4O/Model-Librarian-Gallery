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


# ---------------------------------------------------------------- saved by the Chrome extension
def ext_folder(lib, dl):
    """Downloads\\Model Librarian Gallery: where the extension saves (offered like a downloads folder; notes trusted only here)."""
    ext = dl / "Model Librarian Gallery"
    ext.mkdir(exist_ok=True)
    lib.inbox.add_folder(str(ext))
    return ext


def noted(path, **note):
    import json
    with open(str(path) + ".mlg.json", "w", encoding="utf-8") as fh:
        json.dump(note, fh)


def test_a_picture_saved_from_the_browser_comes_with_its_page(inbox, media, tmp_path):
    import json
    lib, f, dl = inbox
    a = image(ext_folder(lib, dl) / "98765_p0.png", seed=4)
    with open(str(a) + ".mlg.json", "w", encoding="utf-8") as fh:
        json.dump({"page": "https://www.pixiv.net/artworks/98765", "site": "pixiv", "post": "98765", "artist": "Mika Art",
                   "artist_url": "https://www.pixiv.net/users/42", "title": "Harbour at dusk", "description": "A study.",
                   "tags": ["landscape", "harbour", "landscape"]}, fh)
    settled(a)
    it = lib.inbox.items()["items"][0]
    assert it["artist"] == "Mika Art" and it["site"] == "pixiv" and it["title"] == "Harbour at dusk" and it["tags"] == 3
    assert lib.inbox.items()["total"] == 1  # the note isn't offered itself
    r = lib.inbox.file([it["key"]], f["id"], "")
    fid = items(lib)["98765_p0.png"]["id"]
    info = lib.info(fid)
    assert info["credit"]["artist"]["name"] == "Mika Art" and info["credit"]["url"] == "https://www.pixiv.net/artworks/98765"
    assert sorted(t["name"] for t in info["tags"]) == ["harbour", "landscape"] and {t["source"] for t in info["tags"]} == {"site"}
    assert info["note"]["note"] == "Harbour at dusk\n\nA study." and info["note"]["source"] == "site"
    assert lib.artists()["artists"][0]["links"] == ["https://www.pixiv.net/users/42"]
    assert not os.path.exists(str(a) + ".mlg.json")  # read into the index
    lib.set_tags(fid, remove=["harbour"])  # a site's tag taken off stays off
    assert [t["name"] for t in lib.info(fid)["tags"]] == ["landscape"]
    fileops.undo(lib.db, r["op"])
    assert os.path.isfile(a) and os.path.isfile(str(a) + ".mlg.json")  # back, with its note, to file again


def test_the_extensions_folder_is_offered_too(inbox, media, monkeypatch, tmp_path):
    from mlgallery import downloads
    lib, f, dl = inbox
    home = tmp_path / "Home Downloads"
    (home / "Model Librarian Gallery").mkdir(parents=True)
    a = image(home / "Model Librarian Gallery" / "saved.png", seed=5)
    settled(a)
    monkeypatch.setattr(downloads, "downloads_folder", lambda: str(home))
    lib.settings.update({"inbox_downloads": True})
    lib.inbox._listing = None
    assert any(f_["path"].endswith("Model Librarian Gallery") for f_ in lib.inbox.folders())
    assert "saved.png" in [i["name"] for i in lib.inbox.items()["items"]]


def test_pawchive_addresses():
    assert credits.from_url("https://pawchive.pw/patreon/user/21429170/post/173387261") == {
        "site": "pawchive", "post": "173387261", "url": "https://pawchive.pw/patreon/user/21429170/post/173387261"}



def test_what_the_note_says_isnt_replaced_by_what_windows_says_later(inbox, media):
    lib, f, dl = inbox
    a = image(ext_folder(lib, dl) / "illust.jpg", seed=6)
    noted(a, page="https://x.com/realartist/status/1234", site="x", post="1234", artist="realartist")
    downloaded(a, picture="https://pbs.twimg.com/media/GAbc.jpg")  # (says only "X": no post, no artist)
    settled(a)
    lib.inbox.file([lib.inbox.items()["items"][0]["key"]], f["id"], "")
    index(lib, f)  # the background reader looks at it again
    got = lib.info(items(lib)["illust.jpg"]["id"])["credit"]
    assert got["artist"]["name"] == "realartist" and got["url"] == "https://x.com/realartist/status/1234" and got["artist_from"] == "note"


def test_a_note_is_only_believed_in_the_extensions_folder(inbox, media):
    lib, f, dl = inbox
    a = image(dl / "planted.png", seed=7)  # (any site can put a file and a note in Downloads itself)
    noted(a, artist="Someone Else", tags=["spam"])
    b = image(ext_folder(lib, dl) / "broken.png", seed=8)
    with open(str(b) + ".mlg.json", "w") as fh:
        fh.write("[" * 200000)  # not a note: too deep to read
    settled(a, b)
    got = {i["name"]: i for i in lib.inbox.items()["items"]}
    assert got["planted.png"]["artist"] is None and got["planted.png"]["tags"] == 0
    assert got["broken.png"]["artist"] is None  # and the listing still works


def test_a_note_that_comes_after_its_picture(inbox, media):
    lib, f, dl = inbox
    a = image(ext_folder(lib, dl) / "late.png", seed=9)
    settled(a)
    assert lib.inbox.items()["items"][0]["artist"] is None
    noted(a, artist="Late Artist", page="https://www.pixiv.net/artworks/5")
    assert lib.inbox.items()["items"][0]["artist"] == "Late Artist"


def test_the_artists_page_goes_to_that_artist_only(inbox, media):
    lib, f, dl = inbox
    a = image(ext_folder(lib, dl) / "repost.jpg", seed=10)
    noted(a, artist="repost_account", artist_url="https://x.com/repost_account", page="https://x.com/repost_account/status/9")
    settled(a)
    lib.inbox.file([lib.inbox.items()["items"][0]["key"]], f["id"], "", artist="Real Artist")  # you know better
    assert {a_["name"]: a_["links"] for a_ in lib.artists()["artists"]} == {"Real Artist": []}


# ---------------------------------------------------------------- found in a QA pass
@pytest.mark.parametrize("name,want", [("$hiro", "hiro"), (".hack", "hack"), ("art.library", "art.library (artist)"), ("@eaDir", "@eaDir (artist)"),
                                       ("Mika Art", "Mika Art"), ("...", "Unknown")])
def test_an_artists_folder_is_one_the_library_looks_in(name, want):
    from mlgallery import downloads
    assert downloads.safe_folder(name) == want and not scan.skip_dir(downloads.safe_folder(name), True)


def test_a_download_is_never_filed_where_the_library_doesnt_look(inbox, media):
    lib, f, dl = inbox
    a = image(dl / "one.png", seed=1)
    settled(a)
    key = lib.inbox.items()["items"][0]["key"]
    with pytest.raises(fileops.OpError):
        lib.inbox.file([key], f["id"], "Artists/.hidden")
    assert os.path.isfile(a) and lib.inbox.items()["total"] == 1  # still waiting, where it was


def test_a_library_folder_inside_downloads_leaves_downloads_on(inbox, media, tmp_path):
    lib, f, dl = inbox
    inside = dl / "gallery-dl"
    inside.mkdir()
    lib.add_folder(str(inside))
    assert all(x["ok"] for x in lib.inbox.folders() if os.path.normcase(x["path"]) == os.path.normcase(str(dl)))
    with pytest.raises(ValueError):
        lib.inbox.add_folder(str(inside))  # (the library folder itself still isn't one)


def test_what_a_note_says_is_tidied(tmp_path):
    from mlgallery import downloads
    (tmp_path / "Model Librarian Gallery").mkdir()
    p = image(tmp_path / "Model Librarian Gallery" / "x.png", seed=1)
    noted(p, artist="Anonymous", site="Pixiv", page="https://www.pixiv.net/artworks/5")
    n = downloads.read_note(str(p))
    assert "artist" not in n and n["site"] == "pixiv"  # (junk isn't an artist; a site as the Gallery writes it)


def test_undo_puts_back_what_you_typed_for_a_record_taken_over_and_the_artists_link(inbox, media):
    lib, f, dl = inbox
    import shutil
    kept = image(media / "keep.png", seed=4)
    index(lib, f)
    fid = items(lib)["keep.png"]["id"]
    lib.set_credit(fid, artist="Original")
    shutil.copy2(kept, dl / "again.png")
    os.remove(kept)
    scan.scan_folder(lib.db, f)
    settled(dl / "again.png")
    r = lib.inbox.file([lib.inbox.items()["items"][0]["key"]], f["id"], "", artist="Typed")
    assert lib.info(fid)["credit"]["artist"]["name"] == "Typed"
    fileops.undo(lib.db, r["op"])
    with lib.db.read() as c:
        assert c.execute("SELECT artist FROM credits WHERE file_id=? AND src='user'", (fid,)).fetchone()[0] == "Original"
    # a note's artist page is taken off again, so the artist the Gallery made goes, and the name's free for one of yours
    a = image(ext_folder(lib, dl) / "5_p0.png", seed=6)
    noted(a, page="https://www.pixiv.net/artworks/5", artist="Mika", artist_url="https://www.pixiv.net/users/9")
    settled(a)
    r = lib.inbox.file([next(it["key"] for it in lib.inbox.items()["items"] if it["name"] == "5_p0.png")], f["id"], "")
    assert lib.artists()["artists"][0]["links"] == ["https://www.pixiv.net/users/9"]
    fileops.undo(lib.db, r["op"])
    assert not any(x["name"] == "Mika" for x in lib.artists()["artists"])
    assert lib.artist_save(None, "Mika")


def test_an_artist_only_in_the_trash_doesnt_block_the_name(lib, media):
    illustrations(lib)
    image(media / "Artists" / "Ren" / "a.png", seed=1)
    f = folder(lib, media)
    index(lib, f)
    fileops.trash(lib.db, [items(lib)["a.png"]["id"]])
    assert not any(x["name"] == "Ren" for x in lib.artists()["artists"])
    aid = lib.artist_save(None, "Ren")  # (the Gallery's own, hidden: taken over, not a clash)
    assert aid and any(x["name"] == "Ren" for x in lib.artists()["artists"])


def test_a_listing_made_while_dismissing_isnt_kept(inbox, media):
    lib, f, dl = inbox
    a = image(dl / "one.png", seed=1)
    settled(a)
    first = lib.inbox.entries()
    lib.inbox._listing = (lib.inbox._listing[0], first)  # (as if made just before)
    lib.inbox.dismiss([first[0]["key"]])
    lib.inbox._listing = (lib.inbox._listing[0] if lib.inbox._listing else None, first)  # a listing still being made stores the old one...
    assert lib.inbox.entries() == []  # ...but it isn't taken: dismissing started a new one


def test_a_saved_booru_pictures_characters_are_offered_and_theirs_once_filed(inbox, media):
    lib, f, dl = inbox
    ext = ext_folder(lib, dl)
    look = {"name": "Vale Lumen", "series": "Lighthouse Tales", "appearance": ["brown hair", "short hair"], "outfit": ["school uniform"]}
    a, b, c = (image(ext / n, seed=s) for n, s in (("a.png", 11), ("b.png", 12), ("c.png", 13)))
    for p, post in ((a, "1"), (b, "2"), (c, "3")):
        noted(p, page=f"https://danbooru.donmai.us/posts/{post}", site="danbooru", post=post, artist="Someartist",
              tags=["vale lumen", "nova (starfall)", "lighthouse tales", "solo"],
              characters=[{"tag": "vale_lumen", **look}, {"tag": "nova_(starfall)", "name": "Nova", "series": "Starfall"}, "junk"])
    settled(a, b, c)
    mine = lib.character_save(None, "Lumen Vale", [], [])  # yours, given name first, no tag yet
    got = {it["name"]: it for it in lib.inbox.items()["items"]}
    och, nova = got["a.png"]["characters"]
    assert och["suggest"] == [{"id": mine, "name": "Lumen Vale"}] and "have" not in och, "yours by name in the other order, suggested"
    assert nova["suggest"] == [] and nova["name"] == "Nova"
    # added to yours: its tag as a rule and preset, the post's look; then a new one for Nova
    r = lib.inbox.character(got["a.png"]["key"], "vale_lumen", mine)
    assert r == {"character": {"id": mine, "name": "Lumen Vale"}, "created": False}
    ch = lib.character(mine)
    assert ch["shared"]["tag"] == "vale_lumen" and ch["shared"]["series"] == "Lighthouse Tales" and ch["shared"]["appearance"] == ["brown hair", "short hair"]
    assert ch["shared"]["outfits"] == {"default": ["school uniform"]} and ("word", "vale lumen") in [(x["kind"], x["value"]) for x in ch["rules"]]
    lib.inbox.character(got["a.png"]["key"], "vale_lumen", mine)  # (again: nothing twice)
    assert len(lib.character(mine)["rules"]) == 1 and len(lib.character(mine)["shared"]["outfits"]) == 1
    r2 = lib.inbox.character(got["a.png"]["key"], "nova_(starfall)", None)
    assert r2["created"] and r2["character"]["name"] == "Nova"
    got = {it["name"]: it for it in lib.inbox.items()["items"]}
    assert [x.get("have") for x in got["b.png"]["characters"]] == [{"id": mine, "name": "Lumen Vale"}, {"id": r2["character"]["id"], "name": "Nova"}], "yours now, on the others too"
    # filed: the picture is theirs, with no prompt (its tags say so)
    lib.inbox.file([got["a.png"]["key"], got["b.png"]["key"]], f["id"], "")
    ids = items(lib)
    for n in ("a.png", "b.png"):
        assert sorted(x["name"] for x in lib.info(ids[n]["id"])["characters"]) == ["Lumen Vale", "Nova"], n
    assert lib.character(mine)["n"] == 2
    # a character made after the picture was filed finds it too
    lib.inbox.file([got["c.png"]["key"]], f["id"], "")
    assert lib.character(mine)["n"] == 3
    lib.character_delete(r2["character"]["id"])
    late = lib.character_save(None, "Nova", [], [{"kind": "word", "value": "nova (starfall)"}])
    assert lib.character(late)["n"] == 3
    other = lib.character_save(None, "Someone", [], [{"kind": "word", "value": "nova (starfall)"}])
    assert lib.character(other)["n"] == 0, "a rule that isn't its own name: the prompt only, as before"
    with pytest.raises(ValueError):
        lib.inbox.character("nope", "vale_lumen", None)
