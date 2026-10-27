"""Characters the Chrome extension leaves in Downloads\\Model Librarian Gallery when ComfyUI isn't running: filed into
Model Librarian's characters.json (a made-up one in a temp folder), under its lock, then removed."""
import base64
import json
import os
import threading
import time

import pytest

from mlgallery import charfiles, charstore

WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 24
POST = {"site": "danbooru", "post": "4242", "url": "https://danbooru.donmai.us/posts/4242", "post_count": 310, "solo": True}


def leave(folder, name, character, picture=WEBP, v=1):
    p = os.path.join(folder, name + charfiles.SUFFIX)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"v": v, "character": character, "picture": "data:image/webp;base64," + base64.b64encode(picture).decode() if picture else None}, f)
    return p


@pytest.fixture
def dirs(tmp_path):
    ml = tmp_path / "models" / ".sm_model_fetcher"
    ml.mkdir(parents=True)
    (ml / "registry.json").write_text("{}")
    ext = tmp_path / "Downloads" / "Model Librarian Gallery"
    ext.mkdir(parents=True)
    return str(ml), str(ext)


def chars(ml):
    with open(os.path.join(ml, charstore.FILE), encoding="utf-8") as f:
        return json.load(f)["characters"]


def test_files_waiting_characters_and_removes_them(dirs):
    ml, ext = dirs
    aurelia = {"tag": "aurelia_(lighthouse_tales)", "name": "Aurelia", "series": "Lighthouse Tales", "appearance": ["silver hair"],
               "outfit": {"name": "default", "tags": ["white dress"]}, "source": POST}
    a = leave(ext, "aurelia_(lighthouse_tales)", aurelia)
    assert charfiles.pickup(ml, ext) == 1
    assert not os.path.exists(a)
    [c] = chars(ml)
    assert c["name"] == "Aurelia" and c["outfits"] == {"default": ["white dress"]} and c["source"]["post"] == "4242"
    assert c["image"] == f"characters/{c['id']}.webp"
    with open(os.path.join(ml, *c["image"].split("/")), "rb") as f:
        assert f.read() == WEBP
    # the same character from another post: an outfit added, its picture kept
    leave(ext, "aurelia again", {**aurelia, "appearance": ["blue eyes"], "outfit": {"name": "winter", "tags": ["red coat"]}})
    assert charfiles.pickup(ml, ext) == 1
    [c2] = chars(ml)
    assert c2["id"] == c["id"] and c2["appearance"] == ["silver hair", "blue eyes"] and c2["outfits"]["winter"] == ["red coat"]
    assert os.listdir(os.path.join(ml, charstore.PICTURES)) == [f"{c['id']}.webp"]
    assert not os.path.exists(os.path.join(ml, charstore.FILE + ".lock"))


def test_waits_without_model_librarian_and_leaves_what_isnt_a_character(dirs):
    ml, ext = dirs
    good = leave(ext, "nova", {"tag": "nova_(starfall)", "name": "Nova"}, picture=None)
    assert charfiles.pickup(None, ext) == 0 and os.path.exists(good)
    bad = leave(ext, "odd", {"tag": "", "name": ""})
    newer = leave(ext, "future", {"tag": "x", "name": "X"}, v=2)
    with open(os.path.join(ext, "huge" + charfiles.SUFFIX), "w") as f:
        f.write(" " * (charfiles.FILE_MAX + 1))
    assert charfiles.pickup(ml, ext) == 1
    assert [c["name"] for c in chars(ml)] == ["Nova"] and chars(ml)[0]["image"] is None
    assert not os.path.exists(good) and os.path.exists(bad) and os.path.exists(newer)


def test_waits_for_model_librarians_lock(dirs):
    ml, ext = dirs
    lock = os.path.join(ml, charstore.FILE + ".lock")
    with open(lock, "w") as f:
        f.write("123")
    leave(ext, "nova", {"tag": "nova_(starfall)", "name": "Nova"})
    threading.Timer(0.4, os.remove, args=(lock,)).start()
    t = time.time()
    assert charfiles.pickup(ml, ext) == 1
    assert time.time() - t >= 0.3 and [c["name"] for c in chars(ml)] == ["Nova"]


def test_a_stale_lock_is_broken(dirs):
    ml, ext = dirs
    lock = os.path.join(ml, charstore.FILE + ".lock")
    with open(lock, "w") as f:
        f.write("123")
    old = time.time() - charfiles.STALE - 5
    os.utime(lock, (old, old))
    leave(ext, "nova", {"tag": "nova_(starfall)", "name": "Nova"})
    assert charfiles.pickup(ml, ext) == 1


def test_looks_in_the_extension_folders_added_in_settings(lib, tmp_path):
    other = tmp_path / "D" / "Model Librarian Gallery"
    other.mkdir(parents=True)
    lib.settings.update({"inbox_folders": [str(other), str(tmp_path / "Pictures")]})
    found = [os.path.normcase(p) for p in charfiles.folders(lib)]
    assert os.path.normcase(str(other)) in found and os.path.normcase(str(tmp_path / "Pictures")) not in found


def test_same_charstore_as_model_librarian():
    """charstore.py is one file in two repos: the Gallery's copy matches Model Librarian's (when it's beside this one)."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    theirs = os.path.join(os.path.dirname(here), "ComfyUI-Model-Librarian", "smf", "charstore.py")
    if not os.path.isfile(theirs):
        pytest.skip("ComfyUI-Model-Librarian isn't beside this repo")
    with open(theirs, "rb") as a, open(os.path.join(here, "mlgallery", "charstore.py"), "rb") as b:
        assert a.read() == b.read()
