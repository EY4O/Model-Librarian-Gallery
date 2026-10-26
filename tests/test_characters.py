"""Characters: rules, automatic matching, the review list, what you confirm or reject, and the API."""
import asyncio
import json
import random
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

from mlgallery import scan
from mlgallery.server import make_app
from sample_media import generation, make_image
from test_meta import q, read_all


def library(lib, media, n=16):
    rnd = random.Random(5)
    now = time.time()
    for i in range(n):
        p = str(media / f"c{i:02d}.png")
        make_image(p, (128, 128), i, generation(i, rnd, (128, 128)))
        import os
        os.utime(p, (now - i * 3600, now - i * 3600))
    with lib.db.write() as c:
        fid = c.execute("INSERT INTO folders (path, added) VALUES (?, ?)", (str(media), now)).lastrowid
    scan.scan_folder(lib.db, {"id": fid, "path": str(media)})
    read_all(lib)
    return {r["name"]: r["id"] for r in lib.files(limit=100)["items"]}


AURELIA = [i for i in range(16) if (i >> 1) & 1]  # the samples with aurelia_character_v3 (bit 1)
SOFT = [i for i in range(16) if (i >> 2) & 1 and not ((i & 1) and (i >> 1) & 1)]  # soft_light_v1, unless two LoRAs came first


def names(idx):
    return sorted(f"c{i:02d}.png" for i in idx)


def test_a_lora_rule_matches_at_once(lib, media):
    ids = library(lib, media)
    cid = lib.character_save(None, "Aurelia", ["Aury"], [{"kind": "lora", "value": "aurelia_character"}])
    ch = lib.character(cid)
    assert ch["n"] == len(AURELIA) and ch["counts"]["review"] == 0
    assert q(lib, "character:aurelia") == names(AURELIA) and q(lib, "character:aury") == names(AURELIA)  # by alias too
    assert ch["rules"][0]["matches"] == len(AURELIA) and ch["rules"][0]["strength"] == "strong"
    assert ch["loras"][0]["name"] == "aurelia_character_v3" and len(ch["strengths"]) == len(AURELIA)
    assert ch["cover"] in ids.values() and ch["models"]
    assert sum(m["n"] for m in ch["timeline"]) == ch["n"] and len(ch["timeline"]) <= 2  # per month, not per file
    assert [c["name"] for c in lib.info(ids[names(AURELIA)[0]])["characters"]] == ["Aurelia"]


def test_a_word_rule_reads_the_prompt(lib, media):
    library(lib, media)
    cid = lib.character_save(None, "Aurelia", [], [{"kind": "word", "value": "aurelia"}])
    assert lib.character(cid)["n"] == len(AURELIA)  # the samples put the trigger word in the prompt
    cid2 = lib.character_save(None, "Lake", [], [{"kind": "word", "value": "mountain lake"}])
    assert q(lib, "character:lake") == names([i for i in range(16) if i % 8 == 1])


def test_tag_rules_wait_for_review_and_decisions_stick(lib, media):
    ids = library(lib, media)
    cid = lib.character_save(None, "Glow", [], [{"kind": "tag", "value": "soft light"}])  # a tag rule is weak by default
    ch = lib.character(cid)
    waiting = [r["file_id"] for r in lib.review_queue(cid)]
    assert ch["n"] == 0 and len(waiting) == ch["counts"]["review"] > 0
    assert q(lib, "review:glow") == q(lib, "is:review") and q(lib, "character:glow") == []
    yes, no = waiting[0], waiting[1]
    lib.character_review(cid, [yes], "confirm")
    lib.character_review(cid, [no], "reject")
    assert lib.character(cid)["n"] == 1
    lib.character_save(cid, "Glow", [], [{"kind": "tag", "value": "soft light"}])  # rules saved again: decisions stay
    assert lib.character(cid)["counts"]["confirmed"] == 1 and lib.character(cid)["counts"]["rejected"] == 1
    assert no not in [r["file_id"] for r in lib.review_queue(cid)]
    # a strong rule never overrides a rejection
    lib.character_save(cid, "Glow", [], [{"kind": "tag", "value": "soft light", "strength": "strong"}])
    assert "Glow" not in [c["name"] for c in lib.info(no)["characters"]]
    # forgetting a decision lets the rules decide again
    lib.character_reset(cid, no)
    assert "Glow" in [c["name"] for c in lib.info(no)["characters"]]


def test_a_weak_rule_you_keep_rejecting_goes_quiet(lib, media):
    library(lib, media, n=16)
    cid = lib.character_save(None, "Night", [], [{"kind": "tag", "value": "sample"}])  # every file with a Gallery Info entry
    with lib.db.write() as c:  # make the tag common: every file gets it
        pass
    for fid in lib.files(limit=100)["items"]:
        lib.set_tags(fid["id"], add=["noisy"])
    lib.character_save(cid, "Night", [], [{"kind": "tag", "value": "noisy"}])
    queue = [r["file_id"] for r in lib.review_queue(cid)]
    assert len(queue) == 16
    lib.character_review(cid, queue[:5], "reject")
    ch = lib.character(cid)
    assert ch["rules"][0]["muted"] and ch["counts"]["review"] == 0  # no more suggestions from it
    lib.character_save(cid, "Night", [], [{"kind": "tag", "value": "noisy", "muted": False}])
    assert lib.character(cid)["counts"]["review"] == 11  # turned back on: the ones you didn't reject


def test_the_gallery_info_node_confirms_by_name(lib, media):
    ids = library(lib, media)
    # samples i%4==0 with an aurelia LoRA name Aurelia in their Gallery Info entry; none of 0..15 qualify, so add one
    with lib.db.write() as c:
        c.execute("UPDATE generations SET info=? WHERE file_id=?", (json.dumps({"characters": ["Aury"], "tags": []}), ids["c05.png"]))
    with lib.db.read() as c:
        assert {s["name"] for s in __import__("mlgallery.characters", fromlist=["x"]).suggestions(c)} == {"Aury"}
    cid = lib.character_save(None, "Aurelia", ["aury"], [])  # no rules: the workflow's name alone
    assert lib.info(ids["c05.png"])["characters"] == [{"id": cid, "name": "Aurelia", "state": "confirmed", "source": "workflow"}]
    assert lib.characters()["suggestions"] == []


def test_new_files_are_matched_as_they_arrive(lib, media):
    library(lib, media, n=4)
    cid = lib.character_save(None, "Aurelia", [], [{"kind": "lora", "value": "aurelia"}])
    before = lib.character(cid)["n"]
    p = str(media / "late.png")
    make_image(p, (64, 64), 9, generation(6, random.Random(9), (64, 64)))  # i=6 uses the aurelia LoRA
    with lib.db.read() as c:
        folder = dict(c.execute("SELECT * FROM folders").fetchone())
    scan.scan_folder(lib.db, folder)
    read_all(lib)
    assert lib.character(cid)["n"] == before + 1


def test_names_must_be_unique_and_present(lib, media):
    lib.character_save(None, "Aurelia")
    with pytest.raises(ValueError, match="already"):
        lib.character_save(None, "aurelia")
    with pytest.raises(ValueError, match="name"):
        lib.character_save(None, "  ")
    with pytest.raises(KeyError):
        lib.character_save(999, "X")


def test_trigger_words_from_the_prompts(lib, media):
    library(lib, media)
    assert "aurelia" in lib.trigger_words("aurelia_character_v3.safetensors")


def test_api(lib, media):
    ids = library(lib, media, n=8)
    TOKEN = "t"
    H = {"X-MLG-Token": TOKEN}

    async def go():
        c = TestClient(TestServer(make_app(lib, TOKEN), host="127.0.0.1"))
        await c.start_server()
        try:
            r = await c.post("/api/characters", json={"name": "Aurelia", "aliases": ["Aury"], "rules": [{"kind": "lora", "value": "aurelia"}, {"kind": "tag", "value": "soft light"}]}, headers=H)
            ch = await r.json()
            assert r.status == 200 and ch["n"] > 0 and len(ch["rules"]) == 2
            assert (await c.post("/api/characters", json={"name": "aurelia"}, headers=H)).status == 422
            assert (await c.post("/api/characters", json={"name": "X", "rules": "nope"}, headers=H)).status == 422
            lst = await (await c.get("/api/characters", headers=H)).json()
            assert lst["characters"][0]["name"] == "Aurelia" and "review" in lst
            queue = (await (await c.get(f"/api/characters/review?character={ch['id']}", headers=H)).json())["items"]
            if queue:
                r = await c.post(f"/api/characters/{ch['id']}/review", json={"files": [queue[0]["file_id"]], "verdict": "confirm"}, headers=H)
                assert r.status == 200
            assert (await c.post(f"/api/characters/{ch['id']}/review", json={"files": [1], "verdict": "maybe"}, headers=H)).status == 400
            fid = ids["c02.png"]
            r = await c.post(f"/api/file/{fid}/characters", json={"character": ch["id"], "verdict": "reject"}, headers=H)
            assert "Aurelia" not in [x["name"] for x in (await r.json())["characters"]]
            r = await c.post(f"/api/characters/{ch['id']}", json={"name": "Aurelia R", "cover": fid}, headers=H)
            assert (await r.json())["name"] == "Aurelia R" and (await r.json())["cover"] == fid
            words = await (await c.get("/api/characters/words?lora=aurelia_character_v3", headers=H)).json()
            assert isinstance(words["words"], list)
            stats = await (await c.get("/api/stats?days=0", headers=H)).json()
            assert stats["characters"][0]["name"] == "Aurelia R"
            assert (await c.delete(f"/api/characters/{ch['id']}", headers=H)).status == 200
            assert (await c.get(f"/api/characters/{ch['id']}", headers=H)).status == 404
        finally:
            await c.close()
    asyncio.run(go())


def test_answers_in_to_review_are_one_history_step_and_come_back(lib, media):
    from mlgallery import fileops
    library(lib, media, n=16)
    for fid in lib.files(limit=100)["items"]:
        lib.set_tags(fid["id"], add=["noisy"])
    cid = lib.character_save(None, "Night", [], [{"kind": "tag", "value": "noisy"}])
    queue = [r["file_id"] for r in lib.review_queue(cid)]
    a = lib.character_review(cid, [queue[0]], "confirm")
    b = lib.character_review(cid, [queue[1]], "reject")
    assert a["op"] == b["op"]  # (a run of answers: one step)
    with lib.db.read() as c:
        assert c.execute("SELECT summary FROM ops WHERE id=?", (a["op"],)).fetchone()[0] == "Reviewed 2 files for Night: 1 yes, 1 no"
    assert lib.review_take_back(cid) == {"file_id": queue[1]}  # Z: the last one only
    assert queue[1] in [r["file_id"] for r in lib.review_queue(cid)] and lib.character(cid)["n"] == 1
    # enough no's mute the rule; undoing the step brings its suggestions back, and the yes goes too
    lib.character_review(cid, queue[2:8], "reject")
    assert lib.character(cid)["rules"][0]["muted"] and lib.character(cid)["counts"]["review"] == 0
    fileops.undo(lib.db, a["op"])
    ch = lib.character(cid)
    assert not ch["rules"][0]["muted"] and ch["counts"]["review"] == 16 and ch["n"] == 0
    assert lib.review_take_back(cid) is None  # (nothing left to take back)


def test_another_characters_answers_are_another_step(lib, media):
    library(lib, media, n=16)
    for fid in lib.files(limit=100)["items"]:
        lib.set_tags(fid["id"], add=["noisy"])
    one = lib.character_save(None, "One", [], [{"kind": "tag", "value": "noisy"}])
    two = lib.character_save(None, "Two", [], [{"kind": "tag", "value": "noisy"}])
    f = lib.review_queue(one)[0]["file_id"]
    assert lib.character_review(one, [f], "confirm")["op"] != lib.character_review(two, [f], "reject")["op"]


def test_undoing_answers_leaves_a_later_decision_alone(lib, media):
    from mlgallery import fileops
    library(lib, media, n=16)
    for fid in lib.files(limit=100)["items"]:
        lib.set_tags(fid["id"], add=["noisy"])
    cid = lib.character_save(None, "Night", [], [{"kind": "tag", "value": "noisy"}])
    f = lib.review_queue(cid)[0]["file_id"]
    r = lib.character_review(cid, [f], "confirm")
    lib.set_file_character(f, cid, "reject")  # (changed your mind in the details panel)
    fileops.undo(lib.db, r["op"])
    with lib.db.read() as c:
        assert c.execute("SELECT state FROM file_characters WHERE file_id=? AND character_id=?", (f, cid)).fetchone()[0] == "rejected"
    # Z takes back answers just given, for this character: not an old run
    r = lib.character_review(cid, [lib.review_queue(cid)[0]["file_id"]], "confirm")
    with lib.db.write() as c:
        c.execute("UPDATE ops SET at=at-86400 WHERE id=?", (r["op"],))
    assert lib.review_take_back(cid) is None
