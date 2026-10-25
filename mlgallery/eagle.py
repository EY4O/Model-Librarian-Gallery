"""The Eagle bridge: bring an Eagle library's organisation into the Gallery, and send files to Eagle.

Import only reads. An Eagle library (`Name.library`) keeps each item in `images/<id>.info/`: the original
file, a thumbnail and a metadata.json (tags, the folders it's in, a star rating, a note, its source URL);
the library's own metadata.json holds the folder tree. The Gallery matches each item to a file it already
has by fingerprint (the same bytes, wherever they are) and gives that file the item's tags, its folders as
albums, its rating (unless you rated it here), its note (with its source URL on a line of its own) and Eagle's
colours. Items it doesn't have are copied out, laid out by their Eagle folder (an item in several goes into the
first; items in none into Unfiled), under the name Eagle shows: into a folder of the open library, or into a new
library of their own. Every copy is checked against the original (size and fingerprint); copied, never moved, so
Eagle's library stays exactly as it was. Nothing is ever written inside the Eagle library.

Export goes through Eagle's own local API (it must be running): the files are added with their tags, and the
prompt and settings as the note, into the folder you pick. The Gallery never writes into Eagle's folders.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time

from . import bulk, colors, fileops, metadata, scan

API = "http://localhost:41595"
MAX_ITEMS = 100_000


class EagleError(ValueError):
    pass


# ---------------------------------------------------------------- reading a library
def library_root(path: str) -> str:
    """The `.library` folder itself, given it or a folder holding exactly one."""
    p = os.path.abspath(str(path or "").strip().strip('"'))
    if p.lower().endswith(".library") and os.path.isfile(os.path.join(p, "metadata.json")):
        return p
    if os.path.isdir(p):
        libs = [os.path.join(p, n) for n in os.listdir(p) if n.lower().endswith(".library") and os.path.isfile(os.path.join(p, n, "metadata.json"))]
        if len(libs) == 1:
            return libs[0]
        if len(libs) > 1:
            raise EagleError("That folder holds more than one Eagle library: pick the .library folder itself.")
    raise EagleError("That isn't an Eagle library (a folder ending in .library, with a metadata.json).")


def _read(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def folder_names(lib: str) -> dict[str, str]:
    """Eagle folder id -> its name, with its parents ("Characters / Aurelia")."""
    out: dict[str, str] = {}

    def walk(nodes, prefix=""):
        for n in nodes or []:
            if not isinstance(n, dict) or not n.get("id"):
                continue
            name = f"{prefix}{str(n.get('name') or '').strip() or 'Folder'}"
            out[n["id"]] = name
            walk(n.get("children"), name + " / ")
    walk(_read(os.path.join(lib, "metadata.json"), {}).get("folders"))
    return out


def folder_paths(lib: str) -> dict[str, list[str]]:
    """Eagle folder id -> the names from the top down (["Characters", "Aurelia"]): where its items are copied to."""
    out: dict[str, list[str]] = {}

    def walk(nodes, parents=()):
        for n in nodes or []:
            if not isinstance(n, dict) or not n.get("id"):
                continue
            here = (*parents, str(n.get("name") or "").strip() or "Folder")
            out[n["id"]] = list(here)
            walk(n.get("children"), here)
    walk(_read(os.path.join(lib, "metadata.json"), {}).get("folders"))
    return out


_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def safe_name(name: str, fallback: str = "_") -> str:
    """A name Windows takes as a file or folder name: no \\ / : * ? " < > | or control characters, no trailing
    dot or space, not a device name (CON, NUL, ...), not a hidden dot-name."""
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(name or "")).strip().lstrip(".")[:180].rstrip(". ")
    if not s:
        return fallback
    if s.split(".")[0].lower() in _RESERVED:
        s = "_" + s
    return s


def folder_part(name: str) -> str:
    """safe_name for a folder, and never one the Gallery's own scanner leaves out ($..., *.library, @eaDir, ...):
    copies in it would never be indexed."""
    p = safe_name(name, "Folder")
    while scan.skip_dir(p, True):
        p = p[:-len(".library")] + "_library" if p.lower().endswith(".library") else "_" + p
    return p


def note_with_url(note: str, url: str) -> str:
    """Eagle's note, with its source URL on a line of its own at the end (the Gallery has no URL field)."""
    note, url = (note or "").strip(), (url or "").strip()
    if url and url not in note:
        return f"{note}\n{url}" if note else url
    return note


def items(lib: str):
    """Every item in the library that isn't in Eagle's trash: {id, name, ext, file, tags, folders, star, note, url}."""
    base = os.path.join(lib, "images")
    if not os.path.isdir(base):
        return
    for d in os.listdir(base):
        if not d.endswith(".info"):
            continue
        m = _read(os.path.join(base, d, "metadata.json"), None)
        if not isinstance(m, dict) or m.get("isDeleted"):
            continue
        name, ext = str(m.get("name") or ""), str(m.get("ext") or "").lower()
        f = os.path.join(base, d, f"{name}.{ext}")
        if not os.path.isfile(f):  # Eagle sometimes keeps a different file name: the one that isn't the thumbnail
            cands = [n for n in os.listdir(os.path.join(base, d)) if n.lower().endswith("." + ext) and "_thumbnail" not in n] if ext else []
            if not cands:
                continue
            f = os.path.join(base, d, cands[0])
        if not name.strip():
            name = os.path.splitext(os.path.basename(f))[0]
        star = m.get("star")
        yield {"id": m.get("id") or d[:-5], "name": f"{name}.{ext}" if ext else name, "ext": ext, "file": f,
               "tags": [str(t) for t in m.get("tags") or [] if str(t).strip()], "folders": [str(x) for x in m.get("folders") or []],
               "star": int(star) if isinstance(star, (int, float)) and 1 <= star <= 5 else None,
               "note": str(m.get("annotation") or "").strip(), "url": str(m.get("url") or "").strip(),
               "palette": colors.from_eagle(m.get("palettes"))}


# ---------------------------------------------------------------- import
def plan_import(db, path: str, progress=None) -> dict:
    """What an import would do: how many items, which the Gallery already has, the tags, albums and ratings.
    Only items whose size matches a file the Gallery has are fingerprinted (a quick read); the rest can't be one."""
    lib = library_root(path)
    names = folder_names(lib)
    paths = folder_paths(lib)
    with db.read() as c:
        have = {(r["fp"], r["size"]): r["id"] for r in c.execute("SELECT id, fp, size FROM files WHERE missing IN (0, 3) AND fp IS NOT NULL")}
    sizes = {s for _, s in have}
    matched, missing = [], []
    tags, albums = set(), set()
    rated = 0
    for n, it in enumerate(items(lib)):
        if len(matched) + len(missing) >= MAX_ITEMS:
            break
        if progress and n % 200 == 0:
            progress(n)
        try:
            size = os.path.getsize(it["file"])
            fid = have.get((scan.fingerprint(it["file"], size), size)) if size in sizes else None
        except OSError:
            continue
        it["albums"] = [names[x] for x in it["folders"] if x in names]
        it["path"] = next((paths[x] for x in it["folders"] if x in paths), None)  # where its copy goes: its first Eagle folder
        tags.update(it["tags"])
        albums.update(it["albums"])
        rated += bool(it["star"])
        it["size"] = size
        if fid:
            it["file_id"] = fid
            matched.append(it)
        elif scan.kind_of(it["name"]):
            missing.append(it)
    return {"library": lib, "name": os.path.basename(lib)[: -len(".library")], "matched": matched, "missing": missing,
            "tags": sorted(tags, key=str.lower), "albums": sorted(albums, key=str.lower), "rated": rated}


def summary(plan: dict) -> dict:
    return {"library": plan["library"], "name": plan["name"], "matched": len(plan["matched"]), "missing": len(plan["missing"]),
            "missing_size": sum(i["size"] for i in plan["missing"]), "tags": len(plan["tags"]), "albums": plan["albums"][:40],
            "album_count": len(plan["albums"]), "rated": plan["rated"],
            "notes": sum(1 for i in plan["matched"] + plan["missing"] if i["note"]),
            "urls": sum(1 for i in plan["matched"] + plan["missing"] if i["url"]),
            "unfiled": sum(1 for i in plan["matched"] + plan["missing"] if not i.get("path")),
            "all": len(plan["matched"]) + len(plan["missing"]),
            "all_size": sum(i.get("size") or 0 for i in plan["matched"] + plan["missing"])}


def for_new_library(plan: dict) -> dict:
    """The same import for a library that has none of the files yet: every item is copied."""
    keep = ("file_id", "copied_rel")
    return {**plan, "matched": [], "missing": [{k: v for k, v in it.items() if k not in keep}
                                              for it in plan["matched"] + plan["missing"] if scan.kind_of(it["name"])]}


def copy_in(db, plan: dict, folder_id: int, sub: str | None = None, progress=None) -> int:
    """Copy the items the Gallery doesn't have into one of your library folders (Eagle's own copies stay), laid out
    by Eagle folder, under the names Eagle shows; each copy checked against the original; then index them.
    `sub`: the folder inside it (None: "From Eagle - <name>"; "": the library folder itself)."""
    with db.read() as c:
        d = c.execute("SELECT id, path, online FROM folders WHERE id=?", (int(folder_id),)).fetchone()
    if not d or not d["online"] or not os.path.isdir(d["path"]):
        raise EagleError("That library folder isn't reachable.")
    base = f"From Eagle - {plan['name']}" if sub is None else str(sub)
    sub_parts = [folder_part(p) for p in base.replace("\\", "/").split("/") if p.strip()]
    taken: set[str] = set()
    rels, failed = [], []
    for n, it in enumerate(plan["missing"]):
        if progress:
            progress(n)
        target = None
        try:  # anything going wrong with one item (a path too long, a name taken by a file) leaves that item out, not the rest
            parts = [*sub_parts, *(folder_part(p) for p in (it.get("path") or ["Unfiled"]))]
            rel_dir = fileops._real_sub(d["path"], "/".join(parts))  # Art and art are one folder on disk: the index must say which
            dest = os.path.join(d["path"], *rel_dir.split("/"))
            os.makedirs(dest, exist_ok=True)
            stem, ext = os.path.splitext(it["name"])
            with db.read() as c:  # a name an old record still holds (a missing file) is taken too: it would inherit its marks
                name = fileops._free_name(dest, safe_name(stem, "Untitled") + ext.lower(), taken, fileops._held(c, d["id"], rel_dir))
            target = os.path.join(dest, name)
            shutil.copy2(it["file"], target)
            size = os.path.getsize(it["file"])
            if os.path.getsize(target) != size or scan.fingerprint(target, size) != scan.fingerprint(it["file"], size):
                raise OSError("the copy doesn't match")
        except (OSError, ValueError):
            if target:
                try:
                    os.remove(target)
                except OSError:
                    pass
            failed.append(it["name"])
            continue
        it["copied_rel"] = f"{rel_dir}/{name}" if rel_dir else name
        rels.append(it["copied_rel"])
    plan["failed"] = failed
    scan.index_files(db, {"id": d["id"], "path": d["path"]}, rels)
    ids = {}
    with db.read() as c:
        for i in range(0, len(rels), 30000):  # an IN list stops at 32,766 parameters
            part = rels[i:i + 30000]
            ids.update({r["rel"]: r["id"] for r in c.execute(f"SELECT id, rel FROM files WHERE folder_id=? AND rel IN ({','.join('?' * len(part))})",
                                                              (d["id"], *part))})
    n = 0
    for it in plan["missing"]:
        rel = it.get("copied_rel")
        fid = ids.get(rel)
        if fid:
            it["file_id"] = fid
            plan["matched"].append(it)
            n += 1
        elif rel:  # copied but not indexed: never left behind unseen
            try:
                os.remove(os.path.join(d["path"], *rel.split("/")))
            except OSError:
                pass
            it.pop("copied_rel")
            failed.append(it["name"])
    plan["missing"] = [it for it in plan["missing"] if not it.get("file_id")]
    return n


def apply_import(db, plan: dict) -> dict:
    """Give the matched files Eagle's tags, folders (as albums), ratings and notes. One History step, undoable."""
    now = time.time()
    tagged, filed, rated, noted = [], [], [], []
    with db.write() as c:
        for it in plan["matched"]:
            fid = it["file_id"]
            for t in metadata._split(it["tags"]):
                tid = metadata.tag_id(c, t)
                cur = c.execute("SELECT source, removed FROM file_tags WHERE file_id=? AND tag_id=?", (fid, tid)).fetchone()
                if cur and not cur["removed"]:
                    continue
                c.execute("INSERT INTO file_tags (file_id, tag_id, source) VALUES (?, ?, 'user') ON CONFLICT (file_id, tag_id) DO UPDATE SET removed=0", (fid, tid))
                tagged.append({"file_id": fid, "tag": t, "was": dict(cur) if cur else None})
            for a in it.get("albums") or []:
                aid = bulk.album_id(c, a[:100])
                if not aid:
                    continue
                cur = c.execute("SELECT source, removed FROM album_files WHERE album_id=? AND file_id=?", (aid, fid)).fetchone()
                if cur and not cur["removed"]:
                    continue
                c.execute("INSERT INTO album_files (album_id, file_id, source, added) VALUES (?, ?, 'user', ?)"
                          " ON CONFLICT (album_id, file_id) DO UPDATE SET removed=0", (aid, fid, now))
                filed.append({"album": aid, "file_id": fid, "was": dict(cur) if cur else None})
            if it["star"]:
                m = c.execute("SELECT rating, favorite, source FROM marks WHERE file_id=?", (fid,)).fetchone()
                if not m or not m["rating"] or m["source"] == "workflow":  # yours win
                    rated.append(dict(m) if m else {"file_id": fid, "rating": None, "favorite": 0, "source": None})
                    rated[-1]["file_id"] = fid
                    c.execute("INSERT OR REPLACE INTO marks (file_id, rating, favorite, source, updated) VALUES (?,?,?, 'user', ?)",
                              (fid, it["star"], m["favorite"] if m else 0, now))
            if it.get("palette") and not c.execute("SELECT colors FROM files WHERE id=?", (fid,)).fetchone()[0]:
                colors.store(c, fid, it["palette"])  # Eagle's colours, until (instead of) the Gallery working them out
            note = note_with_url(it["note"], it.get("url"))
            if note:
                cur = c.execute("SELECT note, source FROM file_notes WHERE file_id=?", (fid,)).fetchone()
                if cur and cur["source"] == "user":
                    continue  # yours win
                noted.append({"file_id": fid, "was": dict(cur) if cur else None})
                c.execute("INSERT INTO file_notes (file_id, note, source) VALUES (?, ?, 'eagle') ON CONFLICT (file_id) DO UPDATE SET note=excluded.note",
                          (fid, note[:4000]))
        for fid in {i["file_id"] for i in plan["matched"]}:
            metadata.refresh_search(c, fid)
        n = len({i["file_id"] for i in plan["matched"]})
        copied = sorted({i["file_id"] for i in plan["matched"] if i.get("copied_rel")})
        op = fileops.record(c, "eagle", f"Brought in {n} file{'s' if n != 1 else ''}' tags, albums and ratings from Eagle ({plan['name']})"
                            + (f", {len(copied)} of them copied in" if copied else ""),
                            {"tags": tagged, "albums": filed, "marks": rated, "notes": noted, "copied": copied})
    return {"op": op, "files": n, "tags": len(tagged), "albums": len(filed), "ratings": len(rated), "notes": len(noted)}


def undo_import(db, data: dict) -> dict:
    """Each list backwards, so a file changed twice (two Eagle items of one picture) ends as it was."""
    from . import characters as chars
    with db.write() as c:
        for it in reversed(data["tags"]):
            tid = metadata.tag_id(c, it["tag"])
            if it["was"] is None:
                c.execute("DELETE FROM file_tags WHERE file_id=? AND tag_id=?", (it["file_id"], tid))
            else:
                c.execute("INSERT OR REPLACE INTO file_tags (file_id, tag_id, source, removed) VALUES (?,?,?,?)", (it["file_id"], tid, it["was"]["source"], it["was"]["removed"]))
        for it in reversed(data["albums"]):
            if it["was"] is None:
                c.execute("DELETE FROM album_files WHERE album_id=? AND file_id=?", (it["album"], it["file_id"]))
            else:
                c.execute("UPDATE album_files SET removed=? WHERE album_id=? AND file_id=?", (it["was"]["removed"], it["album"], it["file_id"]))
        for m in reversed(data["marks"]):
            if m.get("source") is None:
                c.execute("DELETE FROM marks WHERE file_id=?", (m["file_id"],))
            else:
                c.execute("INSERT OR REPLACE INTO marks (file_id, rating, favorite, source, updated) VALUES (?,?,?,?,?)",
                          (m["file_id"], m.get("rating"), m.get("favorite") or 0, m["source"], time.time()))
        for it in reversed(data.get("notes") or []):
            if it["was"] is None:
                c.execute("DELETE FROM file_notes WHERE file_id=?", (it["file_id"],))
            else:
                c.execute("INSERT OR REPLACE INTO file_notes (file_id, note, source) VALUES (?,?,?)", (it["file_id"], it["was"]["note"], it["was"]["source"]))
        files = {i["file_id"] for i in data["tags"]} | {i["file_id"] for i in data["albums"]}
        for fid in files:
            metadata.refresh_search(c, fid)
            chars.match_file(c, fid)
        c.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM file_tags)")
        c.execute("DELETE FROM albums WHERE id NOT IN (SELECT album_id FROM album_files) AND id IN (%s)" % ",".join(str(int(a["album"])) for a in data["albums"])
                  if data["albums"] else "SELECT 1")
    out = {"undone": len(data["tags"]) + len(data["albums"]) + len(data["marks"]) + len(data.get("notes") or [])}
    if data.get("copied"):  # the copies it made go to the Gallery's Trash (Eagle's own files were never touched)
        r = fileops.trash(db, data["copied"])
        if r["op"]:
            with db.write() as c:
                c.execute("DELETE FROM ops WHERE id=?", (r["op"],))  # the undo itself isn't a new step
        out["undone"] += r["trashed"]
        out["trashed"] = r["trashed"]
    return out


# ---------------------------------------------------------------- send to Eagle
async def _call(base: str, token: str, path: str, body=None):
    import aiohttp
    url = base.rstrip("/") + path + (("&" if "?" in path else "?") + "token=" + token if token else "")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            req = s.post(url, json=body) if body is not None else s.get(url)
            async with req as r:
                data = await r.json(content_type=None)
    except aiohttp.ClientConnectorError:
        raise EagleError("Eagle isn't running (or its address in Settings is wrong): start Eagle, then try again.")
    except (aiohttp.ClientError, TimeoutError, ValueError) as ex:
        raise EagleError(f"Couldn't talk to Eagle: {ex}")
    if not isinstance(data, dict) or data.get("status") != "success":
        msg = (data or {}).get("message") if isinstance(data, dict) else None
        raise EagleError(f"Eagle said no{': ' + str(msg) if msg else ''}. If it asks for an API token, set it in Settings.")
    return data.get("data")


async def eagle_folders(base: str, token: str) -> list[dict]:
    """Eagle's folders, flattened with their full names, for the Send to Eagle picker."""
    out = []

    def walk(nodes, prefix=""):
        for n in nodes or []:
            if isinstance(n, dict) and n.get("id"):
                name = f"{prefix}{n.get('name') or ''}"
                out.append({"id": n["id"], "name": name})
                walk(n.get("children"), name + " / ")
    walk(await _call(base, token, "/api/folder/list"))
    return out


def note_for(info: dict) -> str:
    """The prompt and settings, as the note Eagle keeps with the item."""
    g = info.get("generation") or {}
    parts = []
    if g.get("positive"):
        parts.append(g["positive"])
    if g.get("negative"):
        parts.append("Negative: " + g["negative"])
    sets = [f"{k} {g[v]}" for k, v in (("Seed", "seed"), ("Steps", "steps"), ("CFG", "cfg"), ("Sampler", "sampler"), ("Scheduler", "scheduler")) if g.get(v) is not None]
    models = [u["name"] + (f" {u['strength']}" if u.get("strength") is not None else "") for u in info.get("uses") or []]
    if sets:
        parts.append(", ".join(sets))
    if models:
        parts.append("Models: " + ", ".join(models))
    return "\n".join(parts)[:4000]


async def send(base: str, token: str, files: list[dict], folder_id: str | None) -> dict:
    """files: [{path, name, tags, note}] -> Eagle adds them (in batches); Eagle copies them into its library."""
    sent = 0
    for i in range(0, len(files), 100):
        batch = files[i:i + 100]
        body = {"items": [{"path": f["path"], "name": os.path.splitext(f["name"])[0], "tags": f["tags"], "annotation": f["note"]} for f in batch]}
        if folder_id:
            body["folderId"] = folder_id
        await _call(base, token, "/api/item/addFromPaths", body)
        sent += len(batch)
    return {"sent": sent}
