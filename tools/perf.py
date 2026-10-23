"""How fast the Gallery's queries are on a big library: a made-up index of N files (no image files on disk).

    python tools/perf.py [--files 250000] [--data DIR]

Builds the index straight into SQLite (files, generations, models, prompt parts, tags, marks, characters,
fingerprints), then times what each screen asks for. The plan's target: under 200 ms for search and filters.
"""
from __future__ import annotations

import argparse
import os
import random
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MLG_ML_DATA", os.path.join(tempfile.gettempdir(), "mlg-perf-no-ml"))

from mlgallery import characters as chars  # noqa: E402
from mlgallery import metadata, stats  # noqa: E402
from mlgallery.library import Library  # noqa: E402

SUBJECTS = ["lighthouse", "mountain lake", "desert dunes", "pine forest", "city rooftops", "harbour", "rolling hills", "glacier",
            "castle", "robot", "dragon", "portrait", "street market", "space station", "waterfall", "library"]
STYLES = ["paper cut style", "watercolor", "cinematic lighting", "flat illustration", "oil painting", "anime", "photograph", "pixel art"]
CKPTS = [f"model_{i:02d}" for i in range(40)]
LORAS = [f"lora_{i:03d}" for i in range(300)]


def build(lib: Library, n: int, seed: int = 1) -> None:
    rnd = random.Random(seed)
    now = time.time()
    with lib.db.write() as c:
        fid = c.execute("INSERT INTO folders (path, added, online) VALUES (?, ?, 1)", (os.path.join(lib.data_dir, "fake-media"), now)).lastrowid
    batch = 5000
    for start in range(0, n, batch):
        rows, gens, uses, tags, search, marks = [], [], [], [], [], []
        for i in range(start, min(n, start + batch)):
            t = now - i * 120 - rnd.random() * 60  # a file every two minutes, going back about a year at 250k
            name = f"gen_{i:07d}.png"
            rows.append((i + 1, fid, f"{i // 1000:04d}/{name}", name, ".png", "image" if i % 50 else "video", 1_000_000 + i, t,
                         f"{rnd.getrandbits(128):032x}", 1024, 1024, t, t, 1, 1, f"{rnd.getrandbits(64):016x}"))
            subj, sty = rnd.choice(SUBJECTS), rnd.choice(STYLES)
            ck = CKPTS[int(rnd.paretovariate(1.2)) % len(CKPTS)]
            ls = rnd.sample(LORAS[:60] if rnd.random() < .7 else LORAS, rnd.randint(0, 3))
            positive = f"{subj}, {sty}, " + ", ".join(l.replace("_", " ") for l in ls) + f", detail {rnd.randint(1, 500)}"
            gens.append((i + 1, "comfyui", positive, "blurry, lowres", rnd.getrandbits(31), rnd.choice([8, 20, 28]), rnd.choice([1.0, 5.5, 7.0]),
                         rnd.choice(["euler", "dpmpp_2m", "euler_ancestral"]), rnd.choice(["simple", "karras", "normal"]), 1024, 1024, 1.0, None, ck, 12))
            uses.append((i + 1, "checkpoint", ck, ck + ".safetensors", None, None, None))
            uses += [(i + 1, "lora", l, l + ".safetensors", None, round(rnd.uniform(.4, 1), 2), None) for l in ls]
            tags += [(i + 1, t) for t in metadata.prompt_tags(positive)]
            search.append((i + 1, name.replace("_", " "), positive, "blurry, lowres", " ".join([ck, *ls]), ""))
            if rnd.random() < .08:
                marks.append((i + 1, rnd.randint(1, 5), int(rnd.random() < .3), "user", now))
        with lib.db.write() as c:
            c.executemany("INSERT INTO files (id, folder_id, rel, name, ext, kind, size, mtime, fp, width, height, first_seen, last_seen, thumb, meta, phash)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            c.executemany("INSERT INTO generations (file_id, source, positive, negative, seed, steps, cfg, sampler, scheduler, width, height, denoise, clip_skip, model, nodes)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", gens)
            c.executemany("INSERT INTO uses (file_id, kind, name, file, hash, strength, clip) VALUES (?,?,?,?,?,?,?)", uses)
            c.executemany("INSERT INTO prompt_tags (file_id, tag) VALUES (?, ?)", tags)
            c.executemany("INSERT INTO search (rowid, name, prompt, negative, models, tags) VALUES (?,?,?,?,?,?)", search)
            c.executemany("INSERT INTO marks (file_id, rating, favorite, source, updated) VALUES (?,?,?,?,?)", marks)
        print(f"  {min(n, start + batch):,} files", end="\r", flush=True)
    with lib.db.write() as c:
        c.execute("UPDATE folders SET file_count=?", (n,))
        for k in range(12):
            cid = c.execute("INSERT INTO characters (name, created) VALUES (?, ?)", (f"Character {k}", now)).lastrowid
            c.execute("INSERT INTO character_rules (character_id, kind, value, strength) VALUES (?, 'lora', ?, 'strong')", (cid, LORAS[k]))
            c.execute("INSERT INTO character_rules (character_id, kind, value, strength) VALUES (?, 'tag', ?, 'weak')", (cid, STYLES[k % len(STYLES)]))
        c.execute("ANALYZE")
    print()


LIB = None


def timed(label, fn, runs=3):
    """The first run with nothing cached (the index just changed), then the best of the repeats (cached)."""
    LIB.db.version += 1  # as after a write: every cached result is stale
    t = time.perf_counter()
    out = fn()
    cold = time.perf_counter() - t
    warm = cold
    for _ in range(runs - 1):
        t = time.perf_counter()
        fn()
        warm = min(warm, time.perf_counter() - t)
    flag = "" if cold < 0.2 else "   <-- over 200 ms"
    print(f"{cold * 1000:8.1f} ms  (then {warm * 1000:6.1f})  {label}{flag}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", type=int, default=250_000)
    ap.add_argument("--data")
    ap.add_argument("--keep", action="store_true", help="keep the made-up index afterwards")
    a = ap.parse_args()
    data = a.data or tempfile.mkdtemp(prefix="mlg-perf-")
    try:
        global LIB
        lib = LIB = Library(data)
        t = time.time()
        print(f"Building a made-up library of {a.files:,} files in {data}")
        build(lib, a.files)
        with lib.db.write() as c:
            t2 = time.time()
            for (cid,) in c.execute("SELECT id FROM characters").fetchall():
                chars.rematch(c, cid)
            print(f"  characters matched in {time.time() - t2:.1f} s")
        print(f"  built in {time.time() - t:.0f} s, index {os.path.getsize(os.path.join(data, 'library.db')) / 1e6:.0f} MB\n")
        timed("the first page, newest first", lambda: lib.files(0, 300))
        timed("a page deep in the library (offset 200k)", lambda: lib.files(min(200_000, a.files - 300), 300))
        timed("the day headings", lambda: lib.days("newest"))
        timed("shuffled (Random)", lambda: lib.files(0, 300, sort="random", seed=7))
        timed("by name", lambda: lib.files(0, 300, sort="name"))
        for q in ["lighthouse", "light", '"paper cut style"', "model:model_03", "lora:lora_007", "tag:watercolor", "rating:4", "is:fav",
                  "sampler:euler steps:20", "lighthouse lora:lora_001 rating:3", "character:\"Character 3\"", "is:review", "after:2026-06-01 model:model_01",
                  "neg:blurry", "zzznomatch"]:
            r = timed(f"search  {q}", lambda q=q: lib.files(0, 300, q=q))
            print(f"            {r['total']:,} found")
        timed("search days for  lora:lora_007", lambda: lib.days("newest", q="lora:lora_007"))
        timed("Statistics, 30 days", lambda: stats.overview(lib, 30), runs=2)
        timed("Statistics, all time", lambda: stats.overview(lib, None), runs=2)
        timed("Characters list", lib.characters, runs=2)
        timed("a character's page", lambda: lib.character(1), runs=2)
        timed("To review", lambda: lib.review_queue(None), runs=2)
        timed("Duplicates", lib.duplicates, runs=1)
        timed("select a whole search (lora:lora_001)", lambda: lib.resolve({"view": {"q": "lora:lora_001"}}))
        timed("suggest  lora:lora_0", lambda: lib.suggest("lora", "lora_0"))
        timed("a file's details", lambda: lib.info(a.files // 2))
    finally:
        if not a.keep and not a.data:
            shutil.rmtree(data, ignore_errors=True)


if __name__ == "__main__":
    main()
