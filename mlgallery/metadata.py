"""What made each file: the prompt, settings, models and workflow saved in it, read into the index.

Read in the background (newest first), once per file and again only when the file changes. The readers are
Model Librarian's (mlgallery/meta), so a file reads the same in both. The workflow itself is kept compressed
and only unpacked when you open it. A Gallery Info node's entry (`mlgallery`) adds tags, a rating and more.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import zlib

from .meta import imagemeta, recipe_parse, videometa

MAX_READ = 96 * 1024 * 1024  # an image bigger than this isn't read for metadata (nothing generated is this big)
INFO_NODES = ("MLGalleryInfo",)  # the Gallery Info node's class, for the fallback in the `prompt` graph
CAPS = {"albums": 16, "tags": 64, "characters": 16}
TEXT_CAP, NOTE_CAP = 100, 2048


# ---------------------------------------------------------------- reading one file
def read_text(path: str, kind: str) -> dict:
    """The file's metadata key/values (PNG chunks, EXIF fields, video tags)."""
    if kind == "video":
        return videometa.read(path)["text"]
    if os.path.getsize(path) > MAX_READ:
        return {}
    with open(path, "rb") as f:
        return imagemeta.read(f.read())["text"]


def _split(v) -> list[str]:
    items = v if isinstance(v, list) else re.split(r"[,\n]", v) if isinstance(v, str) else []
    out, seen = [], set()
    for x in items:
        x = str(x).strip()[:TEXT_CAP] if isinstance(x, (str, int, float)) else ""
        if x and x.lower() not in seen:
            seen.add(x.lower())
            out.append(x)
    return out


def clean_info(raw) -> dict | None:
    """A Gallery Info entry, checked and capped (a stray wired string can't bloat the index)."""
    if not isinstance(raw, dict):
        return None
    out = {k: _split(raw.get(k))[:n] for k, n in CAPS.items()}
    out["project"] = str(raw.get("project") or "").strip()[:TEXT_CAP]
    r = raw.get("rating")
    try:
        r = int(r) if r not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        r = None
    out["rating"] = r if r and 1 <= r <= 5 else None
    fav = raw.get("favourite", raw.get("favorite"))
    out["favourite"] = fav is True or str(fav).lower() in ("true", "1", "yes")
    out["note"] = str(raw.get("note") or "").strip()[:NOTE_CAP]
    return out if any(out.values()) else None


def gallery_info(text: dict, graph) -> dict | None:
    """The `mlgallery` entry, or else the values typed into a Gallery Info node, from the `prompt` graph
    (the node may have been skipped as cached; wired-in values are only in the entry)."""
    raw = text.get("mlgallery")
    if raw:
        try:
            got = clean_info(recipe_parse._loads(raw))
            if got:
                return got
        except ValueError:
            pass
    if isinstance(graph, dict):
        for n in graph.values():
            if isinstance(n, dict) and n.get("class_type") in INFO_NODES:
                inputs = {k: v for k, v in (n.get("inputs") or {}).items() if not isinstance(v, list) or not recipe_parse._is_link(v)}
                return clean_info(inputs)
    return None


def _name(ref: dict | None) -> str | None:
    if not ref:
        return None
    name = ref.get("name") or ref.get("file")
    return re.sub(r"\.(safetensors|ckpt|pt|pth|bin|gguf|sft)$", "", str(name).replace("\\", "/").split("/")[-1], flags=re.I) if name else None


def parse(path: str, kind: str) -> dict | None:
    """-> what made this file, or None when it holds no generation data."""
    text = read_text(path, kind)
    if not text:
        return None
    try:
        rec = recipe_parse.from_text(text)
    except recipe_parse.NoMetadata:
        info = gallery_info(text, None)
        return {"source": None, "info": info, "uses": [], "comfy": None} if info else None
    comfy = rec.get("comfy") or {}
    s = rec.get("settings") or {}
    uses = []
    m = rec.get("model")
    if m and _name(m):
        uses.append({"kind": m.get("type") or "checkpoint", "name": _name(m), "file": m.get("file"), "hash": m.get("hash")})
    if rec.get("vae") and _name(rec["vae"]):
        uses.append({"kind": "vae", "name": _name(rec["vae"]), "file": rec["vae"].get("file"), "hash": rec["vae"].get("hash")})
    for l in rec.get("loras") or []:
        if _name(l):
            uses.append({"kind": "lora", "name": _name(l), "file": l.get("file"), "hash": l.get("hash"),
                         "strength": _num(l.get("weight")), "clip": _num(l.get("clip_weight"))})
    graph = comfy.get("prompt")
    wf = comfy.get("workflow")
    nodes = len(wf.get("nodes") or []) if isinstance(wf, dict) else len(graph) if isinstance(graph, dict) else None
    return {
        "source": rec.get("format") or None,
        "positive": (rec.get("prompt") or {}).get("positive") or "",
        "negative": (rec.get("prompt") or {}).get("negative") or "",
        "seed": _int(s.get("seed")), "steps": _int(s.get("steps")), "cfg": _num(s.get("cfg")),
        "sampler": _str(s.get("sampler") or s.get("sampler_raw")), "scheduler": _str(s.get("scheduler")),
        "width": _int(s.get("width")), "height": _int(s.get("height")), "denoise": _num(s.get("denoise")),
        "clip_skip": _int(s.get("clip_skip")),
        "model": _name(m), "nodes": nodes, "uses": uses,
        "comfy": {k: v for k, v in (("prompt", graph), ("workflow", wf)) if v} or None,
        "info": gallery_info(text, graph),
    }


def _num(v):
    try:
        f = float(v)
        return f if f == f and abs(f) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _int(v):
    f = _num(v)
    return int(f) if f is not None and abs(f) < 2 ** 63 else None


def _str(v):
    return str(v)[:200] if v not in (None, "") else None


# ---------------------------------------------------------------- the index
_WEIGHT = re.compile(r"^\((.*?)(?::\s*-?[\d.]+)?\)$|^\[(.*)\]$|^(.*?):\s*-?[\d.]+$")
_LORA_TAG = re.compile(r"<(?:lora|lyco|hypernet):[^>]*>", re.I)
MAX_PROMPT_TAGS = 60


def prompt_tags(text: str) -> list[str]:
    """'a lighthouse, (paper cut:1.2), BREAK, <lora:x:1>' -> ['a lighthouse', 'paper cut']: the prompt's comma-separated
    parts, weights and brackets removed, lower case, for statistics."""
    out, seen = [], set()
    for part in re.split(r"[,\n|]|\bBREAK\b", _LORA_TAG.sub(",", text or "")):
        # an escaped bracket is part of the words ("artist \(style\)"): kept aside while weights come off
        t = part.strip().replace("\\(", "\x01").replace("\\)", "\x02").replace("\\[", "\x03").replace("\\]", "\x04")
        for _ in range(3):  # ((nested:1.1)) and the like
            m = _WEIGHT.match(t)
            if not m:
                break
            t = next(g for g in m.groups() if g is not None).strip()
        t = re.sub(r"[()\[\]{}]", "", t)
        t = t.replace("\x01", "(").replace("\x02", ")").replace("\x03", "[").replace("\x04", "]")
        t = re.sub(r"\s+", " ", t.replace("_", " ")).strip(" .;:").lower()
        if 1 < len(t) <= 60 and t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= MAX_PROMPT_TAGS:
            break
    return out


def tag_id(c, name: str) -> int:
    c.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
    return c.execute("SELECT id FROM tags WHERE name=? COLLATE NOCASE", (name,)).fetchone()[0]


def refresh_search(c, file_id: int) -> None:
    """The file's row in the search index, from what's in the tables now."""
    f = c.execute("SELECT name FROM files WHERE id=?", (file_id,)).fetchone()
    if not f:
        return
    g = c.execute("SELECT positive, negative FROM generations WHERE file_id=?", (file_id,)).fetchone()
    models = " ".join(r[0] for r in c.execute("SELECT DISTINCT name FROM uses WHERE file_id=?", (file_id,)))
    tags = " ".join(r[0] for r in c.execute("SELECT t.name FROM file_tags ft JOIN tags t ON t.id=ft.tag_id WHERE ft.file_id=? AND ft.removed=0", (file_id,)))
    name = re.sub(r"[_\-.]+", " ", f["name"])  # sample_00042_png.png finds "sample 00042"
    c.execute("DELETE FROM search WHERE rowid=?", (file_id,))
    c.execute("INSERT INTO search (rowid, name, prompt, negative, models, tags) VALUES (?,?,?,?,?,?)",
              (file_id, name, g["positive"] if g else "", g["negative"] if g else "", models, tags))


def store(c, file_id: int, got: dict | None) -> int:
    """Write what was read for one file (inside a write transaction). -> its meta state."""
    c.execute("DELETE FROM generations WHERE file_id=?", (file_id,))
    c.execute("DELETE FROM uses WHERE file_id=?", (file_id,))
    c.execute("DELETE FROM prompt_tags WHERE file_id=?", (file_id,))
    # workflow tags are re-derived; ones you removed stay removed, yours are untouched
    c.execute("DELETE FROM file_tags WHERE file_id=? AND source='workflow' AND removed=0", (file_id,))
    state = 2
    if got:
        state = 1 if got.get("source") else 2
        blob = zlib.compress(json.dumps(got["comfy"], separators=(",", ":")).encode("utf-8"), 6) if got.get("comfy") else None
        info = got.get("info")
        c.execute("INSERT INTO generations (file_id, source, positive, negative, seed, steps, cfg, sampler, scheduler, width, height, denoise,"
                  " clip_skip, model, nodes, workflow, info) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (file_id, got.get("source"), got.get("positive"), got.get("negative"), got.get("seed"), got.get("steps"), got.get("cfg"),
                   got.get("sampler"), got.get("scheduler"), got.get("width"), got.get("height"), got.get("denoise"), got.get("clip_skip"),
                   got.get("model"), got.get("nodes"), blob, json.dumps(info) if info else None))
        c.executemany("INSERT INTO uses (file_id, kind, name, file, hash, strength, clip) VALUES (?,?,?,?,?,?,?)",
                      [(file_id, u["kind"], u["name"], u.get("file"), u.get("hash"), u.get("strength"), u.get("clip")) for u in got["uses"]])
        c.executemany("INSERT INTO prompt_tags (file_id, tag) VALUES (?, ?)", [(file_id, t) for t in prompt_tags(got.get("positive"))])
        if info:
            for t in info.get("tags") or []:
                c.execute("INSERT OR IGNORE INTO file_tags (file_id, tag_id, source) VALUES (?,?, 'workflow')", (file_id, tag_id(c, t)))
            m = c.execute("SELECT source FROM marks WHERE file_id=?", (file_id,)).fetchone()
            if (not m or m["source"] == "workflow") and (info.get("rating") or info.get("favourite")):  # yours win
                c.execute("INSERT OR REPLACE INTO marks (file_id, rating, favorite, source, updated) VALUES (?,?,?, 'workflow', ?)",
                          (file_id, info.get("rating"), int(bool(info.get("favourite"))), time.time()))
    c.execute("UPDATE files SET meta=? WHERE id=?", (state, file_id))
    refresh_search(c, file_id)
    return state


def backfill_prompt_tags(db) -> int:
    """Prompt tags for files read before they existed (from the stored prompt: no file is read again)."""
    with db.read() as c:
        rows = c.execute("SELECT g.file_id, g.positive FROM generations g WHERE COALESCE(g.positive, '') <> ''"
                         " AND NOT EXISTS (SELECT 1 FROM prompt_tags p WHERE p.file_id = g.file_id)").fetchall()
    for i in range(0, len(rows), 500):
        with db.write() as c:
            c.executemany("INSERT INTO prompt_tags (file_id, tag) VALUES (?, ?)",
                          [(r["file_id"], t) for r in rows[i:i + 500] for t in prompt_tags(r["positive"])])
    return len(rows)


def workflow(db, file_id: int) -> dict | None:
    with db.read() as c:
        r = c.execute("SELECT workflow FROM generations WHERE file_id=?", (file_id,)).fetchone()
    return json.loads(zlib.decompress(r[0])) if r and r[0] else None


class MetaReader:
    """Reads new and changed files' metadata in the background, newest first."""

    def __init__(self, db):
        self.db = db
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.read = 0
        self.failed = 0

    def start(self) -> None:
        threading.Thread(target=self._work, name="metadata", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def nudge(self) -> None:
        self._wake.set()

    def pending(self) -> int:
        with self.db.read() as c:
            return c.execute("SELECT COUNT(*) FROM files f JOIN folders d ON d.id=f.folder_id WHERE f.meta=0 AND f.missing=0 AND d.online=1").fetchone()[0]

    def read_one(self, row) -> int:
        try:
            got = parse(os.path.join(row["path"], row["rel"]), row["kind"])
            ok = True
        except Exception:  # a file we can't read: marked, not fatal
            got, ok = None, False
        with self.db.write() as c:
            if ok:
                return store(c, row["id"], got)
            c.execute("UPDATE files SET meta=-1 WHERE id=?", (row["id"],))
            self.failed += 1
            return -1

    def _work(self) -> None:
        while not self._stop.is_set():
            with self.db.read() as c:
                rows = c.execute("SELECT f.id, f.rel, f.kind, d.path FROM files f JOIN folders d ON d.id=f.folder_id"
                                 " WHERE f.meta=0 AND f.missing=0 AND d.online=1 ORDER BY f.mtime DESC LIMIT 32").fetchall()
            if not rows:
                self._wake.wait(5)
                self._wake.clear()
                continue
            for r in rows:
                if self._stop.is_set():
                    return
                self.read_one(r)
                self.read += 1
