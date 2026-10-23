"""The search box: free text plus filters, turned into SQL.

    lighthouse dusk            words in the file name, prompt, models or tags (each as a prefix: "light" finds "lighthouse")
    "paper cut"                a phrase
    model:krea  lora:aurelia   a checkpoint / LoRA whose name contains this (quote names with spaces: lora:"paper cut")
    tag:dusk                   a tag (exactly)
    rating:4  rating:>=3  rating:2-4  rating:0 (unrated)
    is:fav  is:video  is:image  has:prompt  has:workflow  no:prompt
    sampler:euler  scheduler:karras  seed:1234  steps:8  neg:blurry (words in the negative prompt)

Unknown keys are treated as text. Every value is passed as a parameter, never pasted into the SQL.
"""
from __future__ import annotations

import re

TOKEN = re.compile(r'(-?)(?:(\w+):)?(?:"([^"]*)"?|(\S+))')
KEYS = {"model", "lora", "tag", "rating", "is", "has", "no", "sampler", "scheduler", "seed", "steps", "neg", "vae", "ext"}


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


def build(q: str) -> tuple[list[str], list]:
    """-> (SQL conditions on files f, their parameters)."""
    where, args, words, neg_words = [], [], [], []
    for key, v in tokens(q):
        if key is None:
            words.append(v)
        elif key in ("model", "lora", "vae"):
            kinds = {"model": ("checkpoint", "unet"), "lora": ("lora",), "vae": ("vae",)}[key]
            where.append(f"EXISTS (SELECT 1 FROM uses u WHERE u.file_id=f.id AND u.kind IN ({','.join('?' * len(kinds))}) AND u.name LIKE ? ESCAPE '\\')")
            args += [*kinds, f"%{_like(v)}%"]
        elif key == "tag":
            where.append("EXISTS (SELECT 1 FROM file_tags ft JOIN tags t ON t.id=ft.tag_id WHERE ft.file_id=f.id AND ft.removed=0 AND t.name=? COLLATE NOCASE)")
            args.append(v)
        elif key == "rating":
            if v.strip() in ("0", "none"):
                where.append("COALESCE(m.rating, 0) = 0")
                continue
            r = _range(v if any(ch in v for ch in "<>=-") else f">={v}")
            if r:
                op, vals = r
                if len(vals) == 2:
                    where.append("m.rating >= ? AND m.rating <= ?")
                else:
                    where.append(f"m.rating {op} ?")
                args += vals
        elif key == "is":
            v = v.lower()
            if v in ("fav", "favorite", "favourite"):
                where.append("m.favorite = 1")
            elif v in ("image", "video"):
                where.append("f.kind = ?")
                args.append(v)
            elif v == "rated":
                where.append("m.rating > 0")
        elif key in ("has", "no"):
            v = v.lower()
            cond = {"prompt": "EXISTS (SELECT 1 FROM generations g WHERE g.file_id=f.id AND COALESCE(g.positive, '') <> '')",
                    "workflow": "EXISTS (SELECT 1 FROM generations g WHERE g.file_id=f.id AND g.workflow IS NOT NULL)",
                    "meta": "f.meta = 1", "data": "f.meta = 1",
                    "tags": "EXISTS (SELECT 1 FROM file_tags ft WHERE ft.file_id=f.id AND ft.removed=0)",
                    "lora": "EXISTS (SELECT 1 FROM uses u WHERE u.file_id=f.id AND u.kind='lora')"}.get(v)
            if cond:
                where.append(cond if key == "has" else f"NOT ({cond})")
        elif key in ("sampler", "scheduler"):
            where.append(f"EXISTS (SELECT 1 FROM generations g WHERE g.file_id=f.id AND g.{key} LIKE ? ESCAPE '\\')")
            args.append(f"%{_like(v)}%")
        elif key in ("seed", "steps"):
            r = _range(v)
            if r:
                op, vals = r
                col = f"g.{key}"
                sql = f"{col} >= ? AND {col} <= ?" if len(vals) == 2 else f"{col} {op} ?"
                where.append(f"EXISTS (SELECT 1 FROM generations g WHERE g.file_id=f.id AND {sql})")
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


def _like(v: str) -> str:
    return v.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
