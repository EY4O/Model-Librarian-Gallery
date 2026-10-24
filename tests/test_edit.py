"""Flip, rotate and crop from the viewer: what the file carries survives, its date stays, Undo puts it back."""
import json
import os
import random

import pytest
from PIL import Image

from mlgallery import edit, fileops, scan
from sample_media import generation, make_image


def one(lib, media, name, size=(300, 200), gen=True):
    p = str(media / name)
    make_image(p, size, 7, generation(3, random.Random(3), size) if gen else None)
    os.utime(p, (1_700_000_000, 1_700_000_000))
    f = lib.add_folder(str(media))
    scan.scan_folder(lib.db, f)
    return p, lib.files()["items"][0]["id"]


def test_rotate_keeps_comfyuis_prompt_and_workflow_and_the_date(lib, media):
    p, fid = one(lib, media, "gen.png")
    before = open(p, "rb").read()
    with Image.open(p) as im:
        prompt, workflow = im.info["prompt"], im.info["workflow"]
    lib.mark(fid, rating=4)
    r = edit.apply(lib.db, fid, rotate=90)
    with Image.open(p) as im:
        assert im.size == (200, 300)  # turned a quarter
        assert im.info["prompt"] == prompt and json.loads(im.info["workflow"]) == json.loads(workflow)
    assert os.path.getmtime(p) == 1_700_000_000  # where it was in the grid
    info = lib.info(fid)
    assert info["rating"] == 4 and (info["width"], info["height"]) == (200, 300)
    fileops.undo(lib.db, r["op"])
    assert open(p, "rb").read() == before and lib.info(fid)["width"] == 300
    assert not (media / fileops.TRASH).exists()  # the kept original went back: nothing left behind


def test_flip_then_crop_matches_what_the_viewer_showed(lib, media):
    p, fid = one(lib, media, "plain.png", gen=False)
    with Image.open(p) as im:
        want = im.transpose(Image.Transpose.FLIP_LEFT_RIGHT).crop((10, 20, 110, 70)).convert("RGB")
    edit.apply(lib.db, fid, flip=True, crop=[10, 20, 100, 50])
    with Image.open(p) as im:
        assert im.size == (100, 50) and im.convert("RGB").tobytes() == want.tobytes()


def test_a_jpeg_keeps_a1111s_parameters(lib, media):
    p, fid = one(lib, media, "a1111.jpg")
    with Image.open(p) as im:
        before = im.getexif().get_ifd(0x8769)[0x9286]
    edit.apply(lib.db, fid, rotate=180)
    with Image.open(p) as im:
        assert im.getexif().get_ifd(0x8769)[0x9286] == before


def test_save_as_copy_leaves_the_original(lib, media):
    p, fid = one(lib, media, "gen.png")
    before = open(p, "rb").read()
    r = edit.apply(lib.db, fid, crop=[0, 0, 150, 100], copy=True)
    assert r["name"] == "gen (edited).png" and open(p, "rb").read() == before
    copy = media / "gen (edited).png"
    with Image.open(copy) as im:
        assert im.size == (150, 100) and "workflow" in im.info
    assert os.path.getmtime(copy) == 1_700_000_000 and lib.files()["total"] == 2
    fileops.undo(lib.db, r["op"])  # the copy goes to the Trash
    assert not copy.exists() and lib.files()["total"] == 1 and lib.status()["trash"] == 1


def test_what_cant_be_edited(lib, media):
    p, fid = one(lib, media, "gen.png")
    for kw in ({}, {"rotate": 45}, {"crop": [1, 2]}):
        with pytest.raises(fileops.OpError):
            edit.apply(lib.db, fid, **kw)


def test_emptying_the_trash_clears_the_kept_originals(lib, media, monkeypatch):
    p, fid = one(lib, media, "gen.png")
    r = edit.apply(lib.db, fid, rotate=90)
    assert next(o for o in fileops.history(lib.db) if o["id"] == r["op"])["undoable"]
    sent = []
    import send2trash
    monkeypatch.setattr(send2trash, "send2trash", lambda path: (sent.append(path), __import__("shutil").rmtree(path)))  # a stand-in: never your Recycle Bin
    fileops.empty(lib.db, [])  # emptying chosen files leaves them
    assert (media / fileops.TRASH / "edits").is_dir() and not sent
    out = fileops.empty(lib.db)
    assert out["backups"] == 1 and sent and not (media / fileops.TRASH).exists()
    assert not next(o for o in fileops.history(lib.db) if o["id"] == r["op"])["undoable"]
    with pytest.raises(fileops.OpError, match="isn't kept"):
        fileops.undo(lib.db, r["op"])
    assert os.path.isfile(p)  # the edited file itself stays
