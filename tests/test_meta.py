"""Reading what made each file, storing it, searching it, your marks and tags, and the folder watcher."""
import json
import os
import random
import shutil
import time

import pytest

from conftest import HAVE_FFMPEG, image
from mlgallery import metadata, scan, search
from sample_media import generation, make_image, make_video

HERE = os.path.dirname(os.path.abspath(__file__))
ML_CANDIDATES = [os.environ.get("MLG_ML_REPO") or "", os.path.join(HERE, "..", "..", "ComfyUI-Model-Librarian")]  # beside it in the suite folder


# ---------------------------------------------------------------- the shared readers
@pytest.mark.parametrize("name", ["imagemeta.py", "videometa.py", "recipe_parse.py"])
def test_readers_are_model_librarians(name):
    """The copies in mlgallery/meta must stay identical to Model Librarian's (set MLG_ML_REPO to its checkout)."""
    ml = next((os.path.join(c, "smf") for c in ML_CANDIDATES if c and os.path.isfile(os.path.join(c, "smf", name))), None)
    if not ml:
        pytest.skip("Model Librarian's checkout not found (set MLG_ML_REPO)")
    mine = open(os.path.join(HERE, "..", "mlgallery", "meta", name), encoding="utf-8").read()
    theirs = open(os.path.join(ml, name), encoding="utf-8").read()
    assert mine == theirs, f"mlgallery/meta/{name} differs from Model Librarian's: copy it over again"


# ---------------------------------------------------------------- one file
def sample(tmp_path, name, i=7, size=(320, 256), gen=True):
    rnd = random.Random(i)
    g = generation(i, rnd, size) if gen else None
    p = str(tmp_path / name)
    make_image(p, size, i, g)
    return p, g


def test_comfyui_png(tmp_path):
    p, g = sample(tmp_path, "a.png", i=7)  # i=7: two LoRAs
    got = metadata.parse(p, "image")
    s = g["graph"]["13"]["inputs"]
    assert got["source"] == "comfyui" and got["positive"] == g["graph"]["10"]["inputs"]["text"]
    assert (got["seed"], got["steps"], got["cfg"], got["sampler"], got["scheduler"]) == (s["seed"], s["steps"], s["cfg"], s["sampler_name"], s["scheduler"])
    assert (got["width"], got["height"]) == (320, 256) and got["nodes"] == len(g["workflow"]["nodes"])
    kinds = [(u["kind"], u["name"]) for u in got["uses"]]
    assert kinds[0] == ("checkpoint", "dreamy_mix_v3") and ("lora", "paper_cut_style_v2") in kinds and ("lora", "aurelia_character_v3") in kinds
    assert got["comfy"]["prompt"] == g["graph"] and got["comfy"]["workflow"] == g["workflow"]


def test_a1111_jpeg(tmp_path):
    p, g = sample(tmp_path, "a.jpg", i=3)
    got = metadata.parse(p, "image")
    assert got["source"] == "a1111" and got["positive"].startswith("a foggy pine forest") and "<lora:" not in got["positive"]
    assert got["negative"] == "blurry, lowres, watermark" and got["steps"] == 8 and got["model"] == "sample_xl_base"
    assert [u["name"] for u in got["uses"] if u["kind"] == "lora"] == ["paper_cut_style_v2", "aurelia_character_v3"]
    assert got["comfy"] is None


def test_no_metadata(tmp_path):
    p, _ = sample(tmp_path, "a.webp", gen=False)
    assert metadata.parse(p, "image") is None
    p2, _ = sample(tmp_path, "b.png", gen=False)
    assert metadata.parse(p2, "image") is None


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")
def test_video_helper_suite_comment(tmp_path):
    g = generation(5, random.Random(5), (480, 320))
    p = str(tmp_path / "v.mp4")
    assert make_video(p, 0, seconds=1, gen=g)
    got = metadata.parse(p, "video")
    assert got["source"] == "comfyui" and got["seed"] == g["graph"]["13"]["inputs"]["seed"] and got["comfy"]["workflow"]


def test_gallery_info_entry_is_cleaned_and_capped():
    raw = {"albums": "Coast, Coast,  Night ", "tags": [f"t{i}" for i in range(100)], "characters": ["A" * 500],
           "rating": "7", "favourite": "true", "note": "n" * 5000, "project": "P"}
    got = metadata.clean_info(raw)
    assert got["albums"] == ["Coast", "Night"] and len(got["tags"]) == 64 and len(got["characters"][0]) == 100
    assert got["rating"] is None and got["favourite"] is True and len(got["note"]) == 2048
    assert metadata.clean_info({"tags": "", "rating": 0}) is None


def test_gallery_info_falls_back_to_the_nodes_typed_values():
    graph = {"9": {"class_type": "MLGalleryInfo", "inputs": {"images": ["8", 0], "tags": "dusk, coast", "rating": 3, "characters": ["5", 0]}}}
    got = metadata.gallery_info({}, graph)
    assert got["tags"] == ["dusk", "coast"] and got["rating"] == 3 and got["characters"] == []  # a wired value isn't in the graph
    entry = {"mlgallery": json.dumps({"tags": ["from entry"]})}
    assert metadata.gallery_info(entry, graph)["tags"] == ["from entry"]  # the entry wins


# ---------------------------------------------------------------- the index
def indexed(lib, media, n=8, **kw):
    rnd = random.Random(1)
    now = time.time()
    names = []
    for i in range(n):
        ext = ".jpg" if i == 3 else ".webp" if i == 4 else ".png"
        p = str(media / f"s{i}{ext}")
        make_image(p, (256, 256), i, None if ext == ".webp" else generation(i, rnd, (256, 256)))
        os.utime(p, (now - i * 60, now - i * 60))
        names.append(os.path.basename(p))
    with lib.db.write() as c:
        fid = c.execute("INSERT INTO folders (path, added) VALUES (?, ?)", (str(media), now)).lastrowid
    scan.scan_folder(lib.db, {"id": fid, "path": str(media)})
    read_all(lib)
    return {r["name"]: r for r in lib.files(limit=100)["items"]}


def read_all(lib):
    with lib.db.read() as c:
        rows = c.execute("SELECT f.id, f.rel, f.kind, d.path FROM files f JOIN folders d ON d.id=f.folder_id WHERE f.meta=0").fetchall()
    for r in rows:
        lib.meta.read_one(r)


def q(lib, text):
    return sorted(r["name"] for r in lib.files(limit=100, q=text)["items"])


def test_store_and_info(lib, media):
    rows = indexed(lib, media)
    info = lib.info(rows["s0.png"]["id"])
    g = info["generation"]
    assert g["source"] == "comfyui" and g["has_workflow"] and g["nodes"] == 7
    assert g["info"]["albums"] == ["Coastline"]  # s0 carries a Gallery Info entry
    assert {t["name"] for t in info["tags"]} == {"sample", "paper cut style"} and all(t["source"] == "workflow" for t in info["tags"])
    assert info["rating"] == 4 and info["marks_source"] == "workflow"
    assert lib.info(rows["s4.webp"]["id"])["generation"] is None
    assert lib.files(q="")["items"][0]["meta"] in (1, 2)
    wf = lib.workflow(rows["s0.png"]["id"])
    assert wf["workflow"]["nodes"] and wf["prompt"]["13"]["class_type"] == "KSampler"


def test_search_filters(lib, media):
    indexed(lib, media)
    assert q(lib, "lighthouse") == ["s0.png"]
    assert q(lib, "lighth") == ["s0.png"]  # words are prefixes
    assert q(lib, "light") == ["s0.png", "s5.png", "s6.png", "s7.png"]  # lighthouse, lighting, soft light
    assert q(lib, '"paper cut"') == q(lib, "paper cut") != []
    assert q(lib, "lora:aurelia") == ["s2.png", "s3.jpg", "s6.png", "s7.png"]
    assert q(lib, "lora:aurelia lora:paper") == ["s3.jpg", "s7.png"]
    assert q(lib, "model:dreamy") == ["s1.png", "s7.png"]
    assert q(lib, "tag:sample") == ["s0.png"]
    assert q(lib, "rating:4") == ["s0.png"] and q(lib, "rating:0") == sorted(set(q(lib, "")) - {"s0.png"})
    assert q(lib, "no:prompt") == ["s4.webp"] and "s4.webp" not in q(lib, "has:prompt")
    assert q(lib, "has:workflow") == sorted(set(q(lib, "has:prompt")) - {"s3.jpg"})  # A1111 saves no workflow
    assert q(lib, "sampler:dpmpp*") == ["s1.png", "s7.png"] and q(lib, "sampler:dpmpp") == []  # exact, or a prefix with *
    assert q(lib, "sampler:euler") == ["s0.png", "s3.jpg", "s6.png"]  # not euler_ancestral
    assert q(lib, "steps:8") == ["s0.png", "s3.jpg", "s6.png"] and q(lib, "steps:>=20") == sorted(set(q(lib, "has:prompt")) - {"s0.png", "s3.jpg", "s6.png"})
    assert q(lib, "neg:watermark") == q(lib, "has:prompt") and q(lib, "watermark") == []  # the negative only when asked
    assert q(lib, "s5") == ["s5.png"]  # the file name
    assert q(lib, "ext:jpg") == ["s3.jpg"] and q(lib, "is:image") == q(lib, "")
    assert q(lib, "nothinglikethis") == [] and q(lib, "!!!") == []
    assert q(lib, '"') == q(lib, "")  # a stray quote is nothing typed
    assert q(lib, "bogus:thing") == []  # an unknown key is just text


def test_search_values_are_never_sql(lib, media):
    indexed(lib, media, n=2)
    for nasty in ["model:'; DROP TABLE files; --", 'tag:"x" OR 1=1', "lora:%", "lora:_", 'NEAR(a b)', "a* OR b", "seed:1;2", "^ $ ( ) :"]:
        lib.files(q=nasty)
    assert lib.files()["total"] == 2
    assert q(lib, "lora:%") == []  # % is a character here, not a wildcard


def test_tokens():
    assert search.tokens('model:"Sample XL" dusk -fog tag:a') == [("model", "Sample XL"), (None, "dusk"), (None, "-fog"), ("tag", "a")]
    assert search.tokens("http://x") == [(None, "http://x")]


def test_marks_yours_win_over_the_workflow(lib, media):
    rows = indexed(lib, media)
    fid = rows["s0.png"]["id"]
    assert lib.mark(fid, favorite=True) == {"rating": 4, "favorite": 1}
    assert lib.mark(fid, rating=0) == {"rating": 0, "favorite": 1}
    with lib.db.write() as c:  # the file is read again (changed on disk): the workflow's rating doesn't come back
        c.execute("UPDATE files SET meta=0 WHERE id=?", (fid,))
    read_all(lib)
    assert lib.info(fid)["rating"] == 0 and lib.info(fid)["favorite"] == 1
    assert q(lib, "is:fav") == ["s0.png"]
    with pytest.raises(KeyError):
        lib.mark(99999, rating=3)


def test_tags_add_remove_and_workflow_tags_stay_removed(lib, media):
    rows = indexed(lib, media)
    fid = rows["s0.png"]["id"]
    tags = lib.set_tags(fid, add=["Night", "night", "keeper"], remove=["sample"])
    assert {t["name"] for t in tags} == {"Night", "keeper", "paper cut style"}
    assert q(lib, "tag:night") == ["s0.png"] and q(lib, "keeper") == ["s0.png"]  # tags are searchable as words too
    with lib.db.write() as c:
        c.execute("UPDATE files SET meta=0 WHERE id=?", (fid,))
    read_all(lib)
    assert "sample" not in {t["name"] for t in lib.info(fid)["tags"]}  # removed by you: re-reading doesn't put it back
    lib.set_tags(fid, add=["sample"])
    assert "sample" in {t["name"] for t in lib.info(fid)["tags"]}
    lib.set_tags(fid, remove=["keeper"])
    assert lib.tags("keep") == []  # an unused tag goes


def test_suggest_and_recent(lib, media):
    rows = indexed(lib, media)
    assert [s["name"] for s in lib.suggest("lora", "aur")] == ["aurelia_character_v3"]
    assert lib.suggest("model", "")[0]["n"] >= 2
    assert {s["name"] for s in lib.suggest("sampler", "")} == {"euler", "dpmpp_2m", "euler_ancestral"}
    assert lib.suggest("tag", "pa")[0]["name"] == "paper cut style"
    assert lib.suggest("nonsense", "x") == []
    lib.viewed(rows["s2.png"]["id"])
    time.sleep(0.01)
    lib.viewed(rows["s5.png"]["id"])
    assert [r["name"] for r in lib.files(sort="viewed")["items"]] == ["s5.png", "s2.png"]


def test_a_changed_file_is_read_again(lib, media):
    rows = indexed(lib, media, n=2)
    p = str(media / "s1.png")
    make_image(p, (256, 256), 1, generation(6, random.Random(6), (256, 256)))
    os.utime(p, (time.time() + 10, time.time() + 10))
    with lib.db.read() as c:
        folder = dict(c.execute("SELECT * FROM folders").fetchone())
    scan.scan_folder(lib.db, folder)
    read_all(lib)
    assert lib.info(rows["s1.png"]["id"])["generation"]["positive"].startswith("rolling hills")


def test_an_unreadable_file_is_marked_not_fatal(lib, media):
    p = media / "broken.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\xff" * 100)
    with lib.db.write() as c:
        fid = c.execute("INSERT INTO folders (path, added) VALUES (?, ?)", (str(media), time.time())).lastrowid
    scan.scan_folder(lib.db, {"id": fid, "path": str(media)})
    read_all(lib)
    assert lib.files()["items"][0]["meta"] in (2, -1)


# ---------------------------------------------------------------- the folder watcher
def wait_for(cond, timeout=10):
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_watcher_picks_up_new_changed_moved_and_deleted_files(lib, media):
    image(media / "first.png")
    lib.start(rescan=False)
    try:
        f = lib.add_folder(str(media))
        assert wait_for(lambda: lib.files()["total"] == 1)
        assert lib.watcher.watching(f["id"])
        t0 = time.time()
        image(media / "new" / "second.png", seed=2)
        assert wait_for(lambda: lib.files()["total"] == 2), "a new file didn't appear"
        assert time.time() - t0 < 6  # within seconds, as the plan promises
        os.makedirs(media / "sorted", exist_ok=True)
        shutil.move(str(media / "new" / "second.png"), str(media / "sorted" / "second.png"))
        assert wait_for(lambda: "sorted/second.png" in {r["rel"] for r in lib.files()["items"]})
        assert lib.files()["total"] == 2  # moved, not added twice
        os.remove(media / "first.png")
        assert wait_for(lambda: lib.files()["total"] == 1)
        (media / ".hidden").mkdir()
        image(media / ".hidden" / "no.png", seed=3)
        time.sleep(2.5)
        assert lib.files()["total"] == 1  # hidden folders stay out, watched or not
        lib.remove_folder(f["id"])
        assert not lib.watcher.watching(f["id"])
    finally:
        lib.stop()
