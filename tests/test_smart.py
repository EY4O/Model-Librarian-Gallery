"""Smart folders (rules.py, Edit Rules): rules kept with a smart album, the search they come to (brackets, OR, name:,
dates, sizes and shapes), keeping up as pictures change, and Eagle's smart folders brought in."""
import json
import os
import time

import pytest

from conftest import image
from mlgallery import bulk, eagle, fileops, rules, scan, search
from test_eagle import FOLDERS, make_eagle, setup
from test_photos import folder

T = time.mktime((2024, 6, 12, 14, 30, 0, 0, 0, -1))


def q(lib, text):
    return sorted(r["name"] for r in lib.files(limit=100, q=text)["items"])


def library(lib, media):
    for i, (name, size) in enumerate([("INK-001.png", (64, 48)), ("INI-002.png", (48, 64)), ("ink-big.png", (300, 100)),
                                      ("other.png", (64, 64)), ("Sub/INK-003.png", (64, 48))]):
        p = image(media / name, size, seed=i)
        os.utime(p, (T + i * 86400, T + i * 86400))
    f = folder(lib, media)
    scan.scan_folder(lib.db, f)
    return f, {v["name"]: v["id"] for v in lib.files(limit=50)["items"]}


def settled(lib, name, n):
    """A smart album's count, once counted again behind the scenes."""
    deadline = time.time() + 5
    while time.time() < deadline:
        a = next(x for x in lib.albums() if x["name"] == name)
        if a["n"] == n and not a.get("stale"):
            return a
        time.sleep(0.05)
    return next(x for x in lib.albums() if x["name"] == name)


# ---------------------------------------------------------------- the search: groups, OR, and the new filters
def test_brackets_and_or_in_a_search(lib, media):
    library(lib, media)
    assert q(lib, "name:INK-*") == ["INK-001.png", "INK-003.png", "ink-big.png"]  # (begins with, any case)
    assert q(lib, "name:INK-* OR name:INI-*") == ["INI-002.png", "INK-001.png", "INK-003.png", "ink-big.png"]
    assert q(lib, "(name:INK-* OR name:INI-*) shape:landscape") == ["INK-001.png", "INK-003.png", "ink-big.png"]
    assert q(lib, "-(name:INK-* OR name:INI-*)") == ["other.png"]
    assert q(lib, "name:*big.png") == ["ink-big.png"] and q(lib, "name:=other.png") == ["other.png"] and q(lib, "name:her") == ["other.png"]
    assert q(lib, r"name:/^in[ik]-\d+/") == ["INI-002.png", "INK-001.png", "INK-003.png"]
    assert q(lib, "name:/[unclosed/") == []  # (not a regex: nothing, not an error)
    assert q(lib, "dir:sub") == ["INK-003.png"] and q(lib, "-dir:sub name:INK-*") == ["INK-001.png", "ink-big.png"]
    assert q(lib, "width:>100") == ["ink-big.png"] and q(lib, "height:60-70") == ["INI-002.png", "other.png"]
    assert q(lib, "shape:portrait") == ["INI-002.png"] and q(lib, "shape:square") == ["other.png"] and q(lib, "shape:wide") == ["ink-big.png"]
    assert q(lib, "filesize:<1mb") == q(lib, "") and q(lib, "filesize:>1gb") == []
    assert q(lib, "modified:2024-06-13") == ["INI-002.png"] and q(lib, "modified:<2024-06-13") == ["INK-001.png"]
    assert q(lib, "modified:2024-06-13..2024-06-14") == ["INI-002.png", "ink-big.png"]
    assert q(lib, "modified:>=2024-06-15") == ["INK-003.png", "other.png"] and q(lib, "imported:1d") == q(lib, "")
    # an unclosed bracket ends with the search; a stray OR picks nothing more
    assert q(lib, "(name:INK-* OR name:INI-*") == q(lib, "name:INK-* OR name:INI-*") and q(lib, "name:INI-* OR") == ["INI-002.png"]


def test_a_bracket_in_a_name_is_still_the_name():
    lex = lambda s: [t for t in search._lex(s) if t[0] == "t"]  # noqa: E731
    assert lex('lora:"char (v2)" x') == [("t", ("lora", "char (v2)")), ("t", (None, "x"))]
    assert lex("(lora:char(v2))") == [("t", ("lora", "char(v2)"))]
    assert lex("lora:char(v2)") == [("t", ("lora", "char(v2)"))]  # (no group open: the ) is the name's)
    assert search.build("lora:a rating:4") == search._flat(search.tokens("lora:a rating:4"))  # a plain search: as it always was


# ---------------------------------------------------------------- rules
ANY_INK = {"match": "any", "is": True, "rules": [{"field": "name", "op": "begins", "value": "INI-"}, {"field": "name", "op": "begins", "value": "INK-"}]}


def test_rules_come_to_a_search():
    assert rules.compile(ANY_INK) == '(name:"INI-*" OR name:"INK-*")'
    nested = {"match": "all", "is": True, "rules": [ANY_INK, {"field": "rating", "op": "atleast", "value": "4"},
                                                    {"match": "any", "is": False, "rules": [{"field": "tags", "op": "has", "value": "wip"}, {"field": "fav", "op": "no"}]}]}
    assert rules.compile(nested) == '(name:"INI-*" OR name:"INK-*") rating:>=4 -(tag:"wip" OR -is:fav)'
    assert rules.compile({"match": "all", "is": False, "rules": [{"field": "created", "op": "between", "value": ["2026-10-01", "2026-10-23"]},
                                                                 {"field": "width", "op": "gt", "value": "1000"}]}) == "-(created:2026-10-01..2026-10-23 width:>1000)"
    assert rules.compile(rules.from_query('lora:"paper cut" rating:4')) == 'lora:"paper cut" rating:4'  # a smart album's search, as it was
    assert rules.compile({"match": "all", "rules": [{"field": "name", "op": "contains", "value": ""}, {"field": "note", "op": "empty"}]}) == "no:note"
    for bad, why in [({"match": "all", "rules": [{"field": "name", "op": "contains", "value": ""}]}, "something filled in"),
                     ({"match": "all", "rules": [{"field": "name", "op": "regex", "value": "[x"}]}, "regex"),
                     ({"match": "all", "rules": [{"field": "name", "op": "is", "value": 'a"b'}]}, "can't hold"),
                     ({"match": "all", "rules": [{"field": "width", "op": "gt", "value": "wide"}]}, "isn't a number"),
                     ({"match": "all", "rules": [{"field": "created", "op": "on", "value": "2026-13-40"}]}, "isn't a date"),
                     ({"match": "all", "rules": [{"field": "nope", "op": "is", "value": "x"}]}, "no field")]:
        with pytest.raises(ValueError, match=why):
            rules.compile(bad)
    assert {f["field"] for f in rules.fields()} >= {"name", "type", "tags", "album", "folder", "color", "created", "modified", "imported", "url",
                                                    "note", "width", "height", "filesize", "shape", "rating", "duration", "fav", "lora", "prompt"}


def test_a_smart_folder_keeps_up_and_keeps_its_rules(lib, media):
    f, ids = library(lib, media)
    a = lib.album_save(None, "Ink's T2I", rules=ANY_INK)
    assert a["query"] == '(name:"INI-*" OR name:"INK-*")' and a["rules"] == {**ANY_INK, "match": "any"}
    assert settled(lib, "Ink's T2I", 4)["n"] == 4 and q(lib, "album:\"Ink's T2I\"") == ["INI-002.png", "INK-001.png", "INK-003.png", "ink-big.png"]
    assert lib.smart_preview(ANY_INK) == {"query": a["query"], "n": 4}
    # a new picture that meets them comes in by itself; one that's renamed away goes
    image(media / "INK-004.png", seed=20)
    scan.scan_folder(lib.db, f)
    assert settled(lib, "Ink's T2I", 5)["n"] == 5
    fileops.trash(lib.db, [ids["other.png"], ids["ink-big.png"]])
    assert settled(lib, "Ink's T2I", 4)["n"] == 4
    # edited: other rules; renamed: the same rules
    two = {"match": "all", "is": True, "rules": [ANY_INK, {"field": "shape", "op": "is", "value": "portrait"}]}
    lib.album_save(a["id"], "Ink's T2I", rules=two)
    assert settled(lib, "Ink's T2I", 1)["n"] == 1
    r = lib.album_save(a["id"], "Ink portraits")
    assert r["rules"] == two and r["query"] == rules.compile(two)
    # deleted, and back with Undo: rules and all
    d = lib.album_delete(a["id"])
    assert all(x["name"] != "Ink portraits" for x in lib.albums())
    fileops.undo(lib.db, d["op"])
    back = next(x for x in lib.albums() if x["name"] == "Ink portraits")
    assert back["rules"] == two and back["query"] == rules.compile(two)
    # changed to a plain search: its one rule is that search
    lib.album_save(a["id"], "Ink portraits", query="rating:5")
    assert next(x for x in lib.albums() if x["name"] == "Ink portraits")["rules"] == rules.from_query("rating:5")


def test_an_album_renamed_is_renamed_in_the_rules_too(lib, media):
    f, ids = library(lib, media)
    bulk.apply(lib.db, [ids["other.png"]], "album", "Trips / Coast")
    keep = {"match": "all", "is": True, "rules": [{"field": "album", "op": "in", "value": "Trips / Coast"},
                                                 {"field": "search", "op": "matches", "value": "album:trips"}]}
    lib.album_save(None, "From trips", rules=keep)
    r = lib.album_move("Trips", "Holidays")
    a = next(x for x in lib.albums() if x["name"] == "From trips")
    assert a["rules"]["rules"][0]["value"] == "Holidays / Coast" and a["rules"]["rules"][1]["value"] == 'album:"Holidays"'
    assert q(lib, 'album:"From trips"') == ["other.png"]
    fileops.undo(lib.db, r["op"])
    a = next(x for x in lib.albums() if x["name"] == "From trips")
    assert a["rules"] == keep and q(lib, 'album:"From trips"') == ["other.png"]


# ---------------------------------------------------------------- Eagle's smart folders
def eagle_smart(eag, smart):
    meta = json.loads((eag / "metadata.json").read_text())
    meta["smartFolders"] = smart
    (eag / "metadata.json").write_text(json.dumps(meta))


def test_eagles_smart_folders_come_in_with_their_rules(lib, tmp_path):
    media, f, ids, eag = setup(lib, tmp_path)
    eagle_smart(eag, [
        {"id": "S1", "name": "Ink's T2I", "conditions": [{"match": "OR", "boolean": "TRUE", "rules": [
            {"property": "name", "method": "startWith", "value": "INI-"}, {"property": "name", "method": "startWith", "value": "one"}]}],
         "children": [{"id": "S2", "name": "Rated", "conditions": [{"match": "AND", "rules": [{"property": "rating", "method": ">=", "value": [4]}]}]}]},
        {"id": "S3", "name": "On the coast", "conditions": [{"match": "AND", "rules": [
            {"property": "folders", "method": "contain", "value": ["F3"]}, {"property": "comments", "method": "contain", "value": "x"}]}]},
        {"id": "S4", "name": "Only comments", "conditions": [{"match": "AND", "rules": [{"property": "comments", "method": "contain", "value": "x"}]}]},
        {"id": "S5", "name": "Not big, not dusk", "conditions": [{"match": "AND", "boolean": "FALSE", "rules": [
            {"property": "width", "method": ">", "value": [1000]}, {"property": "tags", "method": "union", "value": ["dusk", "keeper"]}]}]},
    ])
    plan = eagle.plan_import(lib.db, str(eag))
    s = eagle.summary(plan)
    assert s["smart"] == ["Ink's T2I", "Ink's T2I / Rated", "On the coast", "Not big, not dusk"]
    assert any(x.startswith("On the coast: comments contain") for x in s["smart_skipped"]) and any(x.startswith("Only comments:") for x in s["smart_skipped"])
    lib.album_save(None, "Not big, not dusk")  # (an album of that name already: Eagle's isn't made over it)
    r = eagle.apply_import(lib.db, plan)
    assert r["smart"] == 3
    got = {a["name"]: a for a in lib.albums() if a["query"] is not None}
    assert set(got) == {"Ink's T2I", "Ink's T2I / Rated", "On the coast"}
    assert got["Ink's T2I"]["query"] == '(name:"INI-*" OR name:"one*")'
    assert got["Ink's T2I / Rated"]["query"] == '(name:"INI-*" OR name:"one*") rating:>=4'  # (inside it: both its rules and its own)
    assert got["On the coast"]["query"] == 'album:"Coast"'
    assert q(lib, "album:\"Ink's T2I\"") == ["one.png"] and q(lib, 'album:"On the coast"') == ["one.png"]
    fileops.undo(lib.db, r["op"])
    assert not [a for a in lib.albums() if a["query"] is not None]


def test_eagle_rules_the_gallery_reads():
    names = {"F3": "Coast"}
    one = lambda prop, method, value: rules._eagle_rule({"property": prop, "method": method, "value": value}, names)  # noqa: E731
    assert one("name", "uncontain", "wip") == ({"field": "name", "op": "excludes", "value": "wip"}, None)
    assert one("name", "not-empty", "")[0] is None  # (every picture has a name: Name has no empty here)
    assert one("url", "not-empty", "") == ({"field": "url", "op": "notempty"}, None)
    assert one("annotation", "empty", None) == ({"field": "note", "op": "empty"}, None)
    assert one("type", "equal", "png") == ({"field": "type", "op": "is", "value": "png"}, None)
    assert one("rating", ">", [3]) == ({"field": "rating", "op": "atleast", "value": "4"}, None)
    assert one("rating", "=", [0]) == ({"field": "rating", "op": "unrated"}, None)
    assert one("height", "between", [100, 200]) == ({"field": "height", "op": "between", "value": ["100", "200"]}, None)
    assert one("folders", "uncontain", ["F3"]) == ({"field": "album", "op": "notin", "value": "Coast"}, None)
    assert one("tags", "intersection", ["a", "b"])[0] == {"match": "all", "is": True, "rules": [{"field": "tags", "op": "has", "value": "a"},
                                                                                              {"field": "tags", "op": "has", "value": "b"}]}
    assert one("mtime", ">", 1790830800000)[0] == {"field": "modified", "op": "onafter", "value": time.strftime("%Y-%m-%d", time.localtime(1790830800))}
    assert one("comments", "contain", "x")[0] is None
