"""The search box: free text plus filters, turned into SQL.

    lighthouse dusk            words in the file name, prompt, models or tags (each as a prefix: "light" finds "lighthouse")
    "paper cut"                a phrase
    model:krea  lora:aurelia   a checkpoint / LoRA whose name contains this (quote names with spaces: lora:"paper cut")
    tag:dusk                   a tag (exactly)          album:coast   an album (exactly)
    character:aurelia          a character (by name or alias; matched or confirmed)   review:aurelia  is:review
    rating:4  rating:>=3  rating:2-4  rating:0 (unrated)
    is:fav  is:video  is:image  has:prompt  has:workflow  no:prompt
    sampler:euler  scheduler:karras  (exactly; sampler:dpmpp* for every name starting so)
    seed:1234  steps:8  steps:20-30  cfg:7  size:1024x1024  neg:blurry (words in the negative prompt)
    after:2026-10-01  before:2026-10-23  on:2026-10-23  (by when the file was made, your local time)

Unknown keys are treated as text. Every value is passed as a parameter, never pasted into the SQL.
"""
from __future__ import annotations

import datetime
import re
import time

TOKEN = re.compile(r'(-?)(?:(\w+):)?(?:"([^"]*)"?|(\S+))')
KEYS = {"model", "lora", "tag", "rating", "is", "has", "no", "sampler", "scheduler", "seed", "steps", "neg", "vae", "ext",
        "cfg", "size", "after", "before", "on", "character", "review", "album"}


def tokens(q: str) -> list[tuple[str | None, str]]:
    """[(key or None, value)] in order. A key the search doesn't know stays part of the text."""
    out = []
    for m in TOKEN.finditer(q or ""):
        neg, key, quoted, bare = m.groups()
        value = quoted if quoted is not None else (bare or "")
        key = key.lower() if key else None
        if key and key not in KEYS:
            value, key = f"{key}:{value}", None
        if neg and not key:
            value = "-" + value
        if value or key:
            out.append((key, value))
    return out


def _fts(words: list[str], column: str = "{name prompt models tags}") -> str:
    """An FTS5 query: each word or phrase quoted (so nothing in it is FTS syntax), words as prefixes."""
    parts = []
    for w in words:
        neg = w.startswith("-") and len(w) > 1
        w = w[1:] if neg else w
        w = w.replace('"', "").strip()
        if not w:
            continue
        term = f'"{w}"' + ("" if " " in w else "*")
        parts.append(("NOT " if neg else "") + term)
    if not parts:
        return ""
    if parts[0].startswith("NOT "):  # FTS5 needs something to subtract from
        return ""
    return f"{column} : ({' '.join(parts)})"


def _range(v: str):
    v = v.strip()
    m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", v)
    if m:
        return ">= ? AND {col} <=", [int(m.group(1)), int(m.group(2))]
    m = re.fullmatch(r"(>=|<=|>|<|=)?\s*(\d+)", v)
    if not m:
        return None
    return (m.group(1) or "="), [int(m.group(2))]


def _names(db, kinds: tuple) -> list[str]:
    """Every model name of these kinds (a few hundred at most), so a filter becomes an exact, indexed lookup."""
    def load():
        with db.read() as c:
            return [r[0] for r in c.execute(f"SELECT DISTINCT name FROM uses WHERE kind IN ({','.join('?' * len(kinds))})", kinds)]
    return db.cached(("names", kinds), load)


def _characters(db, name: str) -> list[int]:
    """The characters called this (or with it as an alias)."""
    def load():
        with db.read() as c:
            return [(r[0], r[1], r[2]) for r in c.execute("SELECT id, name, aliases FROM characters")]
    low = name.strip().lower()
    out = []
    import json
    for cid, n, aliases in db.cached(("characters",), load):
        if n.lower() == low or low in (a.lower() for a in json.loads(aliases or "[]")):
            out.append(cid)
    return out


def _in(values: list) -> str:
    return ",".join("?" * len(values))


def build(q: str, db=None) -> tuple[list[str], list]:
    """-> (SQL conditions on files f, their parameters). Each filter picks its files with one indexed lookup
    (f.id IN (...)), never a lookup per file, so they stay quick on a big library. Without `db` (tests of the
    grammar) names aren't resolved and matching uses LIKE."""
    where, args, words, neg_words = [], [], [], []
    for key, v in tokens(q):
        if key is None:
            words.append(v)
        elif key in ("model", "lora", "vae"):
            kinds = {"model": ("checkpoint", "unet"), "lora": ("lora",), "vae": ("vae",)}[key]
            if db is None:
                where.append(f"f.id IN (SELECT file_id FROM uses WHERE kind IN ({_in(kinds)}) AND name LIKE ? ESCAPE '\\')")
                args += [*kinds, f"%{_like(v)}%"]
                continue
            low = v.lower()
            hits = [n for n in _names(db, kinds) if low in n.lower()]
            if not hits:
                where.append("0")
                continue
            where.append(f"f.id IN (SELECT file_id FROM uses WHERE kind IN ({_in(kinds)}) AND name IN ({_in(hits)}))")
            args += [*kinds, *hits]
        elif key in ("character", "review"):
            states = ("auto", "confirmed") if key == "character" else ("review",)
            ids = _characters(db, v) if db is not None else None
            if ids is None:
                where.append(f"f.id IN (SELECT fc.file_id FROM file_characters fc JOIN characters ch ON ch.id=fc.character_id"
                             f" WHERE fc.state IN ({_in(states)}) AND ch.name = ? COLLATE NOCASE)")
                args += [*states, v]
            elif not ids:
                where.append("0")
            else:
                where.append(f"f.id IN (SELECT file_id FROM file_characters WHERE character_id IN ({_in(ids)}) AND state IN ({_in(states)}))")
                args += [*ids, *states]
        elif key == "album":
            where.append("f.id IN (SELECT af.file_id FROM album_files af JOIN albums al ON al.id=af.album_id WHERE af.removed=0 AND al.name = ? COLLATE NOCASE)")
            args.append(v)
        elif key == "tag":
            where.append("f.id IN (SELECT ft.file_id FROM file_tags ft JOIN tags t ON t.id=ft.tag_id WHERE ft.removed=0 AND t.name = ? COLLATE NOCASE)")
            args.append(v)
        elif key == "rating":
            if v.strip() in ("0", "none"):
                where.append("f.id NOT IN (SELECT file_id FROM marks WHERE rating > 0)")
                continue
            r = _range(v if any(ch in v for ch in "<>=-") else f">={v}")
            if r:
                op, vals = r
                if len(vals) == 2:
                    where.append("f.id IN (SELECT file_id FROM marks WHERE rating >= ? AND rating <= ?)")
                else:
                    where.append(f"f.id IN (SELECT file_id FROM marks WHERE rating {op} ?)")
                args += vals
        elif key == "is":
            v = v.lower()
            if v in ("fav", "favorite", "favourite"):
                where.append("f.id IN (SELECT file_id FROM marks WHERE favorite = 1)")
            elif v in ("image", "video"):
                where.append("f.kind = ?")
                args.append(v)
            elif v == "rated":
                where.append("f.id IN (SELECT file_id FROM marks WHERE rating > 0)")
            elif v == "review":
                where.append("f.id IN (SELECT file_id FROM file_characters WHERE state = 'review')")
        elif key in ("has", "no"):
            v = v.lower()
            cond = {"prompt": "f.id IN (SELECT file_id FROM generations WHERE COALESCE(positive, '') <> '')",
                    "workflow": "f.id IN (SELECT file_id FROM generations WHERE workflow IS NOT NULL)",
                    "meta": "f.meta = 1", "data": "f.meta = 1",
                    "tags": "f.id IN (SELECT file_id FROM file_tags WHERE removed = 0)",
                    "lora": "f.id IN (SELECT file_id FROM uses WHERE kind = 'lora')"}.get(v)
            if cond:
                where.append(cond if key == "has" else f"NOT ({cond})")
        elif key in ("sampler", "scheduler"):
            if v.endswith("*"):
                where.append(f"f.id IN (SELECT file_id FROM generations WHERE {key} LIKE ? ESCAPE '\\')")
                args.append(f"{_like(v[:-1])}%")
            else:
                where.append(f"f.id IN (SELECT file_id FROM generations WHERE {key} = ? COLLATE NOCASE)")
                args.append(v)
        elif key == "cfg":
            try:
                cfg = float(v)
            except ValueError:
                where.append("0")
                continue
            where.append("f.id IN (SELECT file_id FROM generations WHERE cfg BETWEEN ? AND ?)")
            args += [cfg - 0.001, cfg + 0.001]
        elif key == "size":
            m = re.fullmatch(r"(\d+)\s*[x×*]\s*(\d+)", v.strip().lower())
            if m:
                where.append("f.width = ? AND f.height = ?")
                args += [int(m.group(1)), int(m.group(2))]
            else:
                where.append("0")
        elif key in ("after", "before", "on"):
            day = _day(v)
            if day is None:
                where.append("0")
            elif key == "after":
                where.append("f.mtime >= ?")
                args.append(day)
            elif key == "before":
                where.append("f.mtime < ?")
                args.append(day)
            else:
                where.append("f.mtime >= ? AND f.mtime < ?")
                args += [day, _day(v, 1)]
        elif key in ("seed", "steps"):
            r = _range(v)
            if r:
                op, vals = r
                sql = f"{key} >= ? AND {key} <= ?" if len(vals) == 2 else f"{key} {op} ?"
                where.append(f"f.id IN (SELECT file_id FROM generations WHERE {sql})")
                args += vals
        elif key == "neg":
            neg_words.append(v)
        elif key == "ext":
            where.append("f.ext = ?")
            args.append("." + v.lower().lstrip("."))
    for words_, col in ((words, "{name prompt models tags}"), (neg_words, "negative")):
        match = _fts(words_, col)
        if match:
            where.append("f.id IN (SELECT rowid FROM search WHERE search MATCH ?)")
            args.append(match)
        elif any(not w.startswith("-") for w in words_):
            where.append("0")  # only punctuation: nothing matches, rather than everything
    return where, args


def _day(v: str, plus: int = 0) -> float | None:
    """'2026-10-23' -> the start of that day (plus some days) in local time, as a timestamp."""
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", v.strip())
    if not m:
        return None
    try:
        d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))) + datetime.timedelta(days=plus)
        return time.mktime((d.year, d.month, d.day, 0, 0, 0, 0, 0, -1))
    except (OverflowError, ValueError):  # 2026-13-45 is no day
        return None


def _like(v: str) -> str:
    return v.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
