"""Organising: moves (one drive and across drives), the Trash, Undo, bulk changes, albums, duplicates, backups.

All on throwaway folders. Emptying the Trash is pointed at a stand-in, so nothing lands in your Recycle Bin.
"""
import json
import os
import random
import shutil
import time

import pytest

from conftest import image
from mlgallery import bulk, fileops, scan
from sample_media import generation, make_image
from test_meta import q, read_all


def setup(lib, tmp_path, n=4):
    a, b = tmp_path / "A", tmp_path / "B"
    a.mkdir()
    b.mkdir()
    for i in range(n):
        image(a / f"f{i}.png", (256, 256), seed=i)
    fa = lib.add_folder(str(a))["id"]
    fb = lib.add_folder(str(b))["id"]
    for f in lib.folders():
        scan.scan_folder(lib.db, f)
    ids = {r["name"]: r["id"] for r in lib.files()["items"]}
    return a, b, fa, fb, ids


@pytest.fixture
def quiet_lib(lib):
    """The library without its scan queue thread running (tests scan themselves)."""
    return lib


def test_move_keeps_the_record_and_undo_moves_back(lib, tmp_path):
    a, b, fa, fb, ids = setup(lib, tmp_path)
    fid = ids["f0.png"]
    lib.mark(fid, rating=5)
    lib.set_tags(fid, add=["keep"])
    plan = fileops.plan_move(lib.db, [fid, ids["f1.png"]], fb, "sorted/day1")
    assert plan["count"] == 2 and plan["dest"].endswith(os.path.join("B", "sorted", "day1")) and plan["copies"] == 0
    r = fileops.move(lib.db, [fid, ids["f1.png"]], fb, "sorted/day1")
    assert r["moved"] == 2 and not r["failed"]
    assert (b / "sorted" / "day1" / "f0.png").is_file() and not (a / "f0.png").exists()
    info = lib.info(fid)
    assert info["folder_id"] == fb and info["rel"] == "sorted/day1/f0.png" and info["rating"] == 5 and info["tags"][0]["name"] == "keep"
    assert q(lib, "keep") == ["f0.png"]
    for f in lib.folders():  # a rescan agrees with what the move recorded
        c = scan.scan_folder(lib.db, f)
        assert c["added"] == 0 and c["missing"] == 0
    fileops.undo(lib.db, r["op"])
    assert (a / "f0.png").is_file() and lib.info(fid)["folder_id"] == fa
    with pytest.raises(fileops.OpError, match="already"):
        fileops.undo(lib.db, r["op"])


def test_a_taken_name_gets_a_number(lib, tmp_path):
    a, b, fa, fb, ids = setup(lib, tmp_path)
    image(b / "f0.png", (32, 32), seed=99)
    scan.scan_folder(lib.db, next(f for f in lib.folders() if f["id"] == fb))
    r = fileops.move(lib.db, [ids["f0.png"]], fb)
    assert r["moved"] == 1 and (b / "f0 (2).png").is_file() and (b / "f0.png").is_file()


def test_across_drives_copy_check_remove(lib, tmp_path, monkeypatch):
    a, b, fa, fb, ids = setup(lib, tmp_path)
    monkeypatch.setattr(fileops, "_same_drive", lambda x, y: False)
    r = fileops.move(lib.db, [ids["f2.png"]], fb)
    assert r["moved"] == 1 and (b / "f2.png").is_file() and not (a / "f2.png").exists()
    assert not list(b.glob("*.mlg-part"))
    # a copy that doesn't match: the original stays, nothing changes in the index
    real = scan.fingerprint
    monkeypatch.setattr(scan, "fingerprint", lambda p, s: "bad" if p.endswith(".mlg-part") else real(p, s))
    r = fileops.move(lib.db, [ids["f3.png"]], fb)
    assert r["moved"] == 0 and "didn't match" in r["failed"][0]["error"]
    assert (a / "f3.png").is_file() and not (b / "f3.png").exists() and lib.info(ids["f3.png"])["folder_id"] == fa


def test_moves_refuse_what_isnt_safe(lib, tmp_path):
    a, b, fa, fb, ids = setup(lib, tmp_path)
    for bad in ("../escape", "C:/x", ".hidden"):
        with pytest.raises(fileops.OpError):
            fileops.plan_move(lib.db, [ids["f0.png"]], fb, bad)
    with pytest.raises(fileops.OpError, match="isn't in the library"):
        fileops.plan_move(lib.db, [ids["f0.png"]], 999)
    assert fileops.plan_move(lib.db, [ids["f0.png"]], fa)["already"] == 1  # it's there already
    with pytest.raises(fileops.OpError, match="Nothing to move"):
        fileops.move(lib.db, [ids["f0.png"]], fa)
    # an offline drive: refused, and its files can't be moved out either
    shutil.move(str(b), str(tmp_path / "B-away"))
    scan.scan_folder(lib.db, next(f for f in lib.folders() if f["id"] == fb))
    with pytest.raises(fileops.OpError, match="reachable"):
        fileops.plan_move(lib.db, [ids["f0.png"]], fb)


def test_trash_restore_empty_and_their_undo(lib, tmp_path, monkeypatch):
    a, b, fa, fb, ids = setup(lib, tmp_path)
    fid = ids["f1.png"]
    r = fileops.trash(lib.db, [fid])
    assert r["trashed"] == 1 and not (a / "f1.png").exists()
    trashed = list((a / fileops.TRASH).rglob("f1.png"))
    assert len(trashed) == 1
    assert "f1.png" not in q(lib, "") and [x["name"] for x in lib.files(sort="trashed")["items"]] == ["f1.png"]
    assert lib.status()["trash"] == 1 and lib.file_path(fid) == str(trashed[0])
    for f in lib.folders():  # a rescan neither brings it back nor loses it
        scan.scan_folder(lib.db, f)
    assert lib.files(sort="trashed")["total"] == 1 and lib.files()["total"] == 3
    fileops.undo(lib.db, r["op"])
    assert (a / "f1.png").is_file() and lib.files()["total"] == 4 and not (a / fileops.TRASH).exists()  # its empty folders tidied away
    # restore puts it back beside a file that took its name meanwhile
    fileops.trash(lib.db, [fid])
    image(a / "f1.png", (40, 40), seed=50)
    rr = fileops.restore(lib.db, [fid])
    assert rr["restored"] == 1 and (a / "f1 (2).png").is_file()
    fileops.undo(lib.db, rr["op"])
    assert lib.files(sort="trashed")["total"] == 1
    sent = []
    import send2trash
    monkeypatch.setattr(send2trash, "send2trash", lambda p: (sent.append(p), os.remove(p)))
    e = fileops.empty(lib.db)
    assert e["emptied"] == 1 and len(sent) == 1 and lib.files(sort="trashed")["total"] == 0
    assert lib.info(fid) is None
    hist = fileops.history(lib.db)
    assert hist[0]["kind"] == "empty" and not hist[0]["undoable"]


def test_bulk_marks_tags_albums_and_undo(lib, tmp_path):
    a, b, fa, fb, ids = setup(lib, tmp_path)
    all_ids = list(ids.values())
    lib.mark(ids["f0.png"], rating=2, favorite=True)
    r = bulk.apply(lib.db, all_ids, "rate", 4)
    assert all(lib.info(i)["rating"] == 4 for i in all_ids)
    fileops.undo(lib.db, r["op"])
    assert lib.info(ids["f0.png"])["rating"] == 2 and lib.info(ids["f0.png"])["favorite"] == 1 and lib.info(ids["f1.png"])["rating"] == 0
    r = bulk.apply(lib.db, all_ids, "favorite", True)
    assert q(lib, "is:fav") == sorted(ids)
    fileops.undo(lib.db, r["op"])
    assert q(lib, "is:fav") == ["f0.png"]
    r = bulk.apply(lib.db, all_ids[:2], "tag", "night")
    assert len(q(lib, "tag:night")) == 2
    fileops.undo(lib.db, r["op"])
    assert q(lib, "tag:night") == [] and lib.tags("night") == []
    r = bulk.apply(lib.db, all_ids, "album", "Coast")
    assert len(q(lib, "album:coast")) == 4 and lib.albums()[0]["n"] == 4
    r2 = bulk.apply(lib.db, all_ids[:1], "unalbum", "Coast")
    assert len(q(lib, "album:coast")) == 3
    fileops.undo(lib.db, r2["op"])
    fileops.undo(lib.db, r["op"])
    assert q(lib, "album:coast") == []
    with pytest.raises(fileops.OpError):
        bulk.apply(lib.db, all_ids, "explode", 1)
    with pytest.raises(fileops.OpError):
        bulk.apply(lib.db, [], "rate", 1)


def test_albums_from_the_workflow_and_their_removal_sticks(lib, media):
    p = str(media / "s.png")
    g = generation(0, random.Random(0), (64, 64))  # i=0 carries a Gallery Info entry with the album Coastline
    make_image(p, (64, 64), 0, g)
    with lib.db.write() as c:
        fid = c.execute("INSERT INTO folders (path, added) VALUES (?, ?)", (str(media), time.time())).lastrowid
    scan.scan_folder(lib.db, {"id": fid, "path": str(media)})
    read_all(lib)
    assert q(lib, "album:coastline") == ["s.png"] and lib.albums()[0]["name"] == "Coastline"
    file_id = lib.files()["items"][0]["id"]
    bulk.apply(lib.db, [file_id], "unalbum", "Coastline")
    with lib.db.write() as c:
        c.execute("UPDATE files SET meta=0")
    read_all(lib)
    assert q(lib, "album:coastline") == []  # taken out by you: reading the file again doesn't put it back
    a = lib.album_save(None, "Picks")
    assert lib.album_save(a["id"], "Best picks")["name"] == "Best picks"
    with pytest.raises(ValueError, match="already"):
        lib.album_save(None, "coastline")
    assert lib.album_delete(a["id"]) and os.path.isfile(p)


def test_duplicates(lib, tmp_path):
    a, b, fa, fb, ids = setup(lib, tmp_path, n=2)
    shutil.copy2(a / "f0.png", b / "copy.png")  # the same bytes
    from PIL import Image
    Image.open(a / "f1.png").convert("RGB").save(b / "f1-again.jpg", quality=95)  # the same picture, saved again
    for f in lib.folders():
        scan.scan_folder(lib.db, f)
    for r in lib.files()["items"]:
        lib.thumbs.get(r["id"])
    d = lib.duplicates()
    kinds = sorted(g["kind"] for g in d["groups"])
    assert kinds == ["copies", "same picture"]
    copies = next(g for g in d["groups"] if g["kind"] == "copies")
    assert {f["name"] for f in copies["files"]} == {"f0.png", "copy.png"} and copies["wasted"] > 0
    same = next(g for g in d["groups"] if g["kind"] == "same picture")
    assert {f["name"] for f in same["files"]} == {"f1.png", "f1-again.jpg"}

    # Keep both: not offered again (one History step), until the group changes; Show them again offers them
    r = lib.duplicates_keep([f["id"] for f in copies["files"]])
    d = lib._duplicates()
    assert [g["kind"] for g in d["groups"]] == ["same picture"] and d["kept"] == 1
    fileops.undo(lib.db, r["op"])
    assert len(lib._duplicates()["groups"]) == 2
    lib.duplicates_keep([f["id"] for f in copies["files"]])
    shutil.copy2(a / "f0.png", b / "copy-2.png")  # a third copy: another group, offered
    for f in lib.folders():
        scan.scan_folder(lib.db, f)
    d = lib._duplicates()
    assert len(next(g for g in d["groups"] if g["kind"] == "copies")["files"]) == 3 and d["kept"] == 0
    lib.duplicates_keep([f["id"] for f in next(g for g in d["groups"] if g["kind"] == "copies")["files"]])
    u = lib.duplicates_unkeep()
    assert u["count"] == 2 and len(lib._duplicates()["groups"]) == 2
    fileops.undo(lib.db, u["op"])
    assert len(lib._duplicates()["groups"]) == 1
    with pytest.raises(ValueError):
        lib.duplicates_keep([copies["files"][0]["id"]])


def test_select_a_whole_view(lib, tmp_path):
    a, b, fa, fb, ids = setup(lib, tmp_path)
    assert sorted(lib.resolve({"view": {"q": "", "folder": fa}})) == sorted(ids.values())
    assert lib.resolve([3, 1]) == [3, 1]
    with pytest.raises(fileops.OpError):
        lib.resolve("everything")


def test_backups_keep_seven(lib, tmp_path):
    folder = os.path.join(lib.data_dir, "backups")
    os.makedirs(folder, exist_ok=True)
    for i in range(9):
        open(os.path.join(folder, f"library-2026-01-0{i + 1}.db"), "w").close()
    path = lib.backup(force=True)
    assert os.path.isfile(path) and len([f for f in os.listdir(folder) if f.endswith(".db")]) == 7
    import sqlite3
    with sqlite3.connect(path) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] >= 5
    assert lib.backup() is None  # once a day
