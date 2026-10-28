"""Characters kept in step with Model Librarian's list (charsync.py), both ways: a made-up list in a temp folder, never
Model Librarian's own. New on either side, linked by name, each part's latest change winning, rules added once, deletions
reaching the other side, a list that lost one, people left out, a picked portrait as the list's picture, and the API."""
import asyncio
import json
import os

import pytest
from aiohttp.test_utils import TestClient, TestServer

from mlgallery import charstore, charsync
from mlgallery.server import make_app
from test_characters import library

AURELIA = {"id": "chr_a0000001", "name": "Aurelia", "series": "Lighthouse Tales", "tag": "aurelia_(lighthouse_tales)", "aliases": [],
           "appearance": ["silver hair", "blue eyes"], "outfits": {"default": ["white dress"]},
           "lora": {"name": "chars/aurelia_character_v3.safetensors", "trigger": ["aurelia_lt"]}, "image": None,
           "source": {"site": "danbooru", "post": "4242", "url": "https://danbooru.donmai.us/posts/4242"},
           "created": 100.0, "updated": 100.0, "changed": {p: 100.0 for p in charstore.PARTS}}


@pytest.fixture
def ml(tmp_path):
    d = tmp_path / "models" / ".sm_model_fetcher"
    d.mkdir(parents=True)
    (d / "registry.json").write_text("{}")
    return str(d)


def put(ml, characters, deleted=()):
    with open(os.path.join(ml, charstore.FILE), "w", encoding="utf-8") as f:
        json.dump({"v": 1, "characters": characters, "deleted": list(deleted)}, f)


def listed(ml):
    with open(os.path.join(ml, charstore.FILE), encoding="utf-8") as f:
        return json.load(f)


def gallery(lib, name):
    with lib.db.read() as c:  # (not lib.characters(): it answers from a cache first)
        r = c.execute("SELECT id, name FROM characters WHERE name=?", (name,)).fetchone()
    return dict(r) if r else None


def rules(lib, cid):
    return sorted((r["kind"], r["value"], r["strength"]) for r in lib.character(cid)["rules"])


def test_from_the_list_into_the_gallery_with_its_rules(lib, ml):
    put(ml, [dict(AURELIA)])
    r = charsync.sync(lib, ml, now=200.0)
    g = gallery(lib, "Aurelia")
    assert g and r["gallery"] == 1
    ch = lib.character(g["id"])
    assert ch["shared"]["linked"] and ch["shared"]["tag"] == "aurelia_(lighthouse_tales)" and ch["shared"]["outfits"] == {"default": ["white dress"]}
    assert rules(lib, g["id"]) == [("word", "aurelia (lighthouse tales)", "strong")], "its tag as a word; its LoRA adds no rule"
    # nothing changed: a second sync does nothing
    assert charsync.sync(lib, ml, now=210.0) == {"list": False, "gallery": 0}
    # a rule you remove stays out
    lib.character_save(g["id"], "Aurelia", [], [])
    charsync.sync(lib, ml, now=220.0)
    assert rules(lib, g["id"]) == []


def test_from_the_gallery_into_the_list_and_linked_by_name(lib, ml):
    put(ml, [dict(AURELIA)])
    mine = lib.character_save(None, "Aurelia", ["Aury"], [{"kind": "lora", "value": "aurelia_character"}])  # yours already, by name
    nova = lib.character_save(None, "Nova", [], [])
    person = lib.character_save(None, "Grandma", [], [], who="person")
    charsync.sync(lib, ml, now=200.0)
    data = listed(ml)
    names = sorted(c["name"] for c in data["characters"])
    assert names == ["Aurelia", "Nova"], "people stay in the Gallery"
    a = next(c for c in data["characters"] if c["name"] == "Aurelia")
    assert a["id"] == "chr_a0000001" and a["appearance"] == ["silver hair", "blue eyes"] and a["aliases"] == ["Aury"], "linked: the list's presets kept, your aliases joined"
    assert lib.character(mine)["shared"]["linked"] and lib.character(nova)["shared"]["linked"] and not lib.character(person)["shared"]["linked"]
    with lib.db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 3, "no second Aurelia"


def test_linked_by_name_in_either_order(lib, ml):
    mine = lib.character_save(None, "Lumen Vale", [], [])  # yours, given name first
    put(ml, [dict(AURELIA, id="chr_u0000001", name="Vale Lumen", tag="vale_lumen", series="Lighthouse Tales")])  # Danbooru's, family name first
    charsync.sync(lib, ml, now=200.0)
    with lib.db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 1, "one character, not two"
    ch = lib.character(mine)
    assert ch["name"] == "Lumen Vale" and ch["shared"]["tag"] == "vale_lumen"
    assert rules(lib, mine) == [("word", "vale lumen", "strong")]


def test_each_part_keeps_its_latest_change(lib, ml):
    put(ml, [dict(AURELIA)])
    charsync.sync(lib, ml, now=200.0)
    cid = gallery(lib, "Aurelia")["id"]
    lib.character_presets(cid, {"appearance": ["silver hair", "blue eyes", "freckles"], "series": "Lighthouse Tales"})  # (the series unchanged: not stamped)
    lib.character_save(cid, "Aurelia Vance", [], None)  # renamed here
    data = listed(ml)  # and meanwhile, in Model Librarian, a new outfit
    sc = data["characters"][0]
    sc["outfits"]["winter"] = ["red coat"]
    charstore.stamp(sc, ["outfits"], 9e12)
    put(ml, data["characters"])
    charsync.sync(lib, ml)
    sc = listed(ml)["characters"][0]
    assert sc["name"] == "Aurelia Vance" and "freckles" in sc["appearance"] and sc["outfits"]["winter"] == ["red coat"]
    ch = lib.character(cid)
    assert ch["name"] == "Aurelia Vance" and ch["shared"]["outfits"]["winter"] == ["red coat"] and "freckles" in ch["shared"]["appearance"]


def test_deleting_reaches_the_other_side(lib, ml):
    put(ml, [dict(AURELIA), {**AURELIA, "id": "chr_b0000002", "name": "Nova", "tag": "nova"}])
    charsync.sync(lib, ml, now=200.0)
    assert lib.character_delete(gallery(lib, "Aurelia")["id"])
    charsync.sync(lib, ml, now=300.0)
    data = listed(ml)
    assert [c["name"] for c in data["characters"]] == ["Nova"] and charstore.deleted_ids(data) == {"chr_a0000001"}
    with lib.db.read() as c:
        assert not c.execute("SELECT * FROM characters_deleted").fetchall(), "the list knows: forgotten here"
    # deleted from the list: deleted here
    charstore.delete(data, "chr_b0000002", 400.0)
    put(ml, data["characters"], data["deleted"])
    charsync.sync(lib, ml, now=500.0)
    assert gallery(lib, "Nova") is None and gallery(lib, "Aurelia") is None


def test_a_list_that_lost_one_gets_it_back(lib, ml):
    put(ml, [dict(AURELIA)])
    charsync.sync(lib, ml, now=200.0)
    put(ml, [])  # (a new, empty list: no deletion recorded)
    charsync.sync(lib, ml, now=300.0)
    assert [c["id"] for c in listed(ml)["characters"]] == ["chr_a0000001"] and gallery(lib, "Aurelia")


def test_a_name_another_of_yours_has_stays_yours(lib, ml):
    put(ml, [dict(AURELIA)])
    charsync.sync(lib, ml, now=200.0)
    lib.character_save(None, "Nova", [], [])
    charsync.sync(lib, ml, now=210.0)
    data = listed(ml)
    a = next(c for c in data["characters"] if c["id"] == "chr_a0000001")
    a["name"] = "Nova"
    charstore.stamp(a, ["name"], 9e12)
    put(ml, data["characters"])
    charsync.sync(lib, ml)
    assert gallery(lib, "Aurelia") and gallery(lib, "Nova")
    assert next(c for c in listed(ml)["characters"] if c["id"] == "chr_a0000001")["name"] == "Aurelia"


def test_a_picked_portrait_becomes_the_lists_picture(lib, media, ml):
    ids = library(lib, media, n=4)
    put(ml, [dict(AURELIA)])
    charsync.sync(lib, ml, now=200.0)
    cid = gallery(lib, "Aurelia")["id"]
    lib.character_save(cid, "Aurelia", [], None, cover=ids["c01.png"])
    charsync.sync(lib, ml)
    sc = listed(ml)["characters"][0]
    assert sc["image"] == "characters/chr_a0000001.webp"
    with open(os.path.join(ml, "characters", "chr_a0000001.webp"), "rb") as f:
        assert f.read(4) == b"RIFF"
    lib.models.data_dir = lambda: ml
    assert lib.character_picture(cid).endswith("chr_a0000001.webp")
    assert charsync.sync(lib, ml)["list"] is False, "sent once"


def test_presets_api(lib, ml):
    lib.models.data_dir = lambda: ml
    put(ml, [dict(AURELIA)])
    charsync.sync(lib, ml, now=200.0)
    cid = gallery(lib, "Aurelia")["id"]

    async def go():
        c = TestClient(TestServer(make_app(lib, "t0k")))
        await c.start_server()
        try:
            hdr = {"X-MLG-Token": "t0k"}
            r = await c.post(f"/api/characters/{cid}/presets", json={"tag": "aurelia vance", "outfits": {"winter": ["red coat", "scarf"]}}, headers=hdr)
            d = await r.json()
            assert r.status == 200 and d["shared"]["tag"] == "aurelia_vance" and d["shared"]["outfits"] == {"winter": ["red coat", "scarf"]}
            r = await c.post(f"/api/characters/{cid}/presets", json={"outfits": ["no"]}, headers=hdr)
            assert r.status == 422
            r = await c.get(f"/api/characters/{cid}/picture", headers=hdr)
            assert r.status == 404, "no picture yet"
        finally:
            await c.close()
    asyncio.run(go())
    charsync.sync(lib, ml)
    sc = listed(ml)["characters"][0]
    assert sc["tag"] == "aurelia_vance" and sc["outfits"] == {"winter": ["red coat", "scarf"]}
    assert ("word", "aurelia vance", "strong") in rules(lib, cid), "a new tag: its word rule added"


def test_linking_a_lora_on_the_prompt_card(lib, ml):
    put(ml, [dict(AURELIA, lora=None)])
    charsync.sync(lib, ml, now=200.0)
    cid = gallery(lib, "Aurelia")["id"]
    lib.models.models = lambda: {"root": None, "models": [{"kind": "lora", "name": "aurelia_v2", "file": "chars/aurelia_v2.safetensors"},
                                                          {"kind": "checkpoint", "name": "base", "file": "base.safetensors"}]}
    lib.trigger_words = lambda lora, guess=True: ["aurelia_lt", "silver hair"] if not guess else ["guessed"]
    assert lib.character_loras() == [{"name": "aurelia_v2", "file": "chars/aurelia_v2.safetensors"}]
    ch = lib.character_presets(cid, {"lora": {"name": r"Chars\aurelia_v2.safetensors"}})  # (\ and any case: as Model Librarian has it)
    assert ch["shared"]["lora"] == {"name": "chars/aurelia_v2.safetensors", "trigger": ["aurelia_lt", "silver hair"]}, "its trigger words from its metadata"
    ch = lib.character_presets(cid, {"lora": {"name": "chars/aurelia_v2.safetensors", "trigger": ["aurelia_lt", "AURELIA_LT", " "]}})
    assert ch["shared"]["lora"]["trigger"] == ["aurelia_lt"], "trigger words you kept, as trained"
    with pytest.raises(ValueError):
        lib.character_presets(cid, {"lora": {"name": "not_there.safetensors"}})
    charsync.sync(lib, ml)
    assert listed(ml)["characters"][0]["lora"] == {"name": "chars/aurelia_v2.safetensors", "trigger": ["aurelia_lt"]}, "in the list: Character Select loads it"
    assert not any(r[0] == "lora" for r in rules(lib, cid)), "linking a LoRA adds no rule (you chose to keep them apart)"
    assert lib.character_presets(cid, {"lora": None})["shared"]["lora"] is None


def test_learning_a_look_from_your_images(lib, media):
    from mlgallery import looks
    assert looks.classify("silver_hair") == "appearance" and looks.classify("hair ribbon") == "outfit" and looks.classify("smile") == "skip"
    got = looks.suggest({"silver hair": 9, "white dress": 6, "smile": 8, "lighthouse": 9, "blue eyes": 2, "1girl": 10, "long hair": 9}, 10, known=["Long_Hair"])
    assert got == [{"tag": "1girl", "kind": "appearance", "share": 1.0}, {"tag": "silver hair", "kind": "appearance", "share": 0.9},
                   {"tag": "white dress", "kind": "outfit", "share": 0.6}], "appearance and outfit tags common enough, ones it has left out"
    assert looks.suggest({"silver hair": 2}, 2) == [], "too few images to say"
    library(lib, media)
    cid = lib.character_save(None, "Aurelia", [], [{"kind": "lora", "value": "aurelia_character"}])
    with lib.db.write() as c:
        files = [r[0] for r in c.execute("SELECT file_id FROM file_characters WHERE character_id=?", (cid,))]
        c.execute("DELETE FROM prompt_tags WHERE file_id IN (%s)" % ",".join("?" * len(files)), files)
        c.executemany("INSERT INTO prompt_tags (file_id, tag) VALUES (?, ?)", [(f, t) for f in files for t in ("silver hair", "red scarf", "smile")])
    r = lib.character_learn(cid)
    assert r["n"] == len(files) and [(t["tag"], t["kind"]) for t in r["tags"]] == [("red scarf", "outfit"), ("silver hair", "appearance")]
    lib.character_presets(cid, {"appearance": ["silver hair"]})
    assert [t["tag"] for t in lib.character_learn(cid)["tags"]] == ["red scarf"], "one it has is no longer suggested"


# ---------------------------------------------------------------- QA: nothing goes back and forth; what can't be synced is said
def version_after(lib, ml, n=3):
    out = []
    for _ in range(n):
        charsync.sync(lib, ml)
        out.append(lib.db.version)
    return out


def test_what_the_gallery_cant_hold_doesnt_go_back_and_forth(lib, ml):
    lib.character_save(None, "Alice", [], [], who="person")
    many = [f"aka {i}" for i in range(20)]
    put(ml, [dict(AURELIA, name="Alice"), dict(AURELIA, id="chr_b0000002", name="Nova", tag="nova", aliases=many)])
    charsync.sync(lib, ml, now=200.0)
    a, b, c = version_after(lib, ml)
    assert a == b == c, "a settled sync writes nothing"
    names = sorted(x["name"] for x in listed(ml)["characters"])
    assert names == ["Alice 2", "Nova"], "a person's name is taken: the character gets another, and the list is told"
    assert len(next(x for x in listed(ml)["characters"] if x["name"] == "Nova")["aliases"]) == 16


def test_a_portrait_that_cant_be_made_is_tried_once(lib, media, ml):
    ids = library(lib, media, n=2)
    put(ml, [dict(AURELIA)])
    charsync.sync(lib, ml, now=200.0)
    cid = gallery(lib, "Aurelia")["id"]
    lib.character_save(cid, "Aurelia", [], None, cover=ids["c01.png"])
    lib.thumbs.get = lambda *a, **k: None  # (its thumbnail can't be made)
    charsync.sync(lib, ml)
    a, b, c = version_after(lib, ml)
    assert a == b == c and listed(ml)["characters"][0]["image"] is None


def test_a_character_made_a_person_leaves_the_list(lib, ml):
    put(ml, [dict(AURELIA)])
    charsync.sync(lib, ml, now=200.0)
    cid = gallery(lib, "Aurelia")["id"]
    lib.character_save(cid, "Aurelia", [], None, who="person")
    charsync.sync(lib, ml)
    assert listed(ml)["characters"] == [] and charstore.deleted_ids(listed(ml)) == {"chr_a0000001"}
    lib.character_save(cid, "Aurelia Vance", [], None)
    charsync.sync(lib, ml)
    assert listed(ml)["characters"] == [] and gallery(lib, "Aurelia Vance"), "no copy comes back"


def test_an_edit_while_syncing_isnt_undone(lib, ml):
    put(ml, [dict(AURELIA)])
    charsync.sync(lib, ml, now=200.0)
    cid = gallery(lib, "Aurelia")["id"]
    data = listed(ml)
    data["characters"][0]["series"] = "Lighthouse Tales II"
    charstore.stamp(data["characters"][0], ["series"], 9e12)
    rows, gone, people = charsync._rows(lib)
    _, acts = charsync.plan(data, rows, gone, 9e12 + 1, people)
    lib.character_presets(cid, {"appearance": ["freckles"]})  # (meanwhile, on the card)
    lib.apply_character_sync(acts)
    assert lib.character(cid)["shared"]["appearance"] == ["freckles"], "the edit made meanwhile stays (the next sync merges both)"


def test_a_broken_or_newer_list_stops_the_sync_and_says_why(lib, ml):
    for content, why in (("", "isn't readable"), ('{"v": 2, "characters": []}', "newer")):
        with open(os.path.join(ml, charstore.FILE), "w", encoding="utf-8") as f:
            f.write(content)
        with pytest.raises(ValueError, match=why):
            charsync.sync(lib, ml)
    with open(os.path.join(ml, charstore.FILE), "w", encoding="utf-8-sig") as f:  # (a byte-order mark is fine)
        json.dump({"v": 1, "characters": [dict(AURELIA), None]}, f)
    charsync.sync(lib, ml)
    assert gallery(lib, "Aurelia")


def test_a_library_only_for_photos_keeps_out(lib, ml):
    put(ml, [dict(AURELIA)])
    lib.set_kinds(["photos"])
    lib.models.data_dir = lambda: ml
    assert lib.sync_characters() == {"list": False, "gallery": 0} and gallery(lib, "Aurelia") is None


def test_two_of_a_name_in_other_series_stay_two(lib, ml):
    mine = lib.character_save(None, "Rin (Fate)", [], [])
    put(ml, [dict(AURELIA, id="chr_r0000001", name="Rin (Vocaloid)", tag="rin_(vocaloid)", series="Vocaloid")])
    charsync.sync(lib, ml, now=200.0)
    with lib.db.read() as c:
        names = sorted(r[0] for r in c.execute("SELECT name FROM characters"))
    assert names == ["Rin (Fate)", "Rin (Vocaloid)"] and lib.character(mine)["shared"]["tag"] == ""
