"""Statistics, prompt tags, the date and size filters, and Model Librarian's library (a fake one, never yours)."""
import json
import os
import random
import time

import pytest

from mlgallery import metadata, mllink, scan, stats
from sample_media import generation, make_image
from test_meta import q, read_all


def library_over_days(lib, media, n=12, days=10):
    """n images, one every days/n days back from now; i%4==3 are JPEG (A1111), i%4==2 have no data."""
    rnd = random.Random(3)
    now = time.time()
    for i in range(n):
        ext = ".jpg" if i % 4 == 3 else ".png"
        p = str(media / f"g{i:02d}{ext}")
        make_image(p, (256, 192) if i % 2 else (192, 256), i, None if i % 4 == 2 else generation(i, rnd, (256, 192)))
        t = now - i * days / n * 86400
        os.utime(p, (t, t))
    with lib.db.write() as c:
        fid = c.execute("INSERT INTO folders (path, added) VALUES (?, ?)", (str(media), now)).lastrowid
    scan.scan_folder(lib.db, {"id": fid, "path": str(media)})
    read_all(lib)


def test_prompt_tags():
    assert metadata.prompt_tags(r"a lighthouse, (paper cut:1.2), ((soft light)), [blurry], BREAK masterpiece, <lora:x:0.8>, score_9:1.1, cute \(cat\), a") == \
        ["a lighthouse", "paper cut", "soft light", "blurry", "masterpiece", "score 9", "cute (cat)"]
    assert metadata.prompt_tags("Dusk, dusk ,DUSK") == ["dusk"]
    assert len(metadata.prompt_tags(", ".join(f"t{i}" for i in range(200)))) == metadata.MAX_PROMPT_TAGS
    assert metadata.prompt_tags("") == [] and metadata.prompt_tags(None) == []


def test_prompt_tags_are_stored_and_backfilled(lib, media):
    library_over_days(lib, media, n=4)
    with lib.db.read() as c:
        n = c.execute("SELECT COUNT(*) FROM prompt_tags").fetchone()[0]
    assert n > 0
    with lib.db.write() as c:
        c.execute("DELETE FROM prompt_tags")
    assert metadata.backfill_prompt_tags(lib.db) == 3  # every file with a prompt, from the stored prompt
    with lib.db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM prompt_tags").fetchone()[0] == n
    assert metadata.backfill_prompt_tags(lib.db) == 0


def test_date_size_and_cfg_filters(lib, media):
    library_over_days(lib, media, n=12, days=12)
    today = time.strftime("%Y-%m-%d")
    week_ago = time.strftime("%Y-%m-%d", time.localtime(time.time() - 7 * 86400))
    assert "g00.png" in q(lib, f"on:{today}") and "g11.png" not in q(lib, f"on:{today}")
    recent = q(lib, f"after:{week_ago}")
    older = q(lib, f"before:{week_ago}")
    assert recent and older and not set(recent) & set(older) and len(recent) + len(older) == 12
    assert q(lib, "after:yesterday") == [] and q(lib, "on:2026-13-45") == []
    assert set(q(lib, "size:256x192")) == {f"g{i:02d}{'.jpg' if i % 4 == 3 else '.png'}" for i in range(1, 12, 2)}
    assert q(lib, "size:1x1") == [] and q(lib, "size:big") == []
    assert q(lib, "cfg:5.5") and set(q(lib, "cfg:5.5")) <= set(q(lib, "has:prompt")) and q(lib, "cfg:x") == []


def test_overview(lib, media):
    library_over_days(lib, media, n=12, days=10)
    o = stats.overview(lib, days=None)
    assert o["totals"]["n"] == 12 and o["totals"]["images"] == 12 and o["totals"]["with_data"] == 9
    assert sum(p["images"] for p in o["timeline"]["points"]) == 12 and o["timeline"]["by"] == "week"
    assert stats.overview(lib, days=30)["timeline"]["by"] == "day"
    ck = {c["name"]: c["n"] for c in o["checkpoints"]}
    assert sum(ck.values()) == 9 and set(ck) <= {"sample_xl_base", "dreamy_mix_v3", "turbo_sample_int4"}
    assert o["models_used"] == len(ck) and o["loras_used"] == len(o["loras"])
    assert all(0.4 < l["strength"] <= 1 for l in o["loras"] if "<lora" not in l["name"])
    assert any(t["name"] == "blurry" for t in o["prompt_tags"]) is False  # the negative isn't counted
    assert {s["name"] for s in o["settings"]["sampler"]} <= {"euler", "dpmpp_2m", "euler_ancestral", "dpmpp 2m", "euler ancestral", "euler a"}
    assert {s["name"] for s in o["settings"]["size"]} == {"256x192", "192x256"}
    assert o["folders"][0]["n"] == 12 and o["drives"][0]["n"] == 12
    short = stats.overview(lib, days=3)
    assert 0 < short["totals"]["n"] < 12
    assert "library" not in stats.overview(lib, days=3)  # no Model Librarian asked for


def fake_ml(tmp_path, models):
    root = tmp_path / "models"
    data = root / ".sm_model_fetcher"
    data.mkdir(parents=True)
    (data / "registry.json").write_text(json.dumps({"version": 1, "entries": {}}))
    (data / "usage.json").write_text(json.dumps({"last_used": {"loras:soft_light_v1": 1700000000.0}}))
    for rel in models:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 100)
    return data


def test_model_librarian_library_and_never_used(lib, media, tmp_path, monkeypatch):
    library_over_days(lib, media, n=8)
    data = fake_ml(tmp_path, ["checkpoints/SDXL/sample_xl_base.safetensors", "checkpoints/never_used_ckpt.safetensors",
                              "diffusion_models/unused_unet.gguf", "loras/paper_cut_style_v2.safetensors", "loras/soft_light_v1.safetensors",
                              "loras/zz_unused.safetensors", "loras/notes.txt", "vae/ignored.safetensors"])
    monkeypatch.setenv("MLG_ML_DATA", str(data))
    lib.models._cache = None
    have = lib.models.models()
    assert have["data_dir"] == os.path.normpath(str(data)) and len(have["models"]) == 6  # .txt and vae/ not counted
    sl = next(m for m in have["models"] if m["name"] == "soft_light_v1")
    assert sl["last_used"] == 1700000000.0 and sl["kind"] == "lora"
    o = stats.overview(lib, days=None, models=lib.models)
    names = {m["name"] for m in o["library"]["unused"]}
    used = {u["name"] for u in lib.info(lib.files()["items"][0]["id"])["uses"]} | {c["name"] for c in o["checkpoints"]} | {l["name"] for l in o["loras"]}
    assert {"never_used_ckpt", "unused_unet", "zz_unused"} <= names and not names & used
    assert o["library"]["unused_count"]["unet"] == 1 and o["library"]["found"] and o["library"]["total"] == 6
    assert o["library"]["unused"][0]["kind"] == "checkpoint"  # checkpoints first


def test_find_only_looks_where_it_is_told(tmp_path, monkeypatch):
    monkeypatch.setenv("MLG_ML_DATA", str(tmp_path / "nothing"))
    assert mllink.find() is None
    data = fake_ml(tmp_path, [])
    monkeypatch.setenv("MLG_ML_DATA", str(data))
    assert mllink.find() == os.path.normpath(str(data))
    assert mllink.stem("loras/Sub/x.v2.safetensors") == "x.v2" and mllink.stem("a.gguf") == "a" and mllink.stem("plain") == "plain"
