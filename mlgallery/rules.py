"""Smart folders: rules (as in Eagle's Edit Rules) kept with a smart album, and the search they come to.

A smart folder is a smart album (albums.query, a search, always up to date) that also keeps the rules it was made
from (albums.rules), so Edit Rules shows them as they were made. The rules:

    {"match": "any" | "all", "is": true | false, "rules": [rule or group, ...]}
    rule: {"field": "name", "op": "begins", "value": "INK-"}       a group: the same as the top, inside it

"any of the following are true", "all of the following are false" and so on. compile() turns them into the search
(search.py: brackets, OR, a minus for false), so a smart folder is found, counted and shown like any search, and
album:"Ink's T2I" in another search holds what it does. from_eagle() reads an Eagle smart folder's conditions.
"""
from __future__ import annotations

import datetime
import json
import re

from . import search

TEXT_OPS = [["contains", "contains"], ["excludes", "excludes"], ["begins", "begins with"], ["ends", "ends with"], ["is", "is"],
            ["regex", "matches the regex"]]
EMPTY_OPS = [["empty", "is empty"], ["notempty", "isn't empty"]]
NUMBER_OPS = [["is", "="], ["isnot", "≠"], ["gt", ">"], ["atleast", "≥"], ["lt", "<"], ["atmost", "≤"], ["between", "between"]]
DATE_OPS = [["on", "is on"], ["before", "is before"], ["onafter", "is on or after"], ["between", "is between"], ["last", "is in the last"]]
IS_OPS = [["is", "is"], ["isnot", "isn't"]]

# key: label, section (for the menu), kind (text, number, date, choice, word: typed with suggestions, none), ops, and more
FIELDS: dict[str, dict] = {
    "name": {"label": "Name", "kind": "text", "ops": TEXT_OPS, "key": "name"},
    "type": {"label": "Type", "kind": "word", "ops": IS_OPS, "suggest": ["image", "video", "png", "jpg", "webp", "gif", "mp4", "webm"]},
    "tags": {"label": "Tags", "kind": "word", "suggest_from": "tag",
             "ops": [["has", "has"], ["hasnot", "doesn't have"], ["contains", "has one containing"], ["begins", "has one beginning with"],
                     ["any", "has any"], ["none", "has none"]]},
    "album": {"label": "Albums", "kind": "word", "suggest_from": "album",
              "ops": [["in", "is in"], ["notin", "isn't in"], ["any", "is in any"], ["none", "is in none"]]},
    "folder": {"label": "Folder", "kind": "text", "key": "dir",
               "ops": [["in", "is in (or inside)"], ["notin", "isn't in"], *TEXT_OPS[:5], ["regex", "matches the regex"]]},
    "color": {"label": "Colour", "kind": "color", "ops": [["like", "looks like"], ["notlike", "doesn't look like"]]},
    "created": {"label": "Date Created", "kind": "date", "ops": DATE_OPS, "key": "created"},
    "modified": {"label": "Date Modified", "kind": "date", "ops": DATE_OPS, "key": "modified"},
    "imported": {"label": "Date Imported", "kind": "date", "ops": DATE_OPS, "key": "imported"},
    "url": {"label": "Website Link", "kind": "text", "ops": TEXT_OPS + EMPTY_OPS, "key": "url", "has": "url"},
    "note": {"label": "Note", "kind": "text", "ops": TEXT_OPS + EMPTY_OPS, "key": "note", "has": "note"},
    "width": {"label": "Width", "kind": "number", "ops": NUMBER_OPS, "key": "width", "unit": "px", "whole": True},
    "height": {"label": "Height", "kind": "number", "ops": NUMBER_OPS, "key": "height", "unit": "px", "whole": True},
    "filesize": {"label": "File Size", "kind": "number", "ops": NUMBER_OPS, "key": "filesize", "unit": "MB"},
    "shape": {"label": "Shape", "kind": "choice", "ops": IS_OPS,
              "options": [["portrait", "Portrait"], ["landscape", "Landscape"], ["square", "Square"], ["wide", "Wide (2:1 or more)"], ["tall", "Tall (1:2 or more)"]]},
    "rating": {"label": "Rating", "kind": "choice", "options": [[str(n), "★" * n] for n in range(1, 6)],
               "ops": [["is", "is"], ["isnot", "isn't"], ["atleast", "is at least"], ["atmost", "is at most"], ["unrated", "is unrated"], ["rated", "is rated"]]},
    "duration": {"label": "Video Duration", "kind": "number", "ops": NUMBER_OPS, "key": "duration", "unit": "s"},
    # the Gallery's own
    "fav": {"label": "Favourite", "kind": "none", "section": "Gallery", "ops": [["yes", "is a favourite"], ["no", "isn't a favourite"]]},
    "made": {"label": "Made as", "kind": "choice", "section": "Gallery", "ops": IS_OPS,
             "options": [["ai", "AI-made"], ["photo", "A photo"], ["illustration", "An illustration"]]},
    "model": {"label": "Checkpoint", "kind": "word", "section": "Gallery", "suggest_from": "model", "key": "model",
              "ops": [["contains", "contains"], ["excludes", "excludes"]]},
    "lora": {"label": "LoRA", "kind": "word", "section": "Gallery", "suggest_from": "lora", "key": "lora",
             "ops": [["contains", "contains"], ["excludes", "excludes"], ["any", "is any"], ["none", "is none"]]},
    "strength": {"label": "LoRA Strength", "kind": "number", "section": "Gallery", "ops": NUMBER_OPS, "key": "strength"},
    "prompt": {"label": "Prompt", "kind": "text", "section": "Gallery", "ops": TEXT_OPS + EMPTY_OPS, "key": "prompt", "has": "prompt"},
    "negative": {"label": "Negative Prompt", "kind": "text", "section": "Gallery", "ops": TEXT_OPS + EMPTY_OPS, "key": "negative", "has": "negative"},
    "sampler": {"label": "Sampler", "kind": "word", "section": "Gallery", "suggest_from": "sampler", "key": "sampler",
                "ops": [["is", "is"], ["isnot", "isn't"], ["begins", "begins with"]]},
    "scheduler": {"label": "Scheduler", "kind": "word", "section": "Gallery", "suggest_from": "scheduler", "key": "scheduler",
                  "ops": [["is", "is"], ["isnot", "isn't"], ["begins", "begins with"]]},
    "steps": {"label": "Steps", "kind": "number", "section": "Gallery", "ops": NUMBER_OPS, "key": "steps", "whole": True},
    "cfg": {"label": "CFG", "kind": "number", "section": "Gallery", "ops": NUMBER_OPS, "key": "cfg"},
    "seed": {"label": "Seed", "kind": "number", "section": "Gallery", "ops": NUMBER_OPS, "key": "seed", "whole": True},
    "workflow": {"label": "Workflow", "kind": "none", "section": "Gallery", "ops": [["yes", "is saved in it"], ["no", "isn't saved in it"]]},
    "character": {"label": "Character", "kind": "word", "section": "Gallery", "suggest_from": "character", "key": "character", "ops": IS_OPS},
    "artist": {"label": "Artist", "kind": "word", "section": "Gallery", "suggest_from": "artist", "key": "artist",
               "ops": [*IS_OPS, ["any", "is known"], ["none", "isn't known"]]},
    "site": {"label": "Source Site", "kind": "word", "section": "Gallery", "suggest_from": "site", "key": "site",
             "ops": [*IS_OPS, ["any", "is known"], ["none", "isn't known"]]},
    "search": {"label": "Search", "kind": "text", "section": "Gallery", "ops": [["matches", "finds it"], ["notmatches", "doesn't find it"]]},
}
NO_VALUE = {"empty", "notempty", "any", "none", "unrated", "rated", "yes", "no"}
MAX_DEPTH, MAX_RULES = 6, 300


def fields() -> list[dict]:
    """The fields and what each can do, for Edit Rules."""
    return [{"field": k, "section": f.get("section", "Eagle"), **{n: f[n] for n in ("label", "kind", "ops", "options", "suggest", "suggest_from", "unit") if n in f}}
            for k, f in FIELDS.items()]


def _q(v: str) -> str:
    return f'"{v}"'


def _value(v) -> str:
    if isinstance(v, (list, tuple)):
        raise ValueError("A rule's value is text.")
    v = "" if v is None else str(v).strip()
    if '"' in v:
        raise ValueError('A rule\'s value can\'t hold a " (use \' instead).')
    return v[:500]


def _number(v, whole=False) -> str:
    try:
        x = float(str(v).strip())
    except ValueError:
        raise ValueError(f"{v} isn't a number.") from None
    if x != x or abs(x) > 1e15:
        raise ValueError(f"{v} isn't a number.")
    return str(int(x)) if whole or x == int(x) else repr(x)


def _date(v) -> str:
    v = str(v).strip()
    if not re.fullmatch(r"\d{4}-\d{1,2}(-\d{1,2})?", v) or search._date_span(v) is None:
        raise ValueError(f"{v} isn't a date (2026-10-23, or 2026-10 for a month).")
    return v


def rule(r: dict) -> str | None:
    """One rule as a search filter (None: its value isn't filled in yet, so it's left out)."""
    field, op = r.get("field"), r.get("op")
    f = FIELDS.get(field)
    if not f:
        raise ValueError(f"There's no field {field}.")
    if op not in {o for o, _ in f["ops"]}:
        raise ValueError(f"{f['label']} can't be {op}.")
    kind, key = f["kind"], f.get("key")
    if op in NO_VALUE:
        if f.get("has"):
            return f"{'no' if op == 'empty' else 'has'}:{f['has']}"
        return {("tags", "any"): "has:tags", ("tags", "none"): "no:tags", ("album", "any"): "has:album", ("album", "none"): "no:album",
                ("lora", "any"): "has:lora", ("lora", "none"): "no:lora", ("artist", "any"): "has:artist", ("artist", "none"): "no:artist",
                ("site", "any"): "has:source", ("site", "none"): "no:source", ("rating", "unrated"): "rating:0", ("rating", "rated"): "is:rated",
                ("fav", "yes"): "is:fav", ("fav", "no"): "-is:fav", ("workflow", "yes"): "has:workflow", ("workflow", "no"): "no:workflow"}[(field, op)]
    raw = r.get("value")
    if kind == "number" or kind == "date":
        if op == "between":
            pair = raw if isinstance(raw, (list, tuple)) else str(raw or "").split("..")
            if len(pair) != 2 or any(str(x).strip() == "" for x in pair):
                return None
            conv = (lambda x: _number(x, f.get("whole"))) if kind == "number" else _date
            a, b = conv(pair[0]), conv(pair[1])
            return f"{key}:{a}..{b}" if kind == "date" else f"{key}:{a}-{b}"
    if field == "search":  # (a search as typed: its own quotes and all)
        v = str(raw or "").strip()[:2000]
        return (f"({v})" if op == "matches" else f"-({v})") if v else None
    v = _value(raw)
    if not v:
        return None
    if kind == "number":
        n = _number(v, f.get("whole"))
        return {"is": f"{key}:{n}", "isnot": f"-{key}:{n}", "gt": f"{key}:>{n}", "atleast": f"{key}:>={n}",
                "lt": f"{key}:<{n}", "atmost": f"{key}:<={n}"}[op]
    if kind == "date":
        if op == "last":
            days = int(float(_number(v, True)))
            if not 1 <= days <= 9999:
                raise ValueError("The last 1 to 9999 days.")
            return f"{key}:{days}d"
        d = _date(v)
        return {"on": f"{key}:{d}", "before": f"{key}:<{d}", "onafter": f"{key}:>={d}"}[op]
    if kind == "text":
        if op == "in" or op == "notin":
            return f"{'-' if op == 'notin' else ''}path:{_q(v)}"
        if op == "regex" and search.pattern(v) is None:
            raise ValueError(f"{v} isn't a regex the Gallery can read.")
        text = {"contains": f"*{v}*" if v[0] in "=/" else v, "excludes": f"*{v}*" if v[0] in "=/" else v,
                "begins": f"{v}*", "ends": f"*{v}", "is": f"={v}", "regex": f"/{v}/"}[op]
        return f"{'-' if op == 'excludes' else ''}{key}:{_q(text)}"
    if field == "tags":
        return {"has": f"tag:{_q(v)}", "hasnot": f"-tag:{_q(v)}", "contains": f"tag:{_q('*' + v + '*')}", "begins": f"tag:{_q(v + '*')}"}[op]
    if field == "album":
        return f"{'-' if op == 'notin' else ''}album:{_q(search.album_name(v))}"
    if field == "type":
        low = v.lower().lstrip(".")
        word = f"is:{low}" if low in ("image", "video") else f"ext:{_q(low)}"
        return ("-" if op == "isnot" else "") + word
    if field == "color":
        return f"{'-' if op == 'notlike' else ''}color:{_q(v)}"
    if field == "shape":
        if v not in search.SHAPES:
            raise ValueError(f"There's no shape {v}.")
        return f"{'-' if op == 'isnot' else ''}shape:{v}"
    if field == "rating":
        n = _number(v, True)
        if not 1 <= int(n) <= 5:
            raise ValueError("A rating is 1 to 5 stars.")
        return {"is": f"rating:{n}-{n}", "isnot": f"-rating:{n}-{n}", "atleast": f"rating:>={n}", "atmost": f"(rating:<={n} OR rating:0)"}[op]
    if field == "made":
        return f"{'-' if op == 'isnot' else ''}is:{v}"
    if field in ("sampler", "scheduler"):
        return {"is": f"{key}:{_q(v)}", "isnot": f"-{key}:{_q(v)}", "begins": f"{key}:{_q(v + '*')}"}[op]
    # model, lora, character, artist, site: is / contains, or not
    return f"{'-' if op in ('excludes', 'isnot') else ''}{key}:{_q(v)}"


def _group(g: dict, depth: int, count: list, top: bool) -> str | None:
    if not isinstance(g, dict) or not isinstance(g.get("rules"), list):
        raise ValueError("Rules are a group: {match, is, rules}.")
    if depth > MAX_DEPTH:
        raise ValueError(f"Groups go {MAX_DEPTH} deep at most.")
    anyof, truth = g.get("match") == "any", g.get("is", True) is not False
    parts = []
    for r in g["rules"]:
        count[0] += 1
        if count[0] > MAX_RULES:
            raise ValueError(f"{MAX_RULES} rules at most.")
        part = _group(r, depth + 1, count, False) if isinstance(r, dict) and "rules" in r else rule(r if isinstance(r, dict) else {})
        if part:
            parts.append(part)
    if not parts:
        return None
    body = (" OR " if anyof else " ").join(parts)
    if top and truth and (not anyof or len(parts) == 1):
        return body  # (all of them, true: the filters one after another, as a search is typed)
    if len(parts) == 1 and truth and not body.startswith("-"):
        return body
    return f"{'' if truth else '-'}({body})"


def compile(rules: dict) -> str:
    """The rules as a search. ValueError (a sentence to show) for rules that can't be one, or have nothing filled in."""
    one = rules.get("rules") if isinstance(rules, dict) else None
    if one and len(one) == 1 and one[0].get("field") == "search" and one[0].get("op") == "matches" and rules.get("is", True) is not False:
        q = str(one[0].get("value") or "").strip()  # (a search as it was typed: kept as it is)
        if len(q) > 4000:
            raise ValueError("That search is too long (4000 letters at most).")
        if q:
            return q
    q = _group(rules, 0, [0], True)
    if not q:
        raise ValueError("A smart folder needs a rule with something filled in.")
    if len(q) > 4000:
        raise ValueError("Those rules make too long a search (4000 letters at most).")
    return q


def tidy(rules) -> dict:
    """Rules as given (JSON text or a dict) -> the same, kept to what's known, checked by compiling them."""
    if isinstance(rules, str):
        try:
            rules = json.loads(rules)
        except ValueError:
            raise ValueError("Those rules aren't readable.") from None

    def clean(g):
        return {"match": "any" if g.get("match") == "any" else "all", "is": g.get("is", True) is not False,
                "rules": [clean(r) if "rules" in r else {"field": str(r.get("field") or ""), "op": str(r.get("op") or ""),
                                                          **({"value": r["value"]} if r.get("value") is not None else {})}
                          for r in g.get("rules") or [] if isinstance(r, dict)]}
    if not isinstance(rules, dict):
        raise ValueError("Rules are a group: {match, is, rules}.")
    out = clean(rules)
    compile(out)
    return out


def from_query(q: str) -> dict:
    """A smart album's search as rules (a smart album from before Edit Rules): one Search rule."""
    return {"match": "all", "is": True, "rules": [{"field": "search", "op": "matches", "value": q}]}


def rename_album(rules: dict, fn) -> dict:
    """The rules with each album named in them through fn (name -> its new name, or None to leave it)."""
    def walk(g):
        out = []
        for r in g.get("rules") or []:
            if "rules" in r:
                out.append(walk(r))
            elif r.get("field") == "album" and isinstance(r.get("value"), str) and fn(r["value"]):
                out.append({**r, "value": fn(r["value"])})
            elif r.get("field") == "search" and isinstance(r.get("value"), str):
                out.append({**r, "value": search.ALBUM_FILTER.sub(lambda m: _renamed(m, fn), r["value"])})
            else:
                out.append(r)
        return {**g, "rules": out}
    return walk(rules)


def _renamed(m, fn):
    new = fn(m.group(2) if m.group(2) is not None else m.group(3))
    return f'{m.group(1)}"{new}"' if new else m.group(0)


# ---------------------------------------------------------------- Eagle's smart folders
_EAGLE_FIELDS = {"name": "name", "filename": "name", "type": "type", "ext": "type", "extension": "type", "tags": "tags", "tag": "tags",
                 "folders": "album", "folder": "album", "foldername": "albumname", "color": "color", "colors": "color", "palettes": "color",
                 "btime": "imported", "importtime": "imported", "dateimported": "imported", "added": "imported",
                 "mtime": "modified", "modificationtime": "modified", "lastmodified": "modified", "datemodified": "modified",
                 "ctime": "created", "createtime": "created", "creationtime": "created", "datecreated": "created", "birthtime": "created",
                 "url": "url", "website": "url", "websitelink": "url", "link": "url", "annotation": "note", "note": "note",
                 "width": "width", "height": "height", "size": "filesize", "filesize": "filesize", "shape": "shape",
                 "rating": "rating", "star": "rating", "stars": "rating", "duration": "duration", "videoduration": "duration"}
_EAGLE_OPS = {"contain": "contains", "contains": "contains", "uncontain": "excludes", "notcontain": "excludes", "excludes": "excludes",
              "startwith": "begins", "beginswith": "begins", "endwith": "ends", "endswith": "ends", "equal": "is", "equals": "is", "is": "is",
              "unequal": "isnot", "notequal": "isnot", "isnot": "isnot", "empty": "empty", "isempty": "empty", "null": "empty",
              "notempty": "notempty", "isnotempty": "notempty", "notnull": "notempty", "regex": "regex",
              "=": "is", "==": "is", "!=": "isnot", ">": "gt", ">=": "atleast", "<": "lt", "<=": "atmost", "between": "between",
              "union": "any_of", "intersection": "all_of", "identity": "all_of", "within": "last", "before": "before", "after": "onafter"}


def _eagle_date(v) -> str | None:
    if isinstance(v, (int, float)) or (isinstance(v, str) and re.fullmatch(r"\d{9,}", v)):
        t = float(v) / (1000 if float(v) > 1e11 else 1)  # (Eagle's times are in milliseconds)
        try:
            return datetime.date.fromtimestamp(t).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(v, str) and re.match(r"\d{4}-\d{1,2}-\d{1,2}", v):
        return v[:10]
    return None


def _eagle_rule(r: dict, folder_names: dict) -> tuple[dict | None, str | None]:
    """One Eagle rule -> (a rule or group here, None) or (None, why it was left out)."""
    prop, method = str(r.get("property") or ""), str(r.get("method") or "")
    field = _EAGLE_FIELDS.get(re.sub(r"[^a-z]", "", prop.lower()))
    m = method.strip().lower()
    op = _EAGLE_OPS.get(m) or _EAGLE_OPS.get(re.sub(r"[^a-z]", "", m))
    what = f"{prop} {method}".strip()
    if not field or not op:
        return None, f"{what}: the Gallery has no such rule" if not field else f"{what}: the Gallery can't compare that way"
    v = r.get("value")
    vals = v if isinstance(v, list) else [v]
    vals = [x for x in vals if x is not None and str(x) != ""]
    if field == "albumname":  # a folder by its name: only "is" has a match here
        field, op = "album", {"is": "in", "isnot": "notin", "contains": "in"}.get(op, op)
    if field == "album":
        vals = [folder_names.get(str(x), str(x)) for x in vals]
        op = {"contains": "in", "any_of": "in", "is": "in", "all_of": "in_all", "excludes": "notin", "isnot": "notin",
              "empty": "none", "notempty": "any"}.get(op, op)
    if field == "tags":
        op = {"contains": "has", "is": "has", "any_of": "has", "all_of": "has_all", "excludes": "hasnot", "isnot": "hasnot",
              "empty": "none", "notempty": "any"}.get(op, op)
    if field == "rating" and vals:  # (whole stars: > 3 is at least 4; at least 0 is any; at most 0 is unrated)
        try:
            n = int(float(vals[0])) + {"gt": 1, "lt": -1}.get(op, 0)
        except (TypeError, ValueError):
            return None, f"{what}: its rating couldn't be read"
        op = {"gt": "atleast", "lt": "atmost"}.get(op, op)
        if n < 1:
            got = {"is": "unrated", "atmost": "unrated", "isnot": "rated"}.get(op)
            return ({"field": "rating", "op": got}, None) if got else (None, f"{what}: every picture is that")
        if n > 5:
            return None, f"{what}: no rating is that"
        vals = [n]
    if field in ("created", "modified", "imported"):
        if op == "last":
            pass
        elif op in ("gt", "atleast"):
            op = "onafter"
        elif op in ("lt", "atmost"):
            op = "before"
        elif op == "is":
            op = "on"
        if op != "last":
            vals = [_eagle_date(x) for x in vals]
            if not vals or None in vals:
                return None, f"{what}: its date couldn't be read"
    if field == "filesize" and vals:  # (Eagle's sizes are in bytes; here, megabytes)
        try:
            vals = [round(float(x) / (1 << 20), 3) for x in vals]
        except (TypeError, ValueError):
            return None, f"{what}: its size couldn't be read"
    if field == "color" and vals and isinstance(vals[0], (list, tuple)) and len(vals[0]) >= 3:
        vals = ["#%02x%02x%02x" % tuple(int(c) for c in vals[0][:3])]
    if field == "color":
        op = "notlike" if op in ("excludes", "isnot") else "like"
    if field == "type":
        op = "isnot" if op in ("isnot", "excludes") else "is"
    if field == "shape":
        vals = [{"horizontal": "landscape", "vertical": "portrait", "panorama": "wide", "panoramah": "wide", "panoramav": "tall",
                 "panoramahorizontal": "wide", "panoramavertical": "tall"}.get(re.sub(r"[^a-z]", "", str(x).lower()), str(x).lower()) for x in vals]
        op = "isnot" if op in ("isnot", "excludes") else "is"
    # several values: one rule each (any of them, or all of them)
    if op in ("in_all", "has_all", "any_of", "all_of") or (len(vals) > 1 and op != "between"):
        anyof = op not in ("in_all", "has_all", "all_of", "notin", "hasnot", "isnot", "excludes")
        one = {"in_all": "in", "has_all": "has", "any_of": "is", "all_of": "is"}.get(op, op)
        rules = [{"field": field, "op": one, "value": str(x)} for x in vals]
        return ({"match": "any" if anyof else "all", "is": True, "rules": rules} if len(rules) > 1 else rules[0] if rules else None), \
            None if rules else f"{what}: it has no value"
    if op not in {o for o, _ in FIELDS[field]["ops"]}:
        return None, f"{what}: the Gallery can't compare {FIELDS[field]['label']} that way"
    if op in NO_VALUE:
        return {"field": field, "op": op}, None
    if op == "between":
        if len(vals) != 2:
            return None, f"{what}: between needs two values"
        return {"field": field, "op": op, "value": [str(x) for x in vals]}, None
    if not vals:
        return None, f"{what}: it has no value"
    value = str(vals[0]).replace('"', "'")
    return {"field": field, "op": op, "value": value}, None


def from_eagle(sf: dict, folder_names: dict) -> tuple[dict | None, list[str]]:
    """An Eagle smart folder's conditions -> (rules, the rules left out and why). Eagle's groups all have to hold."""
    groups, skipped = [], []
    for cond in sf.get("conditions") or []:
        if not isinstance(cond, dict):
            continue
        rules = []
        for r in cond.get("rules") or []:
            got, why = _eagle_rule(r, folder_names) if isinstance(r, dict) else (None, "a rule that couldn't be read")
            if got:
                rules.append(got)
            elif why:
                skipped.append(why)
        if rules:
            groups.append({"match": "any" if str(cond.get("match") or "").upper() == "OR" else "all",
                           "is": str(cond.get("boolean", "TRUE")).upper() != "FALSE", "rules": rules})
    if not groups:
        return None, skipped
    out = groups[0] if len(groups) == 1 else {"match": "all", "is": True, "rules": groups}
    try:
        return tidy(out), skipped
    except ValueError as ex:
        return None, [*skipped, str(ex)]
