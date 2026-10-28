"""New downloads: the pictures and videos in your Downloads folder (and others you add in Settings), offered for filing.

Nothing there is indexed: it's listed (newest first) with who made it and where it's from, as far as the file, its
name and where Windows says it was downloaded from tell (credits.py), and whether the library has it already.
Filing moves it into a library folder - by default the one its artist's other pictures are in - and indexes it
there, with its artist and source; History can undo that. A download you dismiss isn't offered again (unless it
changes); one that's already in the library can go to the Recycle Bin instead.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time

from . import credits, fileops, scan

LISTED = 2000          # downloads listed at most, newest first
EXT_FOLDER = "Model Librarian Gallery"  # Downloads\<this>: where the Chrome extension saves, each picture with its note
SIDECAR = ".mlg.json"  # <picture's name><this>: the note (the page, the artist, the title, the tags)
SETTLE = 2.0           # seconds a download must have been left alone (a browser still writing it isn't offered)
NOTE_WAIT = 15.0       # ... and one in the extension's folder without its note yet, this long (the note follows the picture)
FOLDERID_DOWNLOADS = "{374DE290-123F-4565-9164-39C4925E467B}"


def downloads_folder() -> str | None:
    """Windows' Downloads folder, wherever you moved it."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            from .winshell import _guid
            path = ctypes.c_wchar_p()
            shell32 = ctypes.windll.shell32
            shell32.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
            guid = _guid(FOLDERID_DOWNLOADS)
            if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(path)) == 0:
                got = path.value
                ctypes.windll.ole32.CoTaskMemFree(path)
                if got and os.path.isdir(got):
                    return got
        except Exception:
            pass
    home = os.path.join(os.path.expanduser("~"), "Downloads")
    return home if os.path.isdir(home) else None


NOTE_MAX = 64 * 1024  # a note is a few hundred bytes: anything much bigger isn't one


def read_note(path: str) -> dict | None:
    """The note the Chrome extension saved beside a picture: {page, src, site, post, artist, artist_url, title,
    description, tags, characters} (only what's there, checked and capped; characters: a booru post's, each
    {tag, name, series, appearance, outfit} as + Character would file it). Only in its own folder (Downloads\\Model Librarian
    Gallery): a note beside a file anywhere else could have been put there by any site."""
    if os.path.basename(os.path.dirname(os.path.abspath(path))).lower() != EXT_FOLDER.lower():
        return None
    try:
        if os.path.getsize(path + SIDECAR) > NOTE_MAX:
            return None
        with open(path + SIDECAR, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:  # missing, unreadable, not JSON, nested too deep: no note
        return None
    if not isinstance(raw, dict):
        return None
    text = lambda k, n=300: str(raw.get(k) or "").strip()[:n] or None  # noqa: E731
    tags = [str(t).strip()[:100] for t in raw.get("tags") or [] if isinstance(t, (str, int)) and str(t).strip()][:64] if isinstance(raw.get("tags"), list) else []
    chars = []
    for ch in raw.get("characters") if isinstance(raw.get("characters"), list) else []:
        if isinstance(ch, dict) and isinstance(ch.get("tag"), str) and ch["tag"].strip():
            words = lambda v: [str(t).strip()[:100] for t in v if isinstance(t, str) and t.strip()][:64] if isinstance(v, list) else []  # noqa: E731
            chars.append({"tag": re.sub(r"\s+", "_", ch["tag"].strip())[:200], "name": str(ch.get("name") or "").strip()[:100],
                          "series": str(ch.get("series") or "").strip()[:100], "appearance": words(ch.get("appearance")), "outfit": words(ch.get("outfit"))})
    site = text("site", 40)
    if site:  # (pixiv, Pixiv: one site, under the Gallery's own key for it)
        site = next((k for k, v in credits.SITES.items() if site.lower() in (k.lower(), str(v).lower())), site.lower())
    return {k: v for k, v in {"page": credits.clean_url(raw.get("page")), "src": credits.clean_url(raw.get("src")), "site": site,
                              "post": text("post", 80), "artist": credits.clean_name(raw.get("artist"), junk=True),
                              "artist_url": credits.clean_url(raw.get("artist_url")), "title": text("title"), "description": text("description", 3000),
                              "tags": tags, "characters": chars[:12]}.items() if v}


def from_note(note: dict) -> dict:
    """What a note says about where a picture is from and who made it, as a credit (where it was downloaded from)."""
    got = credits.from_url(note.get("page")) if note.get("page") else {}
    out = {**got, **{k: note[k] for k in ("site", "post", "artist") if note.get(k)}}
    if note.get("page"):
        out["url"] = note["page"]
    return {k: v for k, v in out.items() if v}


def key_of(path: str) -> str:
    return hashlib.sha1(os.path.normcase(os.path.abspath(path)).encode("utf-8")).hexdigest()[:20]


def safe_folder(name: str) -> str:
    """A name Windows takes for a folder (an artist's, for Artists\\<name>), and one the library looks in (not $hiro
    or .hack: scan.skip_dir leaves folders so named out, and what's filed there would never be indexed)."""
    v = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name or "").strip().rstrip(". ").lstrip(".$ ")
    v = re.sub(r"\s+", " ", v)[:80] or "Unknown"
    return f"{v} (artist)" if scan.skip_dir(v, True) else v


def _inside(path: str, root: str) -> bool:
    a, b = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(root))
    return a == b or a.startswith(b.rstrip("\\/") + os.sep)


class Inbox:
    def __init__(self, lib):
        self.lib = lib
        self._lock = threading.Lock()
        self._listing: tuple | None = None  # (what it was made from, the entries)
        self._found: dict[tuple, dict] = {}  # (path, size, mtime) -> what was found about it (fingerprint, credits)
        self._changes = 0  # dismissing, filing, putting back...: a listing made before one isn't kept

    # ---------------------------------------------------------------- where
    def folders(self) -> list[dict]:
        """The folders whose new pictures are offered: {path, downloads (Windows' own), ok (reachable, not a library folder)}."""
        s = self.lib.settings
        lib_folders = [f["path"] for f in self.lib.folders()]
        out, seen = [], set()
        dl = downloads_folder() if s.get("inbox_downloads") else None
        ext = os.path.join(dl, EXT_FOLDER) if dl and os.path.isdir(os.path.join(dl, EXT_FOLDER)) else None  # (the Chrome extension's)
        for path, is_dl in ([(dl, True)] if dl else []) + ([(ext, True)] if ext else []) + [(p, False) for p in s.get("inbox_folders") or [] if isinstance(p, str)]:
            k = os.path.normcase(os.path.abspath(path))
            if k in seen:
                continue
            seen.add(k)
            in_lib = any(_inside(path, r) for r in lib_folders)  # (in one, or one itself; one inside it is fine: only the top level's offered)
            out.append({"path": path, "downloads": is_dl, "ok": os.path.isdir(path) and not in_lib,
                        "why": "It's a library folder (or holds one): its pictures are in the library already." if in_lib else
                               "" if os.path.isdir(path) else "It isn't there (or not reachable right now)."})
        return out

    def add_folder(self, path: str) -> list[dict]:
        path = os.path.abspath(os.path.expanduser(str(path or "").strip().strip('"')))
        if not os.path.isdir(path):
            raise ValueError("That folder doesn't exist (or isn't reachable right now).")
        if any(_inside(path, f["path"]) for f in self.lib.folders()):
            raise ValueError("That's a library folder (or holds one): its pictures are in the library already.")
        have = [p for p in self.lib.settings.get("inbox_folders") or [] if isinstance(p, str)]
        if not any(os.path.normcase(p) == os.path.normcase(path) for p in have):
            self.lib.settings.update({"inbox_folders": have + [path]})
        self._listing = None
        return self.folders()

    def remove_folder(self, path: str) -> list[dict]:
        have = [p for p in self.lib.settings.get("inbox_folders") or [] if isinstance(p, str)]
        self.lib.settings.update({"inbox_folders": [p for p in have if os.path.normcase(p) != os.path.normcase(str(path or ""))]})
        self._listing = None
        return self.folders()

    # ---------------------------------------------------------------- what's there
    def _dismissed(self) -> dict:
        with self.lib.db.read() as c:
            return {os.path.normcase(r[0]): (r[1], r[2]) for r in c.execute("SELECT path, size, mtime FROM inbox_dismissed")}

    def entries(self) -> list[dict]:
        """The downloads on offer: {key, path, name, size, mtime, kind}, newest first (the folders' top level only)."""
        folders = [f["path"] for f in self.folders() if f["ok"]]
        stamp = []
        for p in folders:
            try:
                stamp.append((p, os.stat(p).st_mtime_ns))  # a folder's time changes when a file comes or goes in it
            except OSError:
                stamp.append((p, None))
        made_from = (tuple(stamp), int(time.time() // 10), self._changes)  # (and every 10 s: one still being written settles; filing, dismissing... start afresh)
        with self._lock:
            if self._listing and self._listing[0] == made_from:
                return self._listing[1]
        gone = self._dismissed()
        now = time.time()
        out = []
        for folder in folders:
            ext_dir = os.path.basename(folder.rstrip("\\/")).lower() == EXT_FOLDER.lower()
            try:
                with os.scandir(folder) as it:
                    for e in it:
                        name = e.name
                        kind = scan.kind_of(name)
                        if not kind or name.startswith((".", "~$")) or not e.is_file(follow_symlinks=False):
                            continue
                        st = e.stat(follow_symlinks=False)
                        if not st.st_size or now - st.st_mtime < SETTLE:
                            continue
                        d = gone.get(os.path.normcase(e.path))
                        if d and d[0] == st.st_size and abs(d[1] - st.st_mtime) < 1:
                            continue
                        if ext_dir and now - st.st_mtime < NOTE_WAIT and not os.path.isfile(e.path + SIDECAR):
                            continue  # the extension's, its note not written yet: in a moment
                        out.append({"key": key_of(e.path), "path": e.path, "name": name, "size": st.st_size, "mtime": st.st_mtime,
                                    "kind": kind, "folder": folder})
            except OSError:
                continue
        out.sort(key=lambda x: -x["mtime"])
        out = out[:LISTED]
        with self._lock:
            self._listing = (made_from, out)
        return out

    def count(self) -> int:
        return len(self.entries())

    def notify_loop(self, stop, every: float = 15) -> None:
        """New pictures in a downloads folder: a Windows notification (when it's on, in a library for illustrations).
        Only ones that arrive while the Gallery is open: what was there when it started was there already."""
        seen = None  # every download offered while it's run: one offered again (or put back by an Undo) isn't news
        while not stop.wait(every):
            try:
                if not self.lib.settings.get("inbox_notify") or "illustrations" not in (self.lib.kinds or []):
                    seen = None
                    continue
                es = self.entries()
                keys = {e["key"] for e in es}
                if seen is not None:
                    new = [e for e in es if e["key"] not in seen]
                    if new:
                        from .winshell import notify
                        notify(f"{len(new)} new download{'s' if len(new) != 1 else ''} to file" if len(new) > 1 else "A new download to file",
                               ", ".join(e["name"] for e in new[:3]) + ("…" if len(new) > 3 else "") + " (New downloads, in the Gallery)")
                seen = keys | (seen or set())
            except Exception:
                continue

    def _about(self, e: dict) -> dict:
        """What's known about one download: its fingerprint, and who made it / where it's from."""
        k = (e["path"], e["size"], e["mtime"])
        got = self._found.get(k)
        try:
            side = os.stat(e["path"] + SIDECAR).st_mtime_ns
        except OSError:
            side = None
        if got is not None and side != got.get("side"):
            got = None  # its note came after it (or changed): read again, with it
        if got is None:
            try:
                fp = scan.fingerprint(e["path"], e["size"])
            except OSError:
                fp = None
            got = {"fp": fp, "found": credits.found(e["path"], e["name"], e["name"], e["kind"]), "side": side}
            note = read_note(e["path"])  # saved by the Chrome extension: the page it's from, its artist, title and tags
            if note:
                got["note"] = note
                if from_note(note):
                    got["found"]["note"] = from_note(note)  # (its own source: what Windows says later doesn't replace it)
            if len(self._found) > 5000:
                self._found.clear()
            self._found[k] = got
        return got

    def items(self, offset: int = 0, limit: int = 60) -> dict:
        """A page of downloads with what's known about each: the artist and source found, the copy the library has
        already (if it has), and where to file it."""
        es = self.entries()
        page = es[max(0, offset):max(0, offset) + max(1, min(LISTED, limit))]
        db = self.lib.db
        out = []
        with db.read() as c:
            names = credits.name_map(c)
            last = self._last(c)
            yours = None  # (read once, for the first one with characters)
            for e in page:
                a = self._about(e)
                w = credits.winner([{"src": s, "artist": v.get("artist"), "site": v.get("site"), "post": v.get("post"), "url": v.get("url")}
                                    for s, v in a["found"].items()])
                dup = c.execute("SELECT f.id, f.rel, f.missing, d.path FROM files f JOIN folders d ON d.id=f.folder_id"
                                " WHERE f.fp=? AND f.size=? AND f.missing IN (0, 2, 3) ORDER BY f.missing LIMIT 1", (a["fp"], e["size"])).fetchone() if a["fp"] else None
                aid = names.get(credits.norm(w["artist"])) if w["artist"] else None
                if (a.get("note") or {}).get("characters") and yours is None:
                    yours = _yours(c)
                out.append({**{k: e[k] for k in ("key", "name", "size", "mtime", "kind", "folder")},
                            "artist": w["artist"], "artist_from": w["artist_from"], "artist_id": aid,
                            "site": w["site"], "site_name": credits.SITES.get(w["site"] or "", w["site"]), "url": w["url"],
                            "have": {"id": dup["id"], "path": os.path.join(dup["path"], *dup["rel"].split("/")), "trash": dup["missing"] == 2} if dup else None,
                            "title": (a.get("note") or {}).get("title"), "tags": len((a.get("note") or {}).get("tags") or []),
                            "characters": [_offer(ch, yours) for ch in (a.get("note") or {}).get("characters") or []],
                            "suggest": self._suggest(c, aid, w["artist"], last)})
        return {"total": len(es), "offset": offset, "items": out, "folders": self.folders()}

    def character(self, key: str, tag: str, cid: int | None) -> dict:
        """One of a download's characters (from its note) made a character (cid None), or added to yours (cid)."""
        e = next(iter(self._pick([key])), None)
        if not e:
            raise ValueError("That download isn't there any more.")
        ch = next((x for x in (self._about(e).get("note") or {}).get("characters") or [] if _norm(x["tag"]) == _norm(tag)), None)
        if not ch:
            raise ValueError("That character isn't in the picture's note.")
        out = self.lib.character_from_post(cid, ch)
        self._changes += 1
        return out

    # ---------------------------------------------------------------- where it goes
    def _last(self, c) -> dict | None:
        r = c.execute("SELECT value FROM library_meta WHERE key='inbox_last'").fetchone()
        try:
            last = json.loads(r[0]) if r else None
        except ValueError:
            last = None
        ok = last and c.execute("SELECT 1 FROM folders WHERE id=?", (last.get("folder"),)).fetchone()
        return last if ok else None

    def _suggest(self, c, aid: int | None, artist: str | None, last: dict | None) -> dict | None:
        """The folder to file a download in: where the artist's other pictures are; for an artist new here, a folder
        of their own (Artists\\<name>) in the library folder you filed into last; else that folder as it was."""
        if aid:
            r = c.execute("SELECT f.folder_id, CASE WHEN instr(f.rel, '/') THEN substr(f.rel, 1, length(f.rel) - length(f.name) - 1) ELSE '' END sub,"
                          " COUNT(*) n FROM files f JOIN folders d ON d.id=f.folder_id WHERE f.artist_id=? AND f.missing=0 AND d.online=1"
                          " GROUP BY f.folder_id, sub ORDER BY n DESC, sub = '', length(sub) LIMIT 1", (aid,)).fetchone()
            if r:
                return {"folder": r[0], "sub": r[1], "why": "where their other pictures are"}
        base = last["folder"] if last else (c.execute("SELECT id FROM folders WHERE online=1 ORDER BY id LIMIT 1").fetchone() or [None])[0]
        if not base:
            return None
        if artist:
            return {"folder": base, "sub": f"Artists/{safe_folder(artist)}", "why": "a folder of their own"}
        sub = last["sub"] if last else ""
        if credits.from_folder(f"{sub}/x"):  # the last one went to its artist's folder: not where someone else's goes
            sub = ""
        return {"folder": base, "sub": sub, "why": "where you filed last" if last and sub == last["sub"] else ""}

    # ---------------------------------------------------------------- filing
    def _pick(self, keys) -> list[dict]:
        want = {str(k) for k in (keys or [])}
        return [e for e in self.entries() if e["key"] in want]

    def file(self, keys, folder_id: int, sub: str = "", artist=None) -> dict:
        """Move these downloads into a library folder and index them there, with what was found about each (and your
        artist for them, if you typed one: '' for none)."""
        db = self.lib.db
        picked = self._pick(keys)
        if not picked:
            raise fileops.OpError("Those downloads aren't there any more.")
        sub = fileops._clean_sub(sub)
        with db.read() as c:
            d = c.execute("SELECT id, path, online FROM folders WHERE id=?", (int(folder_id),)).fetchone()
        if not d:
            raise fileops.OpError("That folder isn't in the library.")
        if not d["online"] or not os.path.isdir(d["path"]):
            raise fileops.OpError("That folder isn't reachable right now (a drive that isn't plugged in?).")
        skipped = next((p for p in sub.split("/") if p and scan.skip_dir(p, bool(self.lib.settings.get("skip_eagle")))), None)
        if skipped:
            raise fileops.OpError(f"The library leaves folders named {skipped} out (hidden, system or Eagle's): pick another name.")
        sub = fileops._real_sub(d["path"], sub)
        dest = os.path.join(d["path"], *sub.split("/")) if sub else d["path"]
        moved, failed, taken = [], [], set()
        abouts = {e["key"]: self._about(e) for e in picked}  # (before they move: on another drive, where it was downloaded from doesn't go with it)
        # a download that's the same as a file the index has lost track of (deleted, or on a drive that isn't there) takes
        # over that file's record, ratings and all: undoing it must give the record back as it was now, not delete it
        with db.read() as c:
            newest = c.execute("SELECT COALESCE(MAX(id), 0) FROM files").fetchone()[0]
            before = {}
            held = fileops._held(c, d["id"], sub)
            for e in picked:
                about = abouts[e["key"]]
                for r in c.execute("SELECT id, folder_id, rel, missing FROM files WHERE fp=? AND size=?", (about["fp"], e["size"])) if about["fp"] else ():
                    before[r["id"]] = dict(r)
                name = fileops._free_name(dest, e["name"], taken, held)
                rel = f"{sub}/{name}" if sub else name
                try:
                    fileops._move_file(e["path"], os.path.join(dest, name))
                except (OSError, fileops.OpError) as ex:
                    failed.append({"key": e["key"], "name": e["name"], "error": str(ex)})
                    continue
                moved.append({"from": e["path"], "to_folder": d["id"], "to_rel": rel, "found": about["found"], "fp": about["fp"], "size": e["size"],
                              "note": about.get("note")})
        if not moved:
            raise fileops.OpError(failed[0]["error"] if failed else "Nothing was filed.")
        folder = {"id": d["id"], "path": d["path"]}
        for _ in range(3):  # the folder watcher may be indexing the same new files right now: then they're there already
            try:
                scan.index_files(db, folder, [m["to_rel"] for m in moved], skip_eagle=bool(self.lib.settings.get("skip_eagle")))
                break
            except Exception:
                time.sleep(0.3)
        with db.write() as c:
            done = []
            for m in moved:
                r = c.execute("SELECT id FROM files WHERE folder_id=? AND rel=?", (m["to_folder"], m["to_rel"])).fetchone()
                if not r:  # (not indexed: back where it came from, rather than lost in a folder nothing looks in)
                    try:
                        fileops._move_file(os.path.join(dest, m["to_rel"].split("/")[-1]), m["from"])
                    except (OSError, fileops.OpError):
                        pass
                    failed.append({"key": key_of(m["from"]), "name": os.path.basename(m["from"]), "error": "the library didn't take it in there"})
                    continue
                m["id"] = r[0]
                if r[0] <= newest and r[0] in before:
                    m["was"] = before[r[0]]
                    u = c.execute("SELECT artist, site, post, url FROM credits WHERE file_id=? AND src='user'", (r[0],)).fetchone()
                    m["user_was"] = dict(u) if u else None  # (what you'd typed for it: yours again on Undo)
                credits.store(c, r[0], m.pop("found"))  # shown at once (the file, its name, its download; its folder's read next)
                if artist is not None:
                    credits.set_user(c, r[0], artist=artist)
                if m.get("note"):
                    m["added_tags"], m["added_note"] = _apply_note(c, r[0], m["note"])
                done.append(m)
            credits.resolve(c, [m["id"] for m in done])
            for m in done:  # the artist's page, from the note: one of their links (if they're who it's filed as: not one you typed instead)
                url = (m.get("note") or {}).get("artist_url")
                row = c.execute("SELECT a.id, a.name, a.aliases FROM files f JOIN artists a ON a.id=f.artist_id WHERE f.id=?", (m["id"],)).fetchone() if url else None
                said = credits.norm((m.get("note") or {}).get("artist") or "")
                aid = row["id"] if row and said and said in {credits.norm(x) for x in [row["name"], *json.loads(row["aliases"] or "[]")]} else None
                if aid:
                    links = json.loads(c.execute("SELECT links FROM artists WHERE id=?", (aid,)).fetchone()[0] or "[]")
                    if url not in links:
                        c.execute("UPDATE artists SET links=? WHERE id=?", (json.dumps((links + [url])[:16]), aid))
                        m["link"] = [aid, url]  # (taken off again on Undo)
            c.execute("INSERT INTO library_meta (key, value) VALUES ('inbox_last', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (json.dumps({"folder": d["id"], "sub": sub}),))
            n = len(done)
            op = fileops.record(c, "file", f"Filed {n} download{'s' if n != 1 else ''} in {os.path.basename(dest) or dest}",
                                [{k: m[k] for k in ("id", "from", "to_folder", "to_rel", "was", "user_was", "link", "note", "added_tags", "added_note") if k in m}
                                 for m in done]) if done else None
            fileops._recount(c)
        for m in done:  # its note has been read into the index: it goes (Undo writes it back beside the download)
            if m.get("note"):
                try:
                    os.remove(m["from"] + SIDECAR)
                except OSError:
                    pass
        self._listing = None
        self._changes += 1
        self.lib.thumbs.nudge()
        self.lib.meta.nudge()
        self.lib.credits.nudge()
        db.user_change()
        return {"op": op, "filed": len(done), "failed": failed, "dest": dest}

    def dismiss(self, keys) -> int:
        picked = self._pick(keys)
        with self.lib.db.write() as c:
            c.executemany("INSERT OR REPLACE INTO inbox_dismissed (path, size, mtime) VALUES (?,?,?)", [(e["path"], e["size"], e["mtime"]) for e in picked])
        self._listing = None
        self._changes += 1
        return len(picked)

    def undismiss(self) -> int:
        with self.lib.db.write() as c:
            n = c.execute("DELETE FROM inbox_dismissed").rowcount
        self._listing = None
        self._changes += 1
        return n

    def recycle(self, keys) -> dict:
        """These downloads to the Recycle Bin (one the library has already, say)."""
        from send2trash import send2trash
        gone, failed = 0, []
        for e in self._pick(keys):
            try:
                send2trash(e["path"])
                gone += 1
                if os.path.isfile(e["path"] + SIDECAR):  # (its note with it)
                    send2trash(e["path"] + SIDECAR)
            except OSError as ex:
                failed.append({"key": e["key"], "name": e["name"], "error": str(ex)})
        self._listing = None
        self._changes += 1
        return {"recycled": gone, "failed": failed}

    def path_of(self, key: str) -> dict | None:
        return next((e for e in self.entries() if e["key"] == key), None)

    def thumb(self, key: str, size: int = 320) -> str | None:
        """A thumbnail of a download (kept in the data folder's thumbs\\downloads)."""
        from . import thumbs
        e = self.path_of(key)
        if not e:
            return None
        h = hashlib.sha1(f"{e['path']}|{e['size']}|{e['mtime']}|{size}".encode("utf-8")).hexdigest()
        out = os.path.join(self.lib.data_dir, "thumbs", "downloads", h[:2], h + ".webp")
        if os.path.isfile(out):
            return out
        try:
            if e["kind"] == "video":
                ff = thumbs.find_ffmpeg(self.lib.settings.get("ffmpeg"))
                if not ff:
                    return None
                data = thumbs.video_thumb(ff, e["path"], size)
            else:
                data = thumbs.image_thumb(e["path"], size)
        except Exception:
            return None
        from .config import write_atomic
        write_atomic(out, data)
        return out


def _norm(tag) -> str:
    return re.sub(r"\s+", " ", str(tag or "").replace("_", " ")).strip().lower()


def _yours(c) -> list[dict]:
    """Your characters as New downloads offers them: {id, name, names, tag, rules} (names: its name and aliases; rules: its
    word rules, tag_key)."""
    from . import characters as chars
    rules: dict = {}
    for r in c.execute("SELECT character_id, value FROM character_rules WHERE kind='word'"):
        rules.setdefault(r[0], set()).add(chars.tag_key(r[1]))
    out = []
    for r in c.execute("SELECT id, name, aliases, shared, who FROM characters WHERE who IS NOT 'person' ORDER BY name COLLATE NOCASE"):
        try:
            tag = (json.loads(r["shared"] or "{}") or {}).get("tag") or ""
        except ValueError:
            tag = ""
        out.append({"id": r["id"], "name": r["name"], "tag": tag, "rules": rules.get(r["id"], set()),
                    "names": [r["name"], *json.loads(r["aliases"] or "[]")]})
    return out


def _offer(ch: dict, yours: list[dict]) -> dict:
    """A note's character as New downloads shows it: yours already (a rule of yours is its tag: the picture is theirs once
    filed), or what to make of it, with yours by the same name (in any order, without a tag of their own) suggested."""
    from . import characters as chars
    from .charsync import display
    out = {"tag": ch["tag"], "name": ch.get("name") or display(ch["tag"]), "series": ch.get("series") or ""}
    k = chars.tag_key(display(ch["tag"]))
    have = next((y for y in yours if k in y["rules"]), None)
    if have:
        out["have"] = {"id": have["id"], "name": have["name"]}
    else:
        out["suggest"] = [{"id": y["id"], "name": y["name"]} for y in yours if not y["tag"] and any(chars.same_name(out["name"], n) for n in y["names"])][:3]
    return out


def _apply_note(c, fid: int, note: dict) -> tuple[list[str], bool]:
    """A note's tags (marked as from the site: taking one off keeps it off) and its title as the picture's note
    (unless it has one already). -> (the tags it added, whether it added the note)."""
    from . import metadata
    added = []
    for t in metadata._split(note.get("tags") or []):
        tid = metadata.tag_id(c, t)
        if c.execute("INSERT OR IGNORE INTO file_tags (file_id, tag_id, source) VALUES (?, ?, 'site')", (fid, tid)).rowcount:
            added.append(t)
    text = "\n\n".join(x for x in (note.get("title"), note.get("description")) if x)
    noted = bool(text) and not c.execute("SELECT 1 FROM file_notes WHERE file_id=?", (fid,)).fetchone()
    if noted:
        c.execute("INSERT INTO file_notes (file_id, note, source) VALUES (?, ?, 'site')", (fid, text[:4000]))
    metadata.refresh_search(c, fid)
    from . import characters
    characters.match_file(c, fid)  # (its characters, from its tags: a character's booru tag is one of them)
    return added, noted


def undo(db, data) -> dict:
    """A filing undone: each download back where it was (under a free name, if that's taken now), out of the index."""
    with db.read() as c:
        roots = {r["id"]: r["path"] for r in c.execute("SELECT id, path FROM folders")}
    back, failed = 0, []
    for it in reversed(data):
        with db.read() as c:
            cur = c.execute("SELECT folder_id, rel, missing FROM files WHERE id=?", (it["id"],)).fetchone()
        if not cur or cur["folder_id"] != it["to_folder"] or cur["rel"] != it["to_rel"] or cur["missing"] not in fileops.PRESENT:
            failed.append(it["id"])  # moved, deleted or in the Trash since: left alone
            continue
        src = fileops._full(roots[it["to_folder"]], it["to_rel"])
        home, name = os.path.split(it["from"])
        dst = os.path.join(home, fileops._free_name(home, name, set()))
        try:
            fileops._move_file(src, dst)
        except (OSError, fileops.OpError):
            failed.append(it["id"])
            continue
        if it.get("note"):  # its note back beside it, for filing again
            try:
                with open(dst + SIDECAR, "w", encoding="utf-8") as f:
                    json.dump(it["note"], f, ensure_ascii=False)
            except OSError:
                pass
        with db.write() as c:
            was = it.get("was")
            if was:  # (a record it took over: what the note gave it goes again, and only that)
                from .metadata import tag_id
                c.executemany("DELETE FROM file_tags WHERE file_id=? AND tag_id=? AND source='site'", [(it["id"], tag_id(c, t)) for t in it.get("added_tags") or []])
                if it.get("added_note"):
                    c.execute("DELETE FROM file_notes WHERE file_id=? AND source='site'", (it["id"],))
                c.execute("DELETE FROM credits WHERE file_id=? AND src='note'", (it["id"],))
                if "user_was" in it:  # yours again, as it was
                    credits.set_user(c, it["id"], artist=None, url=None)
                    u = it["user_was"]
                    if u:
                        c.execute("INSERT INTO credits (file_id, src, artist, site, post, url) VALUES (?, 'user', ?,?,?,?)",
                                  (it["id"], u["artist"], u["site"], u["post"], u["url"]))
            if it.get("link"):  # the artist's page the note gave them
                aid, url = it["link"]
                row = c.execute("SELECT links FROM artists WHERE id=?", (aid,)).fetchone()
                if row:
                    c.execute("UPDATE artists SET links=? WHERE id=?", (json.dumps([u for u in json.loads(row[0] or "[]") if u != url]), aid))
            if not was:
                c.execute("DELETE FROM files WHERE id=?", (it["id"],))
            else:  # the record it took over goes back where it was, missing as it was
                name = was["rel"].split("/")[-1]
                try:
                    c.execute("UPDATE files SET folder_id=?, rel=?, name=?, ext=?, missing=?, credited=0 WHERE id=?",
                              (was["folder_id"], was["rel"], name, os.path.splitext(name)[1].lower(), was["missing"] or 1, it["id"]))
                except Exception:  # its old name is someone else's now: it stays here, missing
                    c.execute("UPDATE files SET missing=1 WHERE id=?", (it["id"],))
                from .metadata import refresh_search
                refresh_search(c, it["id"])
                from . import characters
                characters.match_file(c, it["id"])  # (the note's tags gone: the characters they made it go too)
        back += 1
    with db.write() as c:
        credits.prune(c)
    return {"undone": back, "failed": failed}
