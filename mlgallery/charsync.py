"""Your characters kept in step with Model Librarian's character list (characters.json, see charstore.py), both ways.

Every character (not people) is in both, in a library for AI generations or illustrations (a library only for photos
keeps out of it). The Gallery keeps its name and aliases in its own columns and the rest - the list's id, series, booru
tag, appearance, outfits, LoRA, picture, and when each part changed - as `shared` JSON. Each sync, under the list's lock:

- a character in both: each part from whichever side changed it last (charstore.merge_parts)
- one only in the list: linked to the Gallery's character of the same name, or made in the Gallery, with a rule to
  recognise it: its tag as a strong word rule (added once: one you remove stays out). Its LoRA adds no rule: a LoRA
  linked for Character Select and the Gallery's rules are kept apart
- one only in the Gallery: added to the list
- deleted in the Gallery: deleted from the list; deleted from the list: deleted in the Gallery
- a portrait picked in the Gallery: a small copy becomes its picture in the list (made before the lock is taken)

What the Gallery can't hold (a name one of your people has, more aliases than it keeps) stays the Gallery's way, and
the list is told so: nothing goes back and forth. The list is written first and the lock let go; the Gallery's changes
(which may match files again) come after, each only if that character hasn't changed meanwhile. Without Model
Librarian's folder nothing happens.
"""
from __future__ import annotations

import io
import json
import os
import re
import threading
import time

from . import charstore
from .charfiles import _Lock, _write, read_list

LOCAL = ("cover_sent", "rules_for")  # the Gallery's own bookkeeping in `shared`, never in the list
PICTURE_PX = 256
NAME_MAX, ALIASES_MAX = 100, 16    # what the Gallery keeps of a name and its aliases (library.character_save)
_lock = threading.Lock()  # one sync at a time in this Gallery


def display(tag) -> str:
    """A booru tag as it's written in a prompt (and so in a word rule): spaces, no brackets escaped."""
    return re.sub(r"\s+", " ", str(tag or "").replace("_", " ")).strip()


def record(row: dict, sid: str, now: float) -> dict:
    """A Gallery character in the list's shape (its parts' change times from `shared`; new to the list: changed now)."""
    sh = dict(row.get("shared") or {})
    rec = {k: v for k, v in sh.items() if k not in LOCAL}
    rec.update(id=sid, name=row["name"], aliases=list(row.get("aliases") or []))
    rec.setdefault("series", "")
    rec.setdefault("tag", "")
    rec.setdefault("appearance", [])
    rec.setdefault("outfits", {})
    rec.setdefault("lora", None)
    rec.setdefault("image", None)
    rec.setdefault("source", {})
    rec.setdefault("created", row.get("created") or now)
    if not sh.get("id"):  # never in the list: all of it is news to it
        rec["changed"] = {p: max(float((sh.get("changed") or {}).get(p) or 0), now) for p in charstore.PARTS}
        rec["updated"] = now
    return rec


def _fit(m: dict, now: float) -> None:
    """A name and aliases as the Gallery keeps them (changed in place, and stamped so the list takes them too)."""
    name = str(m.get("name") or "Character").strip()[:NAME_MAX] or "Character"
    seen, aliases = {name.lower()}, []
    for a in m.get("aliases") or []:
        a = str(a).strip()[:NAME_MAX]
        if a and a.lower() not in seen:
            seen.add(a.lower())
            aliases.append(a)
    aliases = aliases[:ALIASES_MAX]
    parts = (["name"] if name != m.get("name") else []) + (["aliases"] if aliases != list(m.get("aliases") or []) else [])
    m["name"], m["aliases"] = name, aliases
    if parts:
        charstore.stamp(m, parts, now)


def _gallery_side(m: dict, old: dict | None) -> dict:
    """`shared` for the Gallery from a merged character: everything but name and aliases, and the Gallery's bookkeeping."""
    out = {k: v for k, v in m.items() if k not in ("name", "aliases")}
    for k in LOCAL:
        if old and k in old:
            out[k] = old[k]
    return out


def plan(data: dict, rows: list[dict], gone: list[tuple[str, float]], now: float, people=()) -> tuple[bool, list[tuple]]:
    """What to do: the list is changed in place (-> whether it changed); the Gallery's part comes back as actions:
    ("save", gid, name, aliases, shared, rules, was) a character changed or linked (was: its `shared` as read, so it's only
    saved if nothing changed it since); ("create", None, name, aliases, shared, rules, None); ("delete", gid);
    ("forget", shared_id) a deletion the list now knows of. rules: [(kind, value)] to add. people: the names of the
    library's people (taken: a character can't have one)."""
    charstore.check(data)
    before = json.dumps(data, sort_keys=True)
    acts: list[tuple] = []
    for sid, at in gone:  # deleted here
        charstore.delete(data, sid, at)
        acts.append(("forget", sid))
    dead = charstore.deleted_ids(data)
    store = {c["id"]: c for c in data["characters"]}
    linked = {r["shared"]["id"]: r for r in rows if (r.get("shared") or {}).get("id")}
    free = {r["name"].lower(): r for r in rows if not (r.get("shared") or {}).get("id")}
    taken = {r["name"].lower() for r in rows} | {str(p).lower() for p in people}

    def to_gallery(m: dict, row: dict | None) -> tuple:
        old = (row or {}).get("shared")
        had = dict((old or {}).get("rules_for") or {})
        now_for = {"tag": display(m.get("tag"))}
        add = [("word", now_for["tag"])] if now_for["tag"] and now_for["tag"] != had.get("tag") else []
        sh = _gallery_side(m, old)
        sh["rules_for"] = now_for
        return sh, add

    def was(row: dict) -> str | None:
        return row.get("raw")

    for sid, sc in list(store.items()):
        row = linked.pop(sid, None)
        if row is None and str(sc.get("name") or "").lower() in free:  # the Gallery's character of that name: linked, the list's presets kept
            row = free.pop(str(sc["name"]).lower())
            m = dict(sc)
            m["aliases"] = list(sc.get("aliases") or []) + list(row.get("aliases") or [])
            m["name"] = row["name"]
            _fit(m, now)
            sc.clear()
            sc.update(m)
            sh, add = to_gallery(m, row)
            acts.append(("save", row["id"], m["name"], m["aliases"], sh, add, was(row)))
            continue
        if row is None:  # new to the Gallery
            m = dict(sc)
            _fit(m, now)
            name = charstore._unique(m["name"], taken)
            if name != m["name"]:
                m["name"] = name
                charstore.stamp(m, ["name"], now)
            taken.add(name.lower())
            sc.clear()
            sc.update(m)
            sh, add = to_gallery(m, None)
            acts.append(("create", None, m["name"], m["aliases"], sh, add, None))
            continue
        m = charstore.merge_parts(sc, record(row, sid, now))
        _fit(m, now)
        if m["name"].lower() != row["name"].lower() and m["name"].lower() in taken:  # one of yours is called that: yours stays
            m["name"] = row["name"]
            charstore.stamp(m, ["name"], now)
        taken.discard(row["name"].lower())
        taken.add(m["name"].lower())
        sc.clear()
        sc.update(m)
        sh, add = to_gallery(m, row)
        portrait = row.get("cover") and (row.get("shared") or {}).get("cover_sent") != row["cover"]
        if sh != row.get("shared") or m["name"] != row["name"] or m["aliases"] != list(row.get("aliases") or []) or add or portrait:
            acts.append(("save", row["id"], m["name"], m["aliases"], sh, add, was(row)))
    for sid, row in linked.items():  # linked, but not in the list
        if sid in dead:
            acts.append(("delete", row["id"]))
        else:  # the list lost it (a new folder, say): it goes back
            rec = record(row, sid, now)
            _fit(rec, now)
            data["characters"].append(rec)
            acts.append(("save", row["id"], rec["name"], rec["aliases"], _gallery_side(rec, row.get("shared")), [], was(row)))
    for row in free.values():  # only in the Gallery
        rec = record(row, charstore.new_id(), now)
        _fit(rec, now)
        data["characters"].append(rec)
        sh, _ = to_gallery(rec, row)
        acts.append(("save", row["id"], rec["name"], rec["aliases"], sh, [], was(row)))
    return json.dumps(data, sort_keys=True) != before, acts


def _picture(lib, file_id: int) -> bytes | None:
    """A small WebP of a file, from its thumbnail (None when it can't be made)."""
    try:
        from PIL import Image
        path = lib.thumbs.get(int(file_id))
        if not path:
            return None
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((PICTURE_PX, PICTURE_PX))
            out = io.BytesIO()
            im.save(out, "WEBP", quality=85)
            return out.getvalue()
    except Exception:
        return None


def _rows(lib) -> tuple[list[dict], list[tuple[str, float]], list[str]]:
    with lib.db.read() as c:
        rows = [{"id": r["id"], "name": r["name"], "aliases": json.loads(r["aliases"] or "[]"), "cover": r["cover"], "created": r["created"],
                 "shared": json.loads(r["shared"]) if r["shared"] else None, "raw": r["shared"]}
                for r in c.execute("SELECT id, name, aliases, cover, created, shared FROM characters WHERE who='character'")]
        gone = [(r[0], r[1]) for r in c.execute("SELECT shared_id, at FROM characters_deleted")]
        people = [r[0] for r in c.execute("SELECT name FROM characters WHERE who<>'character'")]
    return rows, gone, people


def sync(lib, ml_dir: str | None, now: float | None = None) -> dict:
    """One sync with Model Librarian's list in ml_dir -> {list: written?, gallery: actions done}. ValueError when the
    list is broken or from a newer version; TimeoutError when Model Librarian holds it too long."""
    if not ml_dir or not os.path.isdir(ml_dir):
        return {"list": False, "gallery": 0}
    now = now or time.time()
    path = os.path.join(ml_dir, charstore.FILE)
    with _lock:
        # portraits picked here, made into pictures before the list is locked (a thumbnail can take a moment)
        rows, _, _ = _rows(lib)
        pics = {(r["id"], r["cover"]): _picture(lib, r["cover"]) for r in rows
                if r["cover"] and (r.get("shared") or {}).get("id") and (r.get("shared") or {}).get("cover_sent") != r["cover"]}
        with _Lock(path):
            data = read_list(path)
            rows, gone, people = _rows(lib)
            changed, acts = plan(data, rows, gone, now, people)
            byid = {c["id"]: c for c in data["characters"]}
            for a in acts:
                if a[0] != "save":
                    continue
                row = next((r for r in rows if r["id"] == a[1]), None)
                sh = a[4]
                if not row or not row["cover"] or sh.get("cover_sent") == row["cover"]:
                    continue
                sh["cover_sent"] = row["cover"]  # (tried, whether or not a picture could be made: not again every round)
                pic, sc = pics.get((row["id"], row["cover"])), byid.get(sh.get("id"))
                if pic and sc:
                    rel = charstore.picture_path(sc["id"], "webp")
                    os.makedirs(os.path.join(ml_dir, charstore.PICTURES), exist_ok=True)
                    with open(os.path.join(ml_dir, *rel.split("/")), "wb") as f:
                        f.write(pic)
                    sc["image"] = rel
                    charstore.stamp(sc, ["image"], now)
                    sh.update(image=rel, changed=dict(sc["changed"]), updated=sc["updated"])
                    changed = True
            if changed:
                _write(path, data)
        done = lib.apply_character_sync(acts) if acts else 0
    return {"list": changed, "gallery": done}
