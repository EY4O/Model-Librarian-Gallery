"""The edge cases a bug hunt found: names Windows folds, records in the Trash that hold a name, undo that
can't finish, search with only words left out, very big characters, and requests that make no sense."""
import os
import time

import pytest

from conftest import image
from mlgallery import eagle, fileops, scan
from test_eagle import make_eagle
from test_meta import q
from test_server import H, run


def setup(lib, tmp_path, n=4):
    a, b = tmp_path / "A", tmp_path / "B"
    for i in range(n):
        image(a / f"f{i}.png", (64, 64), seed=i)
    b.mkdir()
    fa, fb = lib.add_folder(str(a))["id"], lib.add_folder(str(b))["id"]
    rescan(lib)
    return a, b, fa, fb, {r["name"]: r["id"] for r in lib.files()["items"]}


def rescan(lib):
    for f in lib.folders():
        scan.scan_folder(lib.db, f)


def rel(lib, fid):
    with lib.db.read() as c:
        return dict(c.execute("SELECT folder_id, rel, missing FROM files WHERE id=?", (fid,)).fetchone())


# ---------------------------------------------------------------- moves
def test_a_move_stores_the_folder_names_the_disk_has(lib, tmp_path):
    a, b, fa, fb, ids = setup(lib, tmp_path)
    (b / "sorted").mkdir()
    lib.mark(ids["f0.png"], rating=5)
    fileops.move(lib.db, [ids["f0.png"]], fb, "SORTED")  # Windows doesn't mind the case; the index would
    fileops.move(lib.db, [ids["f1.png"]], fb, "new. ")  # nor trailing dots and spaces, which it drops
    assert rel(lib, ids["f0.png"])["rel"] == "sorted/f0.png" and rel(lib, ids["f1.png"])["rel"] == "new/f1.png"
    assert (b / "new" / "f1.png").exists()
    rescan(lib)  # what every start does: nothing goes missing, nothing comes back as new
    assert rel(lib, ids["f0.png"])["missing"] == 0 and lib.info(ids["f0.png"])["rating"] == 5
    assert lib.files()["total"] == 4


def test_a_name_held_in_the_trash_is_taken(lib, tmp_path):
    a, b, fa, fb, ids = setup(lib, tmp_path)
    image(b / "f0.png", (64, 64), seed=99)
    rescan(lib)
    other = next(r["id"] for r in lib.files(folder=fb)["items"])
    fileops.trash(lib.db, [other])
    r = fileops.move(lib.db, [ids["f0.png"]], fb)  # B/f0.png is free on disk, but the Trash's record holds the name
    assert r["moved"] == 1 and not r["failed"] and rel(lib, ids["f0.png"])["rel"] == "f0 (2).png" and (b / "f0 (2).png").exists()
    assert fileops.restore(lib.db, [other])["restored"] == 1 and (b / "f0.png").exists()


def test_a_new_file_where_a_trashed_one_was(lib, tmp_path):
    a, b, fa, fb, ids = setup(lib, tmp_path)
    fid = ids["f1.png"]
    lib.mark(fid, rating=5)
    lib.set_tags(fid, add=["mine"])
    fileops.trash(lib.db, [fid])
    image(a / "f1.png", (80, 80), seed=77)  # a different picture saved under the same name
    rescan(lib)
    old = rel(lib, fid)
    assert old["missing"] == 2 and old["rel"] == "f1 (2).png"  # still in the Trash, with its rating and tag
    assert lib.info(fid)["rating"] == 5 and [t["name"] for t in lib.info(fid)["tags"]] == ["mine"]
    new = next(r for r in lib.files()["items"] if r["name"] == "f1.png")
    assert new["id"] != fid and not new["rating"]
    assert lib.status()["trash"] == 1
    fileops.restore(lib.db, [fid])  # put back beside the new one
    assert (a / "f1 (2).png").exists() and (a / "f1.png").exists() and rel(lib, fid)["missing"] == 0


def test_a_copy_across_drives_whose_original_stays_leaves_no_second_copy(lib, tmp_path, monkeypatch):
    a, b, fa, fb, ids = setup(lib, tmp_path)
    monkeypatch.setattr(fileops, "_same_drive", lambda x, y: False)
    real = os.remove

    def busy(p):
        if os.path.normcase(str(p)) == os.path.normcase(str(a / "f3.png")):
            raise PermissionError(32, "in use", str(p))
        real(p)
    monkeypatch.setattr(fileops.os, "remove", busy)
    r = fileops.move(lib.db, [ids["f3.png"]], fb)
    assert r["moved"] == 0 and r["failed"] and (a / "f3.png").exists() and not (b / "f3.png").exists()


# ---------------------------------------------------------------- undo
def test_an_undo_that_cant_finish_can_be_tried_again(lib, tmp_path):
    a, b, fa, fb, ids = setup(lib, tmp_path)
    r = fileops.move(lib.db, [ids["f2.png"]], fb)
    image(a / "f2.png", (64, 64), seed=55)  # something took the old name
    with pytest.raises(fileops.OpError, match="Nothing could be undone"):
        fileops.undo(lib.db, r["op"])
    assert next(o for o in fileops.history(lib.db) if o["id"] == r["op"])["undoable"]
    os.remove(a / "f2.png")
    assert fileops.undo(lib.db, r["op"])["undone"] == 1 and (a / "f2.png").exists()


def test_undoing_an_emptied_trash_step_says_so(lib, tmp_path, monkeypatch):
    a, b, fa, fb, ids = setup(lib, tmp_path)
    r = fileops.trash(lib.db, [ids["f0.png"]])
    import send2trash
    monkeypatch.setattr(send2trash, "send2trash", lambda p: os.remove(p))  # a stand-in: never your Recycle Bin
    fileops.empty(lib.db)
    with pytest.raises(fileops.OpError, match="emptied"):
        fileops.undo(lib.db, r["op"])


def test_search_follows_names_that_change(lib, tmp_path):
    a, b, fa, fb, ids = setup(lib, tmp_path)
    image(b / "f0.png", (64, 64), seed=99)
    rescan(lib)
    r = fileops.move(lib.db, [ids["f0.png"]], fb)  # becomes "f0 (2).png"
    assert ids["f0.png"] in [x["id"] for x in lib.files(q="2")["items"]]
    fileops.undo(lib.db, r["op"])
    assert ids["f0.png"] not in [x["id"] for x in lib.files(q="2")["items"]]


def test_eagle_undo_puts_a_twice_changed_note_back(lib, tmp_path):
    media = tmp_path / "media"
    pic = image(media / "one.png", (64, 64), seed=1)
    f = lib.add_folder(str(media))
    scan.scan_folder(lib.db, f)
    fid = lib.files()["items"][0]["id"]
    eag = make_eagle(tmp_path / "eagle", [{"id": "N1", "file": "a.png", "src": pic, "note": "first note"},
                                          {"id": "N2", "file": "b.png", "src": pic, "note": "second note"}])
    r = eagle.apply_import(lib.db, eagle.plan_import(lib.db, str(eag)))
    fileops.undo(lib.db, r["op"])
    assert lib.info(fid)["note"] is None


# ---------------------------------------------------------------- search
def test_words_left_out_anywhere_in_the_search(lib, media):
    for n in ("lighthouse.png", "forest.png", "lighthouse forest.png"):
        image(media / n, seed=len(n))
    f = lib.add_folder(str(media))
    scan.scan_folder(lib.db, f)  # names are found before any metadata is read
    assert q(lib, "lighthouse") == ["lighthouse forest.png", "lighthouse.png"]
    assert q(lib, "lighthouse -forest") == q(lib, "-forest lighthouse") == ["lighthouse.png"]
    assert q(lib, "-forest") == ["lighthouse.png"]
    lib.set_tags(lib.files(q="forest")["items"][0]["id"], add=["green"])
    assert len(q(lib, "-tag:green")) == 2 and len(q(lib, "tag:green")) == 1


def test_values_that_cant_be_read_match_nothing(lib, media):
    image(media / "x.png")
    scan.scan_folder(lib.db, lib.add_folder(str(media)))
    for bad in ("rating:-3", "rating:5-", "steps:abc", "seed:99999999999999999999", "steps:>=99999999999999999999",
                "size:99999999999x2", "date:0000-01", "date:9999-12", "date:0001-01", "date:99999999d"):
        assert q(lib, bad) == [], bad  # and no error
    assert lib.days(q="seed:99999999999999999999") == []


# ---------------------------------------------------------------- big characters
def test_a_character_in_more_files_than_an_in_list_takes(lib, tmp_path):
    n = 33000
    now = time.time()
    with lib.db.write() as c:
        fid = c.execute("INSERT INTO folders (path, added) VALUES (?, ?)", (str(tmp_path), now)).lastrowid
        c.executemany("INSERT INTO files (folder_id, rel, name, ext, kind, size, mtime, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?)",
                      [(fid, f"{i}.png", f"{i}.png", ".png", "image", 1, now, now, now) for i in range(n)])
        c.executemany("INSERT INTO prompt_tags (file_id, tag) VALUES (?, 'glow')", [(i,) for i in range(1, n + 1)])
    cid = lib.character_save(None, "Glow", [], [{"kind": "tag", "value": "glow"}])
    assert lib.character(cid)["rules"][0]["matches"] == n
    lib.character_review(cid, list(range(1, 11)), "reject")


# ---------------------------------------------------------------- requests that make no sense
def test_requests_that_make_no_sense_are_refused_not_crashed(lib, media):
    image(media / "x.png")
    scan.scan_folder(lib.db, lib.add_folder(str(media)))

    async def go(client):
        bad = [("delete", "/api/folders/abc", None), ("post", "/api/folders/abc/scan", None),
               ("post", "/api/ops/move", {"selection": [1], "folder": "abc"}),
               ("post", "/api/ops/bulk", {"selection": [1], "action": "rate", "value": [3]}),
               ("post", "/api/ops/bulk", {"selection": [2 ** 70], "action": "rate", "value": 3}),
               ("post", "/api/ops/99999999999999999999999/undo", None),
               ("get", "/api/files?offset=99999999999999999999999", None),
               ("post", "/api/characters/1/review", {"files": [987654], "verdict": "confirm"}),
               ("post", "/api/albums", {"name": ["x"]}),
               ("post", "/api/file/1/note", {"note": {"x": 1}})]
        out = []
        for method, path, body in bad:
            r = await getattr(client, method)(path, headers=H, json=body)
            out.append((path, r.status))
        return out
    for path, status in run(lib, go):
        assert 400 <= status < 500, (path, status)


def test_settings_that_cant_work_are_refused(lib, tmp_path):
    png = image(tmp_path / "only_one.png")

    async def go(client):
        out = {}
        for body in ({"ffmpeg": str(tmp_path / "nope.exe")}, {"ffmpeg": str(png)}, {"comfy_url": "not a url"},
                     {"ml_data": str(tmp_path / "nope")}, {"comfy_url": "127.0.0.1:8199"}):
            r = await client.post("/api/settings", headers=H, json=body)
            out[next(iter(body.values()))] = (r.status, (await r.json()).get("settings", {}).get("comfy_url"))
        return out
    out = run(lib, go)
    assert [s for s, _ in out.values()] == [422, 422, 422, 422, 200]
    assert out["127.0.0.1:8199"][1] == "http://127.0.0.1:8199"  # no scheme: http:// is meant
