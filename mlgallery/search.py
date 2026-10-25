"""The search box: free text plus filters, turned into SQL.

    lighthouse dusk            words in the file name, prompt, models or tags (each as a prefix: "light" finds "lighthouse")
    "paper cut"                a phrase
    model:krea  lora:aurelia   a checkpoint / LoRA whose name contains this (quote names with spaces: lora:"paper cut")
    tag:dusk                   a tag (exactly)          album:coast   an album (exactly; a smart album: its search)
    character:aurelia          a character (by name or alias; matched or confirmed)   review:aurelia  is:review
    artist:"some name"  site:pixiv   an artist (by name or another name), and where a picture is from (credits.py)
    rating:4  rating:>=3  rating:2-4  rating:0 (unrated)
    is:fav  is:video  is:image  is:ai  is:photo  is:illustration  has:prompt  has:workflow  no:prompt  has:artist  has:source
    sampler:euler  scheduler:karras  (exactly; sampler:dpmpp* for every name starting so)
    seed:1234  steps:8  steps:20-30  cfg:7  size:1024x1024  neg:blurry (words in the negative prompt)
    after:2026-10-01  before:2026-10-23  on:2026-10-23  (by when the file was made, your local time; after:yesterday, on:2026-10 too)
    date:today  date:yesterday  date:7d  date:30d  date:2026-10  date:2026-10-23
    lora:aurelia@0.6-0.8  lora:aurelia@>=0.7  strength:0.7   (a LoRA used at that strength; strength: any LoRA)
    color:#140d17  color:red   a colour that looks like it (5% of the picture or more), or of that family (10%; colors.py)
    -fog  -tag:dusk  -lora:aurelia   (a minus leaves out what follows it: words and filters alike)

Unknown keys are treated as text. Every value is passed as a parameter, never pasted into the SQL.
"""
from __future__ import annotations

import datetime
import re
import time

TOKEN = re.compile(r'(-?)(?:(\w+):)?(?:"([^"]*)"?|(\S+))')
KEYS = {"model", "lora", "tag", "rating", "is", "has", "no", "sampler", "scheduler", "seed", "steps", "neg", "vae", "ext",
        "cfg", "size", "after", "before", "on", "character", "review", "album", "date", "strength", "color", "colour", "artist", "site"}


def tokens(q: str) -> list[tuple[str | None, str]]:
    """[(key or None, value)] in order; a left-out filter (-tag:x) has its key as "-tag". A key the search
    doesn't know stays part of the text."""
    out = []
    for m in TOKEN.finditer(q or ""):
        neg, key, quoted, bare = m.groups()
        value = quoted if quoted is not None else (bare or "")
        key = key.lower() if key else None
        if key and key not in KEYS:
            value, key = f"{key}:{value}", None
        if neg and not key:
            value = "-" + value
        elif neg:
            key = "-" + key
        if value or key:
            out.append((key, value))
    return out


def _terms(words: list[str]) -> tuple[list[str], list[str]]:
    """FTS5 terms, (wanted, left out): each word or phrase quoted (so nothing in it is FTS syntax), words as prefixes."""
    want, out = [], []
    for w in words:
        neg = w.startswith("-") and len(w) > 1
        w = (w[1:] if neg else w).replace('"', "").strip()
        if w:
            (out if neg else want).append(f'"{w}"' + ("" if " " in w else "*"))
    return want, out


def _range(v: str):
    v = v.strip()
    big = 2 ** 63 - 1
    m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", v)
    if m:
        return ">= ? AND {col} <=", [min(int(m.group(1)), big), min(int(m.group(2)), big)]
    m = re.fullmatch(r"(>=|<=|>|<|=)?\s*(\d+)", v)
    if not m:
        return None
    return (m.group(1) or "="), [min(int(m.group(2)), big)]


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


def _artists(db, name: str) -> list[int]:
    """The artists called this (or with it as another name; case, spaces, _ and - alike)."""
    from .characters import norm
    from .credits import name_map

    def load():
        with db.read() as c:
            return name_map(c)
    got = db.cached(("artist-names",), load).get(norm(name))
    return [got] if got else []


def _site(v: str) -> str:
    """site:Pixiv, site:twitter, site:yande.re -> the site as it's stored."""
    from .credits import SITES
    low = v.strip().lower()
    by_name = {n.lower(): k for k, n in SITES.items()}
    return {"twitter": "x", "twitter.com": "x", "x.com": "x", "da": "deviantart", "bsky": "bluesky", "fa": "furaffinity"}.get(low) or by_name.get(low) or low


def _loose(name: str) -> str:
    """A model name to compare: lower case, with spaces, underscores and dashes alike."""
    return re.sub(r"[\s_\-]+", " ", name.lower()).strip()


def _in(values: list) -> str:
    return ",".join("?" * len(values))


def _smart(db, name: str) -> str | None:
    """A smart album's search (None: an album of files you put in it, or none of that name)."""
    def load():
        with db.read() as c:
            return {r[0].lower(): r[1] for r in c.execute("SELECT name, query FROM albums WHERE query IS NOT NULL")}
    return db.cached(("smart-albums",), load).get(name.strip().lower())


def build(q: str, db=None, _depth: int = 0) -> tuple[list[str], list]:
    """-> (SQL conditions on files f, their parameters). Each filter picks its files with one indexed lookup
    (f.id IN (...)), never a lookup per file, so they stay quick on a big library. Without `db` (tests of the
    grammar) names aren't resolved and matching uses LIKE."""
    where, args, words, neg_words = [], [], [], []
    for key, v in tokens(q):
        if key and not v.strip():
            continue  # "lora:" being typed: no filter yet
        if key and key.startswith("-"):  # -tag:x: everything but what tag:x picks
            w, a = build(f'{key[1:]}:"{v}"' if " " in v else f"{key[1:]}:{v}", db, _depth)
            if w:
                where.append(f"NOT ({' AND '.join(w)})")
                args += a
            continue
        if key is None:
            words.append(v)
        elif key in ("model", "lora", "vae"):
            kinds = {"model": ("checkpoint", "unet"), "lora": ("lora",), "vae": ("vae",)}[key]
            strength = ""
            if key == "lora" and "@" in v:  # lora:name@0.6-0.8: used at that strength
                v, _, sv = v.rpartition("@")
                r = _frange(sv)
                if not r:
                    where.append("0")
                    continue
                strength = " AND strength BETWEEN ? AND ?"
            if db is None:
                where.append(f"f.id IN (SELECT file_id FROM uses WHERE kind IN ({_in(kinds)}) AND name LIKE ? ESCAPE '\\'{strength})")
                args += [*kinds, f"%{_like(v)}%", *(r if strength else [])]
                continue
            low = _loose(v)
            hits = [n for n in _names(db, kinds) if low in _loose(n)]
            if not hits:
                where.append("0")
                continue
            where.append(f"f.id IN (SELECT file_id FROM uses WHERE kind IN ({_in(kinds)}) AND name IN ({_in(hits)}){strength})")
            args += [*kinds, *hits, *(r if strength else [])]
        elif key == "strength":  # any LoRA at that strength
            r = _frange(v)
            if not r:
                where.append("0")
                continue
            where.append("f.id IN (SELECT file_id FROM uses WHERE kind = 'lora' AND strength BETWEEN ? AND ?)")
            args += r
        elif key == "date":
            span = _date_span(v)
            if not span:
                where.append("0")
                continue
            where.append("f.shot >= ? AND f.shot < ?")
            args += span
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
        elif key == "artist":
            if db is None:
                where.append("f.artist_id IS NOT NULL AND f.artist_id IN (SELECT id FROM artists WHERE name = ? COLLATE NOCASE)")
                args.append(v)
                continue
            ids = _artists(db, v)
            if not ids:
                where.append("0")
                continue
            where.append(f"f.artist_id IS NOT NULL AND f.artist_id IN ({_in(ids)})")  # (so -artist:x keeps the files with no artist)
            args += ids
        elif key == "site":  # where it's from as its details show it (not a source you, or a better one, overrode)
            where.append("f.site IS NOT NULL AND f.site = ?")  # (so -site:x keeps the files with no source)
            args.append(_site(v))
        elif key in ("color", "colour"):
            from . import colors
            cond = colors.condition(v)
            if not cond:
                where.append("0")
                continue
            where.append(cond[0])
            args += cond[1]
        elif key == "album" and db is not None and _smart(db, v) is not None:
            if _depth > 4:  # a smart album whose search names itself (or one that does): nothing, rather than forever
                where.append("0")
                continue
            w, a = build(_smart(db, v), db, _depth + 1)
            where.append("(" + " AND ".join(w) + ")" if w else "1")
            args += a
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
            else:
                where.append("0")
        elif key == "is":
            v = v.lower()
            if v in ("fav", "favorite", "favourite"):
                where.append("f.id IN (SELECT file_id FROM marks WHERE favorite = 1)")
            elif v in ("image", "video"):
                where.append("f.kind = ?")
                args.append(v)
            elif v in ("ai", "photo", "illustration", "photos", "illustrations", "generated"):  # what each file is (library.FILE_IS)
                from .library import FILE_IS
                where.append("(" + FILE_IS[{"photos": "photo", "illustrations": "illustration", "generated": "ai"}.get(v, v)] + ")")
            elif v == "rated":
                where.append("f.id IN (SELECT file_id FROM marks WHERE rating > 0)")
            elif v == "review":
                where.append("f.id IN (SELECT file_id FROM file_characters WHERE state = 'review')")
            else:
                where.append("0")  # is:something it doesn't know: nothing, rather than everything
        elif key in ("has", "no"):
            v = v.lower()
            cond = {"prompt": "f.id IN (SELECT file_id FROM generations WHERE COALESCE(positive, '') <> '')",
                    "workflow": "f.id IN (SELECT file_id FROM generations WHERE workflow IS NOT NULL)",
                    "meta": "f.meta = 1", "data": "f.meta = 1",
                    "tags": "f.id IN (SELECT file_id FROM file_tags WHERE removed = 0)",
                    "lora": "f.id IN (SELECT file_id FROM uses WHERE kind = 'lora')",
                    "artist": "f.artist_id IS NOT NULL",
                    "source": "f.site IS NOT NULL"}.get(v)
            if cond:
                where.append(cond if key == "has" else f"NOT ({cond})")
            else:
                where.append("0")
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
                args += [min(int(m.group(1)), 2 ** 31), min(int(m.group(2)), 2 ** 31)]
            else:
                where.append("0")
        elif key in ("after", "before", "on"):  # a day, or anything date: takes (yesterday, 7d, 2026-10)
            span = _date_span(v)
            if span is None:
                where.append("0")
            elif key == "after":
                where.append("f.shot >= ?")
                args.append(span[0])
            elif key == "before":
                where.append("f.shot < ?")
                args.append(span[0])
            else:
                where.append("f.shot >= ? AND f.shot < ?")
                args += span
        elif key in ("seed", "steps"):
            r = _range(v)
            if r:
                op, vals = r
                sql = f"{key} >= ? AND {key} <= ?" if len(vals) == 2 else f"{key} {op} ?"
                where.append(f"f.id IN (SELECT file_id FROM generations WHERE {sql})")
                args += vals
            else:
                where.append("0")
        elif key == "neg":
            neg_words.append(v)
        elif key == "ext":
            where.append("f.ext = ?")
            args.append("." + v.lower().lstrip("."))
    for words_, col in ((words, "{name prompt models tags}"), (neg_words, "negative")):
        want, out = _terms(words_)
        if want:
            where.append("f.id IN (SELECT rowid FROM search WHERE search MATCH ?)")
            args.append(f"{col} : ({' '.join(want)})")
        elif any(not w.startswith("-") for w in words_):
            where.append("0")  # only punctuation: nothing matches, rather than everything
        if out:  # -fog: whatever has none of these
            where.append("f.id NOT IN (SELECT rowid FROM search WHERE search MATCH ?)")
            args.append(f"{col} : ({' OR '.join(out)})")
    return where, args


def _frange(v: str):
    """'0.6-0.8', '>=0.7', '<0.5', '0.7' (give or take 0.005) -> [low, high]."""
    v = v.strip().replace(" ", "")
    num = r"-?\d+(?:\.\d+)?"
    m = re.fullmatch(f"({num})-({num})", v)
    if m:
        lo, hi = sorted((float(m.group(1)), float(m.group(2))))
        return [lo - 1e-9, hi + 1e-9]
    m = re.fullmatch(f"(>=|<=|>|<|=)?({num})", v)
    if not m:
        return None
    x, op = float(m.group(2)), m.group(1) or "="
    return {"=": [x - 0.005, x + 0.005], ">=": [x - 1e-9, 1e9], ">": [x + 1e-9, 1e9], "<=": [-1e9, x + 1e-9], "<": [-1e9, x - 1e-9]}[op]


def _date_span(v: str):
    """date:today / yesterday / 7d / 30d / 2026-10 / 2026-10-23 -> [start, end) as timestamps, in local time."""
    try:
        return _span(v.strip().lower())
    except (OverflowError, ValueError):  # date:0000-01, date:9999-12, date:99999d: no such days
        return None


def _span(v: str):
    today = datetime.date.today()
    start = lambda d: time.mktime((d.year, d.month, d.day, 0, 0, 0, 0, 0, -1))  # noqa: E731
    if v == "today":
        return [start(today), start(today + datetime.timedelta(days=1))]
    if v == "yesterday":
        return [start(today - datetime.timedelta(days=1)), start(today)]
    m = re.fullmatch(r"(\d{1,4})d", v)
    if m:  # the last N days, today included
        return [start(today - datetime.timedelta(days=int(m.group(1)) - 1)), start(today + datetime.timedelta(days=1))]
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", v)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if not 1 <= mo <= 12:
            return None
        nxt = datetime.date(y + (mo == 12), mo % 12 + 1, 1)
        return [start(datetime.date(y, mo, 1)), start(nxt)]
    d = _day(v)
    return [d, _day(v, 1)] if d is not None else None


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
