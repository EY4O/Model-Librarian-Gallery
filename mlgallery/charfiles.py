"""Characters the Chrome extension left for the Gallery, filed into Model Librarian's character list.

When ComfyUI isn't running, the extension can't hand a character from a booru post to Model Librarian, so it saves it
as <tag>.mlchar.json in Downloads\\Model Librarian Gallery: {"v": 1, "character": {...}, "picture": "data:image/webp;..."}.
While the Gallery is open it looks there every little while and files each one into characters.json in Model
Librarian's shared data folder (charstore.py, the same file as Model Librarian's), then removes it. Without Model
Librarian's folder they wait. The sync that follows (charsync.py) brings them into the Gallery's Characters.
"""
from __future__ import annotations

import json
import os
import threading
import time

from . import charstore
from .downloads import EXT_FOLDER, downloads_folder

SUFFIX = ".mlchar.json"
FILE_MAX = 600_000      # a character and its small picture: anything much bigger isn't one
EVERY = 15              # seconds between looks
STALE = 30.0            # a lock file this old was left by a process that stopped (as Model Librarian reckons it)
WAIT = 15.0             # how long to wait for the lock before trying again later


def waiting(folder: str | None) -> list[str]:
    """The character files in the extension's folder, oldest first."""
    if not folder or not os.path.isdir(folder):
        return []
    try:
        found = [e for e in os.scandir(folder) if e.name.lower().endswith(SUFFIX) and e.is_file(follow_symlinks=False)]
    except OSError:
        return []
    return [e.path for e in sorted(found, key=lambda e: e.stat().st_mtime)]


def read(path: str) -> dict | None:
    """{character, picture} from one file, or None when it isn't one."""
    try:
        if os.path.getsize(path) > FILE_MAX:
            return None
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("v") != 1:
        return None
    imp = charstore.clean_import(d.get("character"))
    return {"character": imp, "picture": charstore.picture(d.get("picture"))} if imp else None


class _Lock:
    """Model Librarian's lock on a data file (<file>.lock, made exclusively), so the two never write it at once."""

    def __init__(self, path: str):
        self.lock = path + ".lock"
        self.held = False

    def __enter__(self):
        start = time.time()
        while True:
            try:
                fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                self.held = True
                return self
            except FileExistsError:
                try:
                    if time.time() - os.stat(self.lock).st_mtime > STALE:
                        os.remove(self.lock)  # (its holder stopped)
                        continue
                except OSError:
                    continue  # released meanwhile
            except OSError:
                pass  # (Windows: being deleted right now)
            if time.time() - start > WAIT:
                raise TimeoutError("Model Librarian is busy with its character list")
            time.sleep(0.05)

    def __exit__(self, *exc):
        for i in range(5):
            try:
                os.remove(self.lock)
                return
            except FileNotFoundError:
                return
            except OSError:
                time.sleep(0.02 * (i + 1))


def _write(path: str, obj) -> None:
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def file_into(ml_dir: str, items: list[dict], now: float | None = None) -> list[dict]:
    """Files these {character, picture} into ml_dir's characters.json -> the characters, as filed."""
    path = os.path.join(ml_dir, charstore.FILE)
    out = []
    with _Lock(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            data = charstore.empty()
        if not isinstance(data, dict):
            raise ValueError("Model Librarian's character list isn't readable")
        for it in items:
            cid = charstore.new_id()
            c, _ = charstore.apply_import(data, it["character"], now or time.time(), cid)
            if it.get("picture") and not c.get("image"):
                rel = charstore.picture_path(cid, it["picture"][1])
                os.makedirs(os.path.join(ml_dir, charstore.PICTURES), exist_ok=True)
                with open(os.path.join(ml_dir, *rel.split("/")), "wb") as f:
                    f.write(it["picture"][0])
                c["image"] = rel
            out.append(dict(c))
        _write(path, data)
    return out


def pickup(ml_dir: str | None, folder: str | None) -> int:
    """Files every waiting character into Model Librarian's list and removes its file -> how many. With no Model
    Librarian folder they stay where they are."""
    paths = waiting(folder)
    if not ml_dir or not paths:
        return 0
    items, done = [], []
    for p in paths:
        it = read(p)
        if it:
            items.append(it)
            done.append(p)
    if items:
        file_into(ml_dir, items)
    for p in done:
        try:
            os.remove(p)
        except OSError:
            pass
    return len(items)


def folders(lib) -> list[str]:
    """The extension's folder in Downloads, and any other `Model Librarian Gallery` folder added in Settings, New downloads
    (Chrome saving somewhere else)."""
    dl = downloads_folder()
    out = [os.path.join(dl, EXT_FOLDER)] if dl else []
    out += [p for p in lib.settings.get("inbox_folders") or [] if isinstance(p, str) and os.path.basename(p.rstrip("\\/")).lower() == EXT_FOLDER.lower()]
    return list({os.path.normcase(os.path.abspath(p)): p for p in out}.values())


def loop(lib, stop: threading.Event, every: float = EVERY) -> None:
    """While the Gallery is open: the extension's characters, filed as they arrive, and your characters kept in step with
    Model Librarian's list (charsync.py): every little while, and at once after a character changes here."""
    while not stop.is_set():
        for folder in folders(lib):
            try:
                pickup(lib.models.data_dir(), folder)
            except Exception:
                pass  # (Model Librarian busy, a file half written: next time)
        try:
            lib.sync_characters()
        except Exception:
            pass
        lib.char_wake.wait(every)
        lib.char_wake.clear()
