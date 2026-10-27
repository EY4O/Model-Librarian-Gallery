"""Characters the Chrome extension left for the Gallery, filed into Model Librarian's character list.

When ComfyUI isn't running, the extension can't hand a character from a booru post to Model Librarian, so it saves it
as <tag>.mlchar.json in Downloads\\Model Librarian Gallery: {"v": 1, "character": {...}, "picture": "data:image/webp;..."}.
While the Gallery is open it looks there every little while and files each one into characters.json in Model
Librarian's shared data folder (charstore.py, the same file as Model Librarian's), then removes it, all under the
list's lock (so two Galleries open at once can't both file one). Without Model Librarian's folder they wait. The sync
that follows (charsync.py) brings them into the Gallery's Characters.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time

from . import charstore
from .downloads import EXT_FOLDER, downloads_folder

SUFFIX = ".mlchar.json"
FILE_MAX = 600_000      # a character and its small picture: anything much bigger isn't one
EVERY = 15              # seconds between looks
STALE = 30.0            # a lock file unchanged this long was left by a process that stopped (as Model Librarian reckons it)
WAIT = 15.0             # how long to wait for the lock before trying again later
BEAT = 5.0              # how often a lock held is shown to be alive (Model Librarian's holders do the same)
_filed: set = set()     # files filed that couldn't be removed (held open): not filed again


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
        with open(path, encoding="utf-8-sig") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("v") != 1:
        return None
    imp = charstore.clean_import(d.get("character"))
    return {"character": imp, "picture": charstore.picture(d.get("picture"))} if imp else None


def read_list(path: str) -> dict:
    """Model Librarian's list, checked (charstore.check): ValueError when it's broken or from a newer version."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except FileNotFoundError:
        return charstore.empty()
    except ValueError:
        raise ValueError("Model Librarian's character list isn't readable") from None
    return charstore.check(data)


class _Lock:
    """Model Librarian's lock on a data file (<file>.lock, made exclusively; see its smf/config.py file_lock), so the
    two never write it at once. Held, it's touched every few seconds; one left unchanged for STALE seconds is broken
    the way Model Librarian breaks one; only a lock this holds is ever removed."""

    def __init__(self, path: str):
        self.lock = path + ".lock"
        self.mine = f"gallery {os.getpid()} {secrets.token_hex(4)}".encode()
        self._stop = threading.Event()

    def __enter__(self):
        start = time.time()
        while True:
            try:
                fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, self.mine)
                os.close(fd)
                threading.Thread(target=self._beat, name="characters-lock", daemon=True).start()
                return self
            except FileExistsError:
                try:
                    if time.time() - os.stat(self.lock).st_mtime > STALE:
                        self._break_stale()
                        continue
                except OSError:
                    continue  # released meanwhile
            except OSError:
                pass  # (Windows: being deleted right now)
            if time.time() - start > WAIT:
                raise TimeoutError("Model Librarian is busy with its character list")
            time.sleep(0.05)

    def _beat(self):
        while not self._stop.wait(BEAT):
            try:
                os.utime(self.lock)
            except OSError:
                pass

    def _break_stale(self):
        """Renamed first, so two waiters can't both break it, and put back if it turns out to be fresh after all."""
        grave = f"{self.lock}.{os.getpid()}.{threading.get_ident()}.stale"
        try:
            os.rename(self.lock, grave)
        except OSError:
            return
        try:
            if time.time() - os.stat(grave).st_mtime > STALE:
                os.remove(grave)
                return
            with open(grave, "rb") as f:
                owner = f.read()
            try:
                fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, owner)
                os.close(fd)
            finally:
                os.remove(grave)
        except OSError:
            pass

    def __exit__(self, *exc):
        self._stop.set()
        try:
            with open(self.lock, "rb") as f:
                if f.read() != self.mine:
                    return  # (not ours any more: broken as stale and taken by another)
        except OSError:
            return
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


def _file(ml_dir: str, data: dict, it: dict, now: float) -> dict:
    """One {character, picture} into the list (changed in place) -> the character, as filed. Its picture is named after
    it and written before the list names it."""
    c, _ = charstore.apply_import(data, it["character"], now)
    if it.get("picture") and not c.get("image"):
        rel = charstore.picture_path(c["id"], it["picture"][1])
        os.makedirs(os.path.join(ml_dir, charstore.PICTURES), exist_ok=True)
        with open(os.path.join(ml_dir, *rel.split("/")), "wb") as f:
            f.write(it["picture"][0])
        c["image"] = rel
        charstore.stamp(c, ["image"], now)
    return dict(c)


def file_into(ml_dir: str, items: list[dict], now: float | None = None) -> list[dict]:
    """Files these {character, picture} into ml_dir's characters.json -> the characters, as filed."""
    path = os.path.join(ml_dir, charstore.FILE)
    with _Lock(path):
        data = read_list(path)
        out = [_file(ml_dir, data, it, now or time.time()) for it in items]
        _write(path, data)
    return out


def _key(path: str):
    st = os.stat(path)
    return (os.path.normcase(os.path.abspath(path)), st.st_size, st.st_mtime_ns)


def pickup(ml_dir: str | None, folder: str | None) -> int:
    """Files every waiting character into Model Librarian's list and removes its file -> how many. All of it under the
    list's lock: another Gallery finds the file gone. One that can't be removed (held open) isn't filed again. With no
    Model Librarian folder they stay where they are."""
    if not ml_dir or not waiting(folder):
        return 0
    path = os.path.join(ml_dir, charstore.FILE)
    n = 0
    with _Lock(path):
        data = read_list(path)
        done = []
        for p in waiting(folder):  # (again, now the lock is held)
            try:
                key = _key(p)
            except OSError:
                continue
            if key in _filed:
                done.append((p, key))  # (filed already: only its removal to try again)
                continue
            it = read(p)
            if it:
                _file(ml_dir, data, it, time.time())
                done.append((p, key))
                n += 1
        if n:
            _write(path, data)
        for p, key in done:
            try:
                os.remove(p)
                _filed.discard(key)
            except OSError:
                _filed.add(key)
    return n


def folders(lib) -> list[str]:
    """The extension's folder in Downloads, and any other `Model Librarian Gallery` folder added in Settings, New downloads
    (Chrome saving somewhere else)."""
    dl = downloads_folder()
    out = [os.path.join(dl, EXT_FOLDER)] if dl else []
    out += [p for p in lib.settings.get("inbox_folders") or [] if isinstance(p, str) and os.path.basename(p.rstrip("\\/")).lower() == EXT_FOLDER.lower()]
    return list({os.path.normcase(os.path.abspath(p)): p for p in out}.values())


def loop(lib, stop: threading.Event, every: float = EVERY) -> None:
    """While the Gallery is open: the extension's characters, filed as they arrive, and your characters kept in step with
    Model Librarian's list (charsync.py): every little while, and at once after a character changes here. What stops it
    (a broken list, Model Librarian busy) is kept on the library for the character page to say."""
    while not stop.is_set():
        lib.char_wake.clear()  # (a change from now on wakes the next round)
        errors = []
        for folder in folders(lib):
            try:
                pickup(lib.models.data_dir(), folder)
            except Exception as ex:  # (Model Librarian busy, a broken list: next time)
                errors.append(str(ex))
        try:
            lib.sync_characters()
        except Exception as ex:
            errors.append(str(ex))
        lib.char_sync_error = errors[0] if errors else None
        lib.char_wake.wait(every)
