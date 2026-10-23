"""Characters: a name plus rules that mark the files it's in, and what you confirmed or rejected.

    lora  a LoRA whose name contains this (any version or strength)      strong: counts straight away
    word  words in the positive prompt (a trigger word, as a phrase)    strong
    tag   a prompt part or one of your tags, exactly                    weak: waits in To review

A match is stored per file and character with its state: `auto` (a strong rule), `review` (only weak rules),
`confirmed` (you, or the workflow's Gallery Info node named it) or `rejected` (you said no: never suggested
again for that file). Statistics count `auto` and `confirmed`. A weak rule you've mostly said no to (5 or
more, three times as many no as yes) stops sending files to review until you turn it back on.
"""
from __future__ import annotations

import json
import re
import time

COUNTED = ("auto", "confirmed")
MUTE_AFTER = 5


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[_\-]+", " ", str(s or ""))).strip().lower()


def _fts_phrase(words: str) -> str | None:
    w = norm(words).replace('"', "")
    return f'prompt : "{w}"' if w else None


def rule_files(c, rule) -> set[int]:
    """The files one rule matches (whatever their state)."""
    kind, value = rule["kind"], rule["value"]
    if kind == "lora":
        v = norm(value)
        rows = c.execute("SELECT DISTINCT file_id, name FROM uses WHERE kind='lora'").fetchall() if v else []
        return {r[0] for r in rows if v in norm(r[1])}
    if kind == "word":
        q = _fts_phrase(value)
        return {r[0] for r in c.execute("SELECT rowid FROM search WHERE search MATCH ?", (q,))} if q else set()
    if kind == "tag":
        v = norm(value)
        a = {r[0] for r in c.execute("SELECT file_id FROM prompt_tags WHERE tag = ?", (v,))}
        b = {r[0] for r in c.execute("SELECT ft.file_id FROM file_tags ft JOIN tags t ON t.id=ft.tag_id WHERE ft.removed=0 AND t.name = ? COLLATE NOCASE", (value,))}
        return a | b
    return set()


def _file_matches(c, rule, file_id: int) -> bool:
    kind, value = rule["kind"], rule["value"]
    if kind == "lora":
        v = norm(value)
        return bool(v) and any(v in norm(r[0]) for r in c.execute("SELECT name FROM uses WHERE file_id=? AND kind='lora'", (file_id,)))
    if kind == "word":
        q = _fts_phrase(value)
        return bool(q) and c.execute("SELECT 1 FROM search WHERE search MATCH ? AND rowid=?", (q, file_id)).fetchone() is not None
    if kind == "tag":
        v = norm(value)
        return (c.execute("SELECT 1 FROM prompt_tags WHERE file_id=? AND tag=?", (file_id, v)).fetchone() is not None
                or c.execute("SELECT 1 FROM file_tags ft JOIN tags t ON t.id=ft.tag_id WHERE ft.file_id=? AND ft.removed=0 AND t.name=? COLLATE NOCASE",
                             (file_id, value)).fetchone() is not None)
    return False


def _rules(c, character_id: int | None = None) -> list:
    sql = "SELECT id, character_id, kind, value, strength, muted FROM character_rules"
    return c.execute(sql + (" WHERE character_id=?" if character_id else ""), (character_id,) if character_id else ()).fetchall()


def _set(c, file_id: int, cid: int, state: str, source: str) -> None:
    """Record a match, never overriding what you decided (confirmed or rejected)."""
    cur = c.execute("SELECT state FROM file_characters WHERE file_id=? AND character_id=?", (file_id, cid)).fetchone()
    if cur and cur[0] in ("confirmed", "rejected"):
        return
    if cur and cur[0] == "auto" and state == "review":
        return
    c.execute("INSERT INTO file_characters (file_id, character_id, state, source, at) VALUES (?,?,?,?,?)"
              " ON CONFLICT (file_id, character_id) DO UPDATE SET state=excluded.state, source=excluded.source, at=excluded.at",
              (file_id, cid, state, source, time.time()))


def rematch(c, character_id: int) -> None:
    """Match one character against every file again (after its rules changed)."""
    strong, weak, src = set(), set(), {}
    for r in _rules(c, character_id):
        files = rule_files(c, r)
        if r["strength"] == "strong":
            strong |= files
            for f in files:
                src.setdefault(f, r["kind"])
        elif not r["muted"]:
            weak |= files
            for f in files:
                src.setdefault(f, r["kind"])
    # matches no rule makes any more go (yours stay)
    keep = strong | weak
    for (fid,) in c.execute("SELECT file_id FROM file_characters WHERE character_id=? AND state IN ('auto', 'review')", (character_id,)).fetchall():
        if fid not in keep:
            c.execute("DELETE FROM file_characters WHERE file_id=? AND character_id=?", (fid, character_id))
        elif fid not in strong:  # was strong, now only weak
            c.execute("UPDATE file_characters SET state='review' WHERE file_id=? AND character_id=? AND state='auto'", (fid, character_id))
    for f in strong:
        _set(c, f, character_id, "auto", src.get(f, "lora"))
    for f in weak - strong:
        _set(c, f, character_id, "review", src.get(f, "tag"))
    _apply_workflow_names(c, character_id)


def match_file(c, file_id: int) -> None:
    """Match one (newly read) file against every character."""
    rules = _rules(c)
    if rules:
        by_char: dict[int, list] = {}
        for r in rules:
            by_char.setdefault(r["character_id"], []).append(r)
        for cid, rs in by_char.items():
            hit_strong = next((r for r in rs if r["strength"] == "strong" and _file_matches(c, r, file_id)), None)
            if hit_strong:
                _set(c, file_id, cid, "auto", hit_strong["kind"])
                continue
            hit_weak = next((r for r in rs if r["strength"] != "strong" and not r["muted"] and _file_matches(c, r, file_id)), None)
            if hit_weak:
                _set(c, file_id, cid, "review", hit_weak["kind"])
            else:
                c.execute("DELETE FROM file_characters WHERE file_id=? AND character_id=? AND state IN ('auto', 'review')", (file_id, cid))
    _workflow_names_for_file(c, file_id)


def _names(c) -> dict[str, int]:
    """Every name and alias (lower case) -> its character."""
    out = {}
    for r in c.execute("SELECT id, name, aliases FROM characters"):
        out[norm(r["name"])] = r["id"]
        for a in json.loads(r["aliases"] or "[]"):
            out.setdefault(norm(a), r["id"])
    return out


def _workflow_names_for_file(c, file_id: int) -> None:
    g = c.execute("SELECT info FROM generations WHERE file_id=?", (file_id,)).fetchone()
    if not g or not g[0]:
        return
    names = _names(c)
    for n in json.loads(g[0]).get("characters") or []:
        cid = names.get(norm(n))
        if cid:
            _set(c, file_id, cid, "confirmed", "workflow")


def _apply_workflow_names(c, character_id: int) -> None:
    """Files whose Gallery Info node named this character (by name or alias): confirmed."""
    row = c.execute("SELECT name, aliases FROM characters WHERE id=?", (character_id,)).fetchone()
    if not row:
        return
    wanted = {norm(row["name"])} | {norm(a) for a in json.loads(row["aliases"] or "[]")}
    for fid, value in c.execute("SELECT g.file_id, j.value FROM generations g, json_each(g.info, '$.characters') j WHERE g.info IS NOT NULL").fetchall():
        if norm(value) in wanted:
            _set(c, fid, character_id, "confirmed", "workflow")


def decide(c, character_id: int, file_ids: list[int], verdict: str) -> None:
    state = {"confirm": "confirmed", "reject": "rejected"}[verdict]
    now = time.time()
    for f in file_ids:
        c.execute("INSERT INTO file_characters (file_id, character_id, state, source, at) VALUES (?,?,?, 'user', ?)"
                  " ON CONFLICT (file_id, character_id) DO UPDATE SET state=excluded.state, source='user', at=excluded.at",
                  (int(f), character_id, state, now))
    _learn(c, character_id)


def undecide(c, character_id: int, file_id: int) -> None:
    """Forget what you decided for one file (it's matched by the rules again)."""
    c.execute("DELETE FROM file_characters WHERE file_id=? AND character_id=? AND state IN ('confirmed', 'rejected')", (file_id, character_id))
    match_file(c, file_id)


def _learn(c, character_id: int) -> None:
    """A weak rule whose matches you've mostly rejected stops suggesting files."""
    for r in _rules(c, character_id):
        if r["strength"] == "strong" or r["muted"]:
            continue
        files = rule_files(c, r)
        if not files:
            continue
        marks = c.execute(f"SELECT state, COUNT(*) FROM file_characters WHERE character_id=? AND file_id IN ({','.join('?' * len(files))})"
                          " GROUP BY state", (character_id, *files)).fetchall()
        n = dict(marks)
        no, yes = n.get("rejected", 0), n.get("confirmed", 0)
        if no >= MUTE_AFTER and no >= 3 * yes:
            c.execute("UPDATE character_rules SET muted=1 WHERE id=?", (r["id"],))
            c.execute(f"DELETE FROM file_characters WHERE character_id=? AND state='review' AND file_id IN ({','.join('?' * len(files))})",
                      (character_id, *files))


def rule_stats(c, character_id: int) -> list[dict]:
    out = []
    for r in _rules(c, character_id):
        files = rule_files(c, r)
        counts = {}
        if files:
            counts = dict(c.execute(f"SELECT state, COUNT(*) FROM file_characters WHERE character_id=? AND file_id IN ({','.join('?' * len(files))})"
                                    " GROUP BY state", (character_id, *files)).fetchall())
        out.append({"id": r["id"], "kind": r["kind"], "value": r["value"], "strength": r["strength"], "muted": bool(r["muted"]),
                    "matches": len(files), "confirmed": counts.get("confirmed", 0), "rejected": counts.get("rejected", 0)})
    return out


def suggestions(c) -> list[dict]:
    """Names the workflow's Gallery Info node used that aren't characters yet."""
    known = _names(c)
    rows = c.execute("SELECT j.value AS name, COUNT(*) AS n FROM generations g, json_each(g.info, '$.characters') j"
                     " WHERE g.info IS NOT NULL GROUP BY lower(j.value) ORDER BY n DESC LIMIT 20").fetchall()
    return [dict(r) for r in rows if norm(r["name"]) not in known]
