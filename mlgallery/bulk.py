"""Changes to many files at once - rating, favourite, tags, albums, characters, the artist - each recorded so it can be undone."""
from __future__ import annotations

import time

from . import characters as chars
from . import credits, fileops, metadata, search

MAX = fileops.MAX_ITEMS


def album_id(c, name: str, create: bool = True) -> int | None:
    """The album of that name (made if there's none); None for a smart album: its search decides what's in it."""
    name = search.album_name(name)
    if not name:
        return None
    r = c.execute("SELECT id, query FROM albums WHERE name=? COLLATE NOCASE", (name,)).fetchone()
    if not r:  # (Été for été: folded as the sidebar folds them, past A-Z)
        r = next((x for x in c.execute("SELECT id, query, name FROM albums") if search.album_name(x[2]).casefold() == name.casefold()), None)
    if r:
        return r[0] if r[1] is None else None
    return c.execute("INSERT INTO albums (name, created) VALUES (?,?)", (name, time.time())).lastrowid if create else None


def apply(db, ids, action: str, value=None) -> dict:
    if isinstance(value, (list, dict)):
        raise fileops.OpError("The value is a number or a name.")
    ids = [int(i) for i in list(ids)[:MAX]]
    if not ids:
        raise fileops.OpError("Nothing selected.")
    n = len(ids)
    files = f"{n} file{'s' if n != 1 else ''}"
    with db.write() as c:
        ids = [r[0] for r in c.execute(f"SELECT id FROM files WHERE id IN ({','.join('?' * len(ids))})", ids)]
        if action in ("rate", "favorite"):
            prev = {r["file_id"]: dict(r) for r in c.execute(
                f"SELECT file_id, rating, favorite, source FROM marks WHERE file_id IN ({','.join('?' * len(ids))})", ids)}
            data = [prev.get(i, {"file_id": i, "rating": None, "favorite": 0, "source": None}) for i in ids]
            for i in ids:
                cur = prev.get(i, {"rating": None, "favorite": 0})
                rating = cur["rating"]
                fav = cur["favorite"]
                if action == "rate":
                    v = int(value or 0)
                    rating = v if 1 <= v <= 5 else None
                else:
                    fav = int(bool(value))
                c.execute("INSERT OR REPLACE INTO marks (file_id, rating, favorite, source, updated) VALUES (?,?,?, 'user', ?)", (i, rating, fav, time.time()))
            what = f"Rated {files} {int(value or 0)}" if action == "rate" and value else f"Cleared the rating of {files}" if action == "rate" \
                else f"{'Marked' if value else 'Unmarked'} {files} as favourite{'s' if n != 1 else ''}"
            op = fileops.record(c, "marks", what, data)
        elif action in ("tag", "untag"):
            names = metadata._split(value if isinstance(value, list) else [value])
            if not names:
                raise fileops.OpError("Which tag?")
            data = []
            for name in names:
                tid = metadata.tag_id(c, name)
                for i in ids:
                    cur = c.execute("SELECT source, removed FROM file_tags WHERE file_id=? AND tag_id=?", (i, tid)).fetchone()
                    if action == "tag":
                        if cur and not cur["removed"]:
                            continue
                        c.execute("INSERT INTO file_tags (file_id, tag_id, source) VALUES (?, ?, 'user') ON CONFLICT (file_id, tag_id) DO UPDATE SET removed=0", (i, tid))
                        data.append({"file_id": i, "tag": name, "was": dict(cur) if cur else None})
                    elif cur and not cur["removed"]:
                        if cur["source"] in ("workflow", "site"):  # (taken off, it stays off: not found again)
                            c.execute("UPDATE file_tags SET removed=1 WHERE file_id=? AND tag_id=?", (i, tid))
                        else:
                            c.execute("DELETE FROM file_tags WHERE file_id=? AND tag_id=?", (i, tid))
                        data.append({"file_id": i, "tag": name, "was": dict(cur)})
            for i in {d["file_id"] for d in data}:
                metadata.refresh_search(c, i)
                chars.match_file(c, i)
            c.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM file_tags)")
            op = fileops.record(c, "tags", f"{'Tagged' if action == 'tag' else 'Untagged'} {files} {', '.join(names)}", {"action": action, "items": data})
        elif action in ("album", "unalbum"):
            if c.execute("SELECT 1 FROM albums WHERE name=? COLLATE NOCASE AND query IS NOT NULL", (search.album_name(value),)).fetchone():
                raise fileops.OpError("That's a smart album: it holds what its search finds. Change its search instead.")
            aid = album_id(c, value, create=action == "album")
            if not aid:
                raise fileops.OpError("Which album?")
            data = []
            for i in ids:
                cur = c.execute("SELECT source, removed FROM album_files WHERE album_id=? AND file_id=?", (aid, i)).fetchone()
                if action == "album":
                    if cur and not cur["removed"]:
                        continue
                    c.execute("INSERT INTO album_files (album_id, file_id, source, added) VALUES (?, ?, 'user', ?)"
                              " ON CONFLICT (album_id, file_id) DO UPDATE SET removed=0", (aid, i, time.time()))
                    data.append({"file_id": i, "was": dict(cur) if cur else None})
                elif cur and not cur["removed"]:
                    if cur["source"] == "workflow":
                        c.execute("UPDATE album_files SET removed=1 WHERE album_id=? AND file_id=?", (aid, i))
                    else:
                        c.execute("DELETE FROM album_files WHERE album_id=? AND file_id=?", (aid, i))
                    data.append({"file_id": i, "was": dict(cur)})
            name = c.execute("SELECT name FROM albums WHERE id=?", (aid,)).fetchone()[0]
            op = fileops.record(c, "albums", f"{'Added' if action == 'album' else 'Took'} {files} {'to' if action == 'album' else 'out of'} {name}",
                                {"action": action, "album": aid, "items": data})
        elif action == "character":  # these are that character (or person): confirmed, as a yes in To review is
            cid = chars.find(c, value)
            if not cid:
                raise fileops.OpError("Which character?")
            data = []
            now = time.time()
            for i in ids:
                cur = c.execute("SELECT state, source, at FROM file_characters WHERE file_id=? AND character_id=?", (i, cid)).fetchone()
                if cur and cur["state"] == "confirmed":
                    continue
                c.execute("INSERT INTO file_characters (file_id, character_id, state, source, at) VALUES (?,?, 'confirmed', 'user', ?)"
                          " ON CONFLICT (file_id, character_id) DO UPDATE SET state='confirmed', source='user', at=excluded.at", (i, cid, now))
                data.append({"file_id": i, "was": dict(cur) if cur else None})
            name = c.execute("SELECT name FROM characters WHERE id=?", (cid,)).fetchone()[0]
            op = fileops.record(c, "characters", f"Added {files} to {name}", {"character": cid, "items": data})
        elif action == "artist":  # a name; '' for no artist; None for what was found in them
            if value is not None and not isinstance(value, str):
                raise fileops.OpError("The artist is a name.")
            prev = {r["file_id"]: dict(r) for r in c.execute(
                f"SELECT file_id, artist, site, post, url FROM credits WHERE src='user' AND file_id IN ({','.join('?' * len(ids))})", ids)}
            for i in ids:
                credits.set_user(c, i, artist=value)
            credits.resolve(c, ids)
            credits.prune(c)
            name = credits.clean_name(value, junk=False) if value else None
            what = f"Set the artist of {files} to {name}" if name else f"Said {files} {'have' if n != 1 else 'has'} no artist" if value == "" \
                else f"Gave {files} the artist found in {'them' if n != 1 else 'it'}"
            op = fileops.record(c, "credits", what, [{"file_id": i, "was": prev.get(i)} for i in ids])
        else:
            raise fileops.OpError(f"Unknown action {action}")
    if action in ("artist", "character"):
        db.user_change()
    return {"op": op, "count": len(ids)}


def alive(c, items: list, key: str = "file_id") -> list:
    """The items whose file is still in the index (one emptied from the Trash since is gone: nothing to put back)."""
    ids = sorted({int(it[key]) for it in items})
    have = set()
    for part in chars._chunks(ids):
        have |= {r[0] for r in c.execute(f"SELECT id FROM files WHERE id IN ({','.join('?' * len(part))})", part)}
    return [it for it in items if int(it[key]) in have]


def review_restore(c, cid: int, items: list) -> None:
    """Answers in To review taken back, last first: each file as it was, and a rule they muted suggesting again."""
    if not c.execute("SELECT 1 FROM characters WHERE id=?", (cid,)).fetchone():
        return
    gave = {"confirm": "confirmed", "reject": "rejected"}
    unmute = set()
    for it in reversed(items):
        cur = c.execute("SELECT state, source FROM file_characters WHERE file_id=? AND character_id=?", (it["file_id"], cid)).fetchone()
        if not cur or cur["source"] != "user" or cur["state"] != gave.get(it["verdict"]):
            continue  # (decided otherwise since, in the details panel say: that stays)
        c.execute("DELETE FROM file_characters WHERE file_id=? AND character_id=?", (it["file_id"], cid))
        was = it["was"]
        if was and was["source"] == "user" and was["state"] in ("confirmed", "rejected"):  # a decision of yours from before
            c.execute("INSERT INTO file_characters (file_id, character_id, state, source, at) VALUES (?,?,?,?,?)",
                      (it["file_id"], cid, was["state"], was["source"], was["at"]))
        unmute |= set(it.get("muted") or [])
    if unmute:  # a rule the answers muted suggests again
        c.execute(f"UPDATE character_rules SET muted=0 WHERE character_id=? AND id IN ({','.join('?' * len(unmute))})", (cid, *sorted(unmute)))
    chars.rematch(c, cid)  # (the rules say again what the rest are: all of them at once, not file by file)


def undo(db, kind: str, data) -> dict:
    with db.write() as c:
        if kind == "marks":
            data = alive(c, data)
            for m in data:
                if m.get("source") is None and m.get("rating") is None and not m.get("favorite"):
                    c.execute("DELETE FROM marks WHERE file_id=?", (m["file_id"],))
                else:
                    c.execute("INSERT OR REPLACE INTO marks (file_id, rating, favorite, source, updated) VALUES (?,?,?,?,?)",
                              (m["file_id"], m.get("rating"), m.get("favorite") or 0, m.get("source") or "user", time.time()))
            return {"undone": len(data)}
        if kind == "tags":
            data = {**data, "items": alive(c, data["items"])}
            for it in data["items"]:
                tid = metadata.tag_id(c, it["tag"])
                was = it["was"]
                if was is None:
                    c.execute("DELETE FROM file_tags WHERE file_id=? AND tag_id=?", (it["file_id"], tid))
                else:
                    c.execute("INSERT OR REPLACE INTO file_tags (file_id, tag_id, source, removed) VALUES (?,?,?,?)",
                              (it["file_id"], tid, was["source"], was["removed"]))
            for i in {it["file_id"] for it in data["items"]}:
                metadata.refresh_search(c, i)
                chars.match_file(c, i)
            c.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM file_tags)")
            return {"undone": len(data["items"])}
        if kind == "album_move":  # the names they had (one taken since by another album: that one stays as it is)
            back, failed = 0, []
            rows = {r[0]: r[1] for r in c.execute("SELECT id, name FROM albums")}
            mine = {aid for aid, _, after in data["names"] if aid in rows and rows[aid] == after}  # (renamed again since: left alone)
            failed += [aid for aid, _, _ in data["names"] if aid not in mine]
            for aid in mine:  # out of the way first: names change places
                c.execute("UPDATE albums SET name=? WHERE id=?", (f"~moving {aid}~", aid))
            used = {search.album_name(n).casefold() for i, n in rows.items() if i not in mine}  # (Été and été alike)
            for aid, before, after in data["names"]:
                if aid not in mine:
                    continue
                name = before if before.casefold() not in used else after if after.casefold() not in used else \
                    next(f"{after} ({k})" for k in range(2, 1000) if f"{after} ({k})".casefold() not in used)
                c.execute("UPDATE albums SET name=? WHERE id=?", (name, aid))
                used.add(name.casefold())
                if name == before:
                    back += 1
                else:
                    failed.append(aid)  # (its old name is another album's now)
            if not failed:  # (a search names the old names: only right if they're all back)
                for aid, q, q2 in data.get("queries") or []:
                    c.execute("UPDATE albums SET query=? WHERE id=? AND query=?", (q, aid, q2))
            return {"undone": back, "failed": failed}
        if kind == "review":
            review_restore(c, data["character"], data["items"])
            return {"undone": len(data["items"])}
        if kind == "dupes_kept":  # offered again
            c.executemany("DELETE FROM dupes_kept WHERE files=?", [(k,) for k in data["keys"]])
            return {"undone": len(data["keys"])}
        if kind == "dupes_unkept":  # kept again
            c.executemany("INSERT OR IGNORE INTO dupes_kept (files, at) VALUES (?, ?)", [(k, time.time()) for k in data["keys"]])
            return {"undone": len(data["keys"])}
        if kind == "album_delete":  # the albums back, with their files (into one of the same name, if one's been made since)
            for a in data["albums"]:
                have = c.execute("SELECT id FROM albums WHERE name=? COLLATE NOCASE", (a["name"],)).fetchone()
                if have:
                    aid = have[0]
                else:
                    free = not c.execute("SELECT 1 FROM albums WHERE id=?", (a["id"],)).fetchone()
                    cover = a.get("cover") if a.get("cover") and c.execute("SELECT 1 FROM files WHERE id=?", (a["cover"],)).fetchone() else None
                    aid = c.execute("INSERT INTO albums (id, name, cover, created, icon, color, query) VALUES (?,?,?,?,?,?,?)",
                                    (a["id"] if free else None, a["name"], cover, a.get("created"), a.get("icon"), a.get("color"), a.get("query"))).lastrowid
                for f in a["files"]:
                    if c.execute("SELECT 1 FROM files WHERE id=?", (f["file_id"],)).fetchone():
                        c.execute("INSERT OR IGNORE INTO album_files (album_id, file_id, source, removed, added) VALUES (?,?,?,?,?)",
                                  (aid, f["file_id"], f["source"], f["removed"], f["added"]))
            return {"undone": len(data["albums"])}
        if kind == "albums":
            data = {**data, "items": alive(c, data["items"])}
            aid = data["album"]
            if not c.execute("SELECT 1 FROM albums WHERE id=?", (aid,)).fetchone():
                return {"undone": 0}
            for it in data["items"]:
                was = it["was"]
                if was is None:
                    c.execute("DELETE FROM album_files WHERE album_id=? AND file_id=?", (aid, it["file_id"]))
                else:
                    c.execute("INSERT OR REPLACE INTO album_files (album_id, file_id, source, removed, added) VALUES (?,?,?,?,?)",
                              (aid, it["file_id"], was["source"], was["removed"], time.time()))
            return {"undone": len(data["items"])}
        if kind == "characters":
            cid = data["character"]
            if not c.execute("SELECT 1 FROM characters WHERE id=?", (cid,)).fetchone():
                return {"undone": 0}
            for it in data["items"]:
                was = it["was"]
                if was is None:  # not theirs before: what its rules say again
                    c.execute("DELETE FROM file_characters WHERE file_id=? AND character_id=?", (it["file_id"], cid))
                    if c.execute("SELECT 1 FROM files WHERE id=?", (it["file_id"],)).fetchone():
                        chars.match_file(c, it["file_id"])
                else:
                    c.execute("UPDATE file_characters SET state=?, source=?, at=? WHERE file_id=? AND character_id=?",
                              (was["state"], was["source"], was["at"], it["file_id"], cid))
            return {"undone": len(data["items"])}
        if kind == "credits":
            data = alive(c, data)
            for it in data:
                was = it["was"]
                c.execute("DELETE FROM credits WHERE file_id=? AND src='user'", (it["file_id"],))
                if was:
                    c.execute("INSERT INTO credits (file_id, src, artist, site, post, url) VALUES (?, 'user', ?,?,?,?)",
                              (it["file_id"], was["artist"], was["site"], was["post"], was["url"]))
            credits.resolve(c, [it["file_id"] for it in data if c.execute("SELECT 1 FROM files WHERE id=?", (it["file_id"],)).fetchone()])
            credits.prune(c)
            return {"undone": len(data)}
    return {"undone": 0}
