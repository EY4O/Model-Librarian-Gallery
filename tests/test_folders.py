"""Folders from the sidebar: new, rename, move, delete, files copied in from Explorer, and the Undo of each.

All on throwaway folders with generated pictures.
"""
import os
import time

import pytest

from conftest import image
from mlgallery import fileops, folders, scan
from mlgallery.fileops import OpError
from test_server import H, run


def setup(lib, tmp_path):
    """Library folder A (sub/x0.png, sub/deep/x1.png, top.png) and an empty library folder B, scanned."""
    a, b = tmp_path / "A", tmp_path / "B"
    image(a / "sub" / "x0.png", seed=1)
    image(a / "sub" / "deep" / "x1.png", seed=2)
    image(a / "top.png", seed=3)
    b.mkdir()
    fa, fb = lib.add_folder(str(a))["id"], lib.add_folder(str(b))["id"]
    for f in lib.folders():
        scan.scan_folder(lib.db, f)
    ids = {r["name"]: r["id"] for r in lib.files()["items"]}
    return a, b, fa, fb, ids


def rels(lib, fid):
    return {d["rel"]: d["n"] for d in lib.dirs(fid)}


def agrees(lib):
    """A rescan finds nothing to change: the index followed the disk."""
    for f in lib.folders():
        c = scan.scan_folder(lib.db, f)
        assert c["added"] == c["moved"] == c["missing"] == 0, c


def test_new_folder_is_listed_while_empty_and_undo_removes_it(lib, tmp_path):
    a, b, fa, fb, ids = setup(lib, tmp_path)
    r = folders.make(lib.db, fa, "sub", "Coast")
    assert (a / "sub" / "Coast").is_dir() and r["rel"] == "sub/Coast"
    assert rels(lib, fa)["sub/Coast"] == 0 and rels(lib, fa)["sub"] == 2
    folders.make(lib.db, fb, "", "Empty top")
    assert rels(lib, fb) == {"Empty top": 0}
    agrees(lib)
    assert "sub/Coast" in rels(lib, fa)  # (a rescan keeps an empty folder listed)
    with pytest.raises(OpError, match="already a folder called Coast"):
        folders.make(lib.db, fa, "sub", "coast")
    for bad, says in (("a/b", "can't have"), ("", "name"), (".hidden", "dot"), ("con", "keeps the name"), ("what?", "can't have")):
        with pytest.raises(OpError, match=says):
            folders.make(lib.db, fa, "", bad)
    fileops.undo(lib.db, r["op"])
    assert not (a / "sub" / "Coast").exists() and "sub/Coast" not in rels(lib, fa)


def test_undoing_a_new_folder_that_has_files_now_is_refused(lib, tmp_path):
    a, b, fa, fb, ids = setup(lib, tmp_path)
    r = folders.make(lib.db, fa, "", "New")
    image(a / "New" / "n.png", seed=9)
    with pytest.raises(OpError, match="has something in it"):
        fileops.undo(lib.db, r["op"])
    assert (a / "New" / "n.png").is_file()


def test_rename_keeps_every_record_and_undo_renames_back(lib, tmp_path):
    a, b, fa, fb, ids = setup(lib, tmp_path)
    lib.mark(ids["x1.png"], rating=4)
    folders.make(lib.db, fa, "sub/deep", "empty")
    r = folders.rename(lib.db, fa, "sub", "Sorted")
    assert (a / "Sorted" / "deep" / "x1.png").is_file() and not (a / "sub").exists()
    info = lib.info(ids["x1.png"])
    assert info["rel"] == "Sorted/deep/x1.png" and info["rating"] == 4
    assert rels(lib, fa) == {"Sorted": 2, "Sorted/deep": 1, "Sorted/deep/empty": 0}
    agrees(lib)
    with pytest.raises(OpError, match="its name already"):
        folders.rename(lib.db, fa, "Sorted", "Sorted")
    fileops.undo(lib.db, r["op"])
    assert (a / "sub" / "deep" / "x1.png").is_file() and lib.info(ids["x1.png"])["rel"] == "sub/deep/x1.png"
    assert "sub/deep/empty" in rels(lib, fa)
    agrees(lib)


def test_a_rename_that_only_changes_case(lib, tmp_path):
    a, b, fa, fb, ids = setup(lib, tmp_path)
    folders.rename(lib.db, fa, "sub", "SUB")
    assert sorted(os.listdir(a)) == ["SUB", "top.png"] and lib.info(ids["x0.png"])["rel"] == "SUB/x0.png"
    agrees(lib)


def test_rename_refuses_a_name_that_is_taken(lib, tmp_path):
    a, b, fa, fb, ids = setup(lib, tmp_path)
    (a / "Other").mkdir()
    with pytest.raises(OpError, match="already a folder called Other"):
        folders.rename(lib.db, fa, "sub", "Other")
    with pytest.raises(OpError, match="library folder itself"):
        folders.rename(lib.db, fa, "", "x")
    assert (a / "sub" / "x0.png").is_file()


def test_move_into_another_folder_and_another_library_folder(lib, tmp_path):
    a, b, fa, fb, ids = setup(lib, tmp_path)
    lib.set_tags(ids["x1.png"], add=["keep"])
    folders.make(lib.db, fa, "", "Into")
    r = folders.move(lib.db, fa, "sub/deep", fa, "Into")
    assert (a / "Into" / "deep" / "x1.png").is_file() and lib.info(ids["x1.png"])["rel"] == "Into/deep/x1.png"
    assert rels(lib, fa) == {"Into": 1, "Into/deep": 1, "sub": 1}
    r2 = folders.move(lib.db, fa, "Into/deep", fb, "")
    info = lib.info(ids["x1.png"])
    assert (b / "deep" / "x1.png").is_file() and info["folder_id"] == fb and info["rel"] == "deep/x1.png" and info["tags"][0]["name"] == "keep"
    assert [f["file_count"] for f in sorted(lib.folders(), key=lambda f: f["id"])] == [2, 1]
    agrees(lib)
    fileops.undo(lib.db, r2["op"])
    fileops.undo(lib.db, r["op"])
    assert (a / "sub" / "deep" / "x1.png").is_file() and lib.info(ids["x1.png"])["folder_id"] == fa
    agrees(lib)


def test_moves_refuse_what_cant_be(lib, tmp_path, monkeypatch):
    a, b, fa, fb, ids = setup(lib, tmp_path)
    with pytest.raises(OpError, match="inside itself"):
        folders.move(lib.db, fa, "sub", fa, "sub/deep")
    with pytest.raises(OpError, match="inside itself"):
        folders.move(lib.db, fa, "sub", fa, "sub")
    with pytest.raises(OpError, match="there already"):
        folders.move(lib.db, fa, "sub/deep", fa, "sub")
    (b / "sub").mkdir()
    with pytest.raises(OpError, match="already a folder called sub"):
        folders.move(lib.db, fa, "sub", fb, "")
    monkeypatch.setattr(fileops, "_same_drive", lambda x, y: False)
    with pytest.raises(OpError, match="same drive"):
        folders.move(lib.db, fa, "sub/deep", fb, "")
    assert (a / "sub" / "deep" / "x1.png").is_file()


def test_delete_sends_its_pictures_to_the_trash_and_keeps_other_files(lib, tmp_path):
    a, b, fa, fb, ids = setup(lib, tmp_path)
    lib.mark(ids["x0.png"], rating=5)
    (a / "sub" / "deep" / "notes.txt").write_text("mine")
    (a / "sub" / "empty").mkdir()
    r = folders.delete(lib.db, fa, "sub")
    assert r["trashed"] == 2 and r["kept"] == 1 and not r["gone"]
    assert (a / "sub" / "deep" / "notes.txt").is_file() and not (a / "sub" / "empty").exists() and not (a / "sub" / "x0.png").exists()
    with lib.db.read() as c:
        assert {x[0] for x in c.execute("SELECT missing FROM files WHERE id IN (?, ?)", (ids["x0.png"], ids["x1.png"]))} == {2}
    assert [o["summary"] for o in fileops.history(lib.db)][:1] == ["Deleted the folder sub (2 files to the Trash)"]  # (one step, not two)
    fileops.undo(lib.db, r["op"])
    assert (a / "sub" / "x0.png").is_file() and (a / "sub" / "empty").is_dir() and lib.info(ids["x0.png"])["rating"] == 5
    agrees(lib)


def test_deleting_a_folder_with_only_pictures_removes_it_whole(lib, tmp_path):
    a, b, fa, fb, ids = setup(lib, tmp_path)
    r = folders.delete(lib.db, fa, "sub")
    assert r["gone"] and r["kept"] == 0 and not (a / "sub").exists() and "sub" not in rels(lib, fa)
    fileops.undo(lib.db, r["op"])
    assert (a / "sub" / "deep" / "x1.png").is_file()


def test_a_scan_lists_empty_folders_and_forgets_them_when_they_go(lib, tmp_path):
    a, b, fa, fb, ids = setup(lib, tmp_path)
    (a / "made" / "inside").mkdir(parents=True)
    (a / ".hidden").mkdir()
    agrees(lib)
    assert rels(lib, fa)["made/inside"] == 0 and rels(lib, fa)["made"] == 0 and ".hidden" not in rels(lib, fa)
    os.rmdir(a / "made" / "inside")
    agrees(lib)
    assert rels(lib, fa)["made"] == 0 and "made/inside" not in rels(lib, fa)


def test_files_in_the_trash_go_back_to_a_folder_renamed_since(lib, tmp_path):
    a, b, fa, fb, ids = setup(lib, tmp_path)
    t = fileops.trash(lib.db, [ids["x0.png"]])
    folders.rename(lib.db, fa, "sub", "Renamed")
    fileops.undo(lib.db, t["op"])
    assert (a / "Renamed" / "x0.png").is_file() and lib.info(ids["x0.png"])["rel"] == "Renamed/x0.png"


def test_copy_in_through_the_server(lib, tmp_path):
    a, b, fa, fb, ids = setup(lib, tmp_path)
    src = image(tmp_path / "outside" / "dropped.png", seed=7)
    data = open(src, "rb").read()
    when = time.time() - 86400 * 30

    async def fn(c):
        d = await (await c.post(f"/api/folders/{fa}/copy/dir", json={"parent": "sub", "name": "Holiday", "fresh": True}, headers=H)).json()
        assert d == {"rel": "sub/Holiday", "made": True}
        d2 = await (await c.post(f"/api/folders/{fa}/copy/dir", json={"parent": "sub", "name": "Holiday", "fresh": True}, headers=H)).json()
        assert d2["rel"] == "sub/Holiday (2)"
        got = []
        for _ in range(2):
            r = await c.post(f"/api/folders/{fa}/copy", params={"sub": "sub/Holiday", "name": "dropped.png", "mtime": str(when)}, data=data, headers=H)
            assert r.status == 200
            got.append(await r.json())
        assert [g["name"] for g in got] == ["dropped.png", "dropped (2).png"] and all(g["id"] for g in got)
        r = await c.post(f"/api/folders/{fa}/copy", params={"sub": "sub", "name": "notes.txt"}, data=b"hi", headers=H)
        assert r.status == 422 and "only pictures and videos" in (await r.json())["error"]
        r = await c.post(f"/api/folders/{fa}/copy", params={"sub": "../escape", "name": "x.png"}, data=data, headers=H)
        assert r.status == 422
        done = await (await c.post(f"/api/folders/{fa}/copy/done", json={"ids": [g["id"] for g in got], "dirs": ["sub/Holiday", "sub/Holiday (2)"], "into": "Holiday"},
                                   headers=H)).json()
        return got, done

    got, done = run(lib, fn)
    p = a / "sub" / "Holiday" / "dropped.png"
    assert p.read_bytes() == data and abs(p.stat().st_mtime - when) < 2 and os.path.isfile(src)  # (a copy: the original stays)
    assert not [n for n in os.listdir(a / "sub" / "Holiday") if n.endswith(".mlg-part")]
    assert lib.info(got[0]["id"])["rel"] == "sub/Holiday/dropped.png"
    fileops.undo(lib.db, done["op"])
    with lib.db.read() as c:
        assert {x[0] for x in c.execute("SELECT missing FROM files WHERE id IN (?, ?)", (got[0]["id"], got[1]["id"]))} == {2}
    assert not (a / "sub" / "Holiday (2)").exists() and not (a / "sub" / "Holiday" / "dropped.png").exists()


def test_folder_routes_answer_with_messages(lib, tmp_path):
    a, b, fa, fb, ids = setup(lib, tmp_path)

    async def fn(c):
        r = await (await c.post(f"/api/folders/{fa}/dirs/new", json={"parent": "", "name": "New"}, headers=H)).json()
        assert r["rel"] == "New" and r["op"]
        r = await c.post(f"/api/folders/{fa}/dirs/rename", json={"rel": "New", "name": "a:b"}, headers=H)
        assert r.status == 422 and "can't have" in (await r.json())["error"]
        r = await (await c.post(f"/api/folders/{fa}/dirs/move", json={"rel": "New", "to_folder": fb, "to_parent": ""}, headers=H)).json()
        assert r["rel"] == "New"
        r = await (await c.post(f"/api/folders/{fb}/dirs/delete", json={"rel": "New"}, headers=H)).json()
        assert r["gone"] and r["trashed"] == 0
    run(lib, fn)
    assert not (b / "New").exists()


def test_phones_cant_change_folders():
    from mlgallery import remote
    for path in ("/api/folders/1/dirs/new", "/api/folders/1/dirs/rename", "/api/folders/1/dirs/move", "/api/folders/1/dirs/delete",
                 "/api/folders/1/copy", "/api/folders/1/copy/dir", "/api/folders/1/copy/done"):
        assert not remote.allowed("POST", path)
    assert not {"dir_make", "dir_move", "dir_delete", "copy_in"} & remote.UNDO


def test_a_folder_left_empty_stays_listed(lib, tmp_path):
    a, b, fa, fb, ids = setup(lib, tmp_path)
    folders.delete(lib.db, fa, "sub/deep")  # sub still has x0.png
    fileops.move(lib.db, [ids["x0.png"]], fb, "")  # ... and now nothing
    assert rels(lib, fa) == {"sub": 0}
    image(a / "other" / "o.png", seed=5)
    for f in lib.folders():
        scan.scan_folder(lib.db, f)
    t = fileops.trash(lib.db, [r["id"] for r in lib.files()["items"] if r["name"] == "o.png"])
    assert t["trashed"] == 1 and rels(lib, fa) == {"sub": 0, "other": 0}
    folders.make(lib.db, fa, "sub", "inner")
    folders.move(lib.db, fa, "sub/inner", fb, "")
    assert rels(lib, fa) == {"sub": 0, "other": 0} and rels(lib, fb) == {"inner": 0}
