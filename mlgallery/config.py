"""Where the Gallery keeps its own data (the index, thumbnails, settings) and the settings themselves.

Nothing here is ever written next to your media: everything lives in the data folder,
%LOCALAPPDATA%\\Model Librarian Gallery by default (or --data / MLG_DATA).
"""
from __future__ import annotations

import json
import os
import tempfile
import threading

from . import APP_NAME

DEFAULTS = {
    "thumb_size": 256,          # the short side of a grid thumbnail, in pixels
    "thumb_workers": 3,         # thumbnails made at once in the background
    "skip_eagle": True,         # never index Eagle libraries (*.library folders) inside a chosen folder
    "ffmpeg": "",               # path to ffmpeg for video posters ("" = look on PATH)
    "ml_data": "",              # Model Librarian's shared data folder ("" = find it)
    "comfy_url": "http://127.0.0.1:8188",  # where ComfyUI runs, for Open in ComfyUI
    "eagle_url": "http://localhost:41595",  # Eagle's local API, for Send to Eagle
    "eagle_token": "",          # Eagle's API token, if your Eagle asks for one (you type it in Settings)
    "updates": False,           # ask GitHub once a day whether there's a newer Gallery (nothing else is sent)
    "inbox_downloads": True,    # New downloads: offer the pictures in Windows' Downloads folder for filing (a library for illustrations)
    "inbox_folders": [],        # ... and these folders' too (a Discord or Telegram save folder, say)
    "inbox_notify": False,      # a Windows notification when new pictures arrive there
    "remote": False,            # Phone and tablet: paired devices on the home network or Tailscale can open the library
    "remote_port": 8766,        # ... on this port (it stays the same, so a paired device's address does)
    "remote_devices": [],       # ... these: {id, name, hash of its key, added, seen} (remote.py)
    "tray": False,              # closing the window leaves the Gallery running in the tray (tray.py)
    "tray_login": False,        # ... and it starts there when you sign in to Windows (needs tray)
}


def default_data_dir() -> str:
    env = os.environ.get("MLG_DATA")
    if env:
        return env
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_NAME)


def write_atomic(path: str, data: bytes) -> None:
    """Write via a temporary file in the same folder, so a crash never leaves half a file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


class Settings:
    """settings.json in the data folder, with defaults for anything not set."""

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "settings.json")
        self._lock = threading.Lock()
        self._values = dict(DEFAULTS)
        try:
            with open(self.path, encoding="utf-8") as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                self._values.update({k: v for k, v in stored.items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass

    def get(self, key: str):
        return self._values.get(key, DEFAULTS.get(key))

    def fresh(self) -> None:
        """Read the file again if it changed (another Gallery on this PC, with another library, may have written it)."""
        try:
            mtime = os.stat(self.path).st_mtime_ns
        except OSError:
            return
        if mtime == getattr(self, "_mtime", None):
            return
        with self._lock:
            try:
                with open(self.path, encoding="utf-8") as f:
                    stored = json.load(f)
                if isinstance(stored, dict):
                    self._values.update({k: v for k, v in stored.items() if k in DEFAULTS})
                self._mtime = mtime
            except (OSError, ValueError):
                pass

    def all(self) -> dict:
        return dict(self._values)

    def change(self, key: str, fn) -> object:
        """One setting changed from what it is in the file right now (read and written under the lock: another Gallery
        on this PC, or another thread, changing it meanwhile isn't undone). -> the new value."""
        with self._lock:
            try:
                with open(self.path, encoding="utf-8") as f:
                    stored = json.load(f)
                if isinstance(stored, dict):
                    self._values.update({k: v for k, v in stored.items() if k in DEFAULTS})
            except (OSError, ValueError):
                pass
            value = fn(self._values.get(key, DEFAULTS.get(key)))
            if key in DEFAULTS and isinstance(value, type(DEFAULTS[key])):
                self._values[key] = value
            write_atomic(self.path, json.dumps(self._values, indent=1).encode("utf-8"))
            try:
                self._mtime = os.stat(self.path).st_mtime_ns
            except OSError:
                pass
            return self._values.get(key)

    def update(self, patch: dict) -> dict:
        with self._lock:
            try:  # another Gallery on this PC (another library) may have changed other settings meanwhile: keep those
                with open(self.path, encoding="utf-8") as f:
                    stored = json.load(f)
                if isinstance(stored, dict):
                    self._values.update({k: v for k, v in stored.items() if k in DEFAULTS})
            except (OSError, ValueError):
                pass
            for k, v in patch.items():
                if k in DEFAULTS and isinstance(v, type(DEFAULTS[k])):
                    self._values[k] = v
            write_atomic(self.path, json.dumps(self._values, indent=1).encode("utf-8"))
            try:
                self._mtime = os.stat(self.path).st_mtime_ns
            except OSError:
                pass
            return dict(self._values)
