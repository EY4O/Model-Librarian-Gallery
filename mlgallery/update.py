"""Updates from GitHub Releases: off unless you turn them on in Settings (or press Check now).

* Checking asks GitHub's API which release of EY4O/Model-Librarian-Gallery is the newest. Nothing about you or
  your library is sent; with the switch on it asks once a day.
* A release carries mlgallery-<version>.zip: the portable folder's app\\ (mlgallery\\, start.pyw, requirements.txt).
  Installing downloads it, checks its SHA-256 (GitHub's own digest of the asset, or the .sha256 file beside it),
  unpacks it into update\\app beside app\\ and marks it ready. Model Librarian Gallery.exe swaps it in at the next
  start, once no Gallery is open (Restart now does that straight away).
* Only the portable folder updates itself (a checkout updates with git pull), and only to a release built on the
  same packages: one that needs others says so, and the full portable folder is on the release page.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import shutil
import time
import zipfile

from . import APP_NAME, VERSION
from .config import write_atomic

REPO = "EY4O/Model-Librarian-Gallery"
DAY = 24 * 3600
MAX_ZIP = 64 * 1024 * 1024  # the app is about 1 MB; anything near this isn't one


class UpdateError(Exception):
    pass


def api_base() -> str:
    return (os.environ.get("MLG_UPDATE_API") or "https://api.github.com").rstrip("/")  # the tests' stand-in for GitHub


def version_tuple(v: str) -> tuple | None:
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", (v or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def portable_root() -> str | None:
    """The portable folder this Gallery runs from (its launcher, python\\ and app\\ side by side), or None."""
    app = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    root = os.path.dirname(app)
    if os.path.isfile(os.path.join(app, "start.pyw")) and os.path.isfile(os.path.join(root, f"{APP_NAME}.exe")):
        return root
    return None


def _requirements(path: str) -> list[str] | None:
    try:
        with open(path, encoding="utf-8") as f:
            return sorted(ln.split("#")[0].strip().lower() for ln in f if ln.split("#")[0].strip())
    except OSError:
        return None


class Updater:
    """What's known about updates (kept in update.json in the data folder) and the steps to install one."""

    def __init__(self, data_dir: str, settings, root: str | None = ...):
        self.path = os.path.join(data_dir, "update.json")
        self.settings = settings
        self.root = portable_root() if root is ... else root
        self.busy = False
        try:
            with open(self.path, encoding="utf-8") as f:
                self.state = json.load(f)
            if not isinstance(self.state, dict):
                self.state = {}
        except (OSError, ValueError):
            self.state = {}

    def _save(self) -> None:
        write_atomic(self.path, json.dumps(self.state, indent=1).encode("utf-8"))

    def staged(self) -> str | None:
        """The version waiting in update\\app for the next start."""
        if not self.root:
            return None
        try:
            with open(os.path.join(self.root, "update", "ready"), encoding="utf-8") as f:
                v = f.read().strip()
        except OSError:
            return None
        return v if version_tuple(v) and os.path.isfile(os.path.join(self.root, "update", "app", "start.pyw")) else None

    def newer(self) -> dict | None:
        """The release last seen, if it's newer than this Gallery."""
        rel = self.state.get("latest")
        if not isinstance(rel, dict) or not version_tuple(rel.get("version", "")):
            return None
        return rel if version_tuple(rel["version"]) > version_tuple(VERSION) else None

    def available(self) -> str | None:
        """A newer version worth mentioning: not the one already waiting for the next start."""
        rel = self.newer()
        return rel["version"] if rel and rel["version"] != self.staged() else None

    def info(self) -> dict:
        rel = self.newer()
        return {"current": VERSION, "portable": bool(self.root), "checked": self.state.get("checked"),
                "error": self.state.get("error"), "latest": (self.state.get("latest") or {}).get("version"),
                "newer": rel, "staged": self.staged(), "busy": self.busy}

    def due(self, now: float | None = None) -> bool:
        return bool(self.settings.get("updates")) and (now or time.time()) - float(self.state.get("checked") or 0) >= DAY

    # ---------------------------------------------------------------- GitHub
    async def _get(self, session, url: str, headers=None):
        import aiohttp
        try:
            async with session.get(url, headers=headers) as r:
                if r.status == 404:
                    raise UpdateError("There are no releases on GitHub yet.")
                if r.status in (403, 429):
                    raise UpdateError("GitHub is limiting how often it's asked: try again in an hour.")
                if r.status >= 400:
                    raise UpdateError(f"GitHub said {r.status}.")
                return await r.read()
        except aiohttp.ClientConnectorError:
            raise UpdateError("Couldn't reach GitHub: is this PC online?")
        except (aiohttp.ClientError, asyncio.TimeoutError) as ex:
            raise UpdateError(f"Couldn't reach GitHub: {ex}")

    def _session(self, timeout: float):
        import aiohttp
        return aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout), headers={"User-Agent": f"{APP_NAME}/{VERSION}"})

    async def check(self) -> dict:
        """Ask GitHub for the newest release; remembered for Settings and the daily check."""
        try:
            async with self._session(20) as s:
                data = json.loads(await self._get(s, f"{api_base()}/repos/{REPO}/releases/latest", {"Accept": "application/vnd.github+json"}))
            rel = _release(data)
        except (UpdateError, ValueError) as ex:
            self.state.update(checked=time.time(), error=str(ex) if isinstance(ex, UpdateError) else "GitHub's answer didn't make sense.")
            await asyncio.to_thread(self._save)
            raise UpdateError(self.state["error"])
        self.state.update(checked=time.time(), error=None, latest=rel)
        await asyncio.to_thread(self._save)
        return self.info()

    async def install(self) -> dict:
        """Download the newer release's app zip, check it and leave it ready for the next start."""
        if not self.root:
            raise UpdateError("Only the portable Gallery updates itself: in a checkout, use git pull.")
        rel = self.newer()
        if not rel:
            raise UpdateError("There's no newer version to install: check again first.")
        if not rel.get("zip"):
            raise UpdateError(f"Release {rel['version']} has no update for the portable folder: download it from its page instead.")
        if self.busy:
            raise UpdateError("An update is already being downloaded.")
        self.busy = True
        try:
            z = rel["zip"]
            if z["size"] > MAX_ZIP:
                raise UpdateError("The update is far bigger than the Gallery: it wasn't downloaded.")
            if not (z["url"].startswith("https://") or os.environ.get("MLG_UPDATE_API")):
                raise UpdateError("The update isn't on a secure address: it wasn't downloaded.")
            async with self._session(300) as s:
                sha = z.get("sha256") or _sha_from_file(await self._get(s, z["sha_url"]) if z.get("sha_url") else b"")
                if not sha:
                    raise UpdateError(f"Release {rel['version']} doesn't say what its update should be (no SHA-256): it wasn't installed.")
                blob = await self._get(s, z["url"])
            await asyncio.to_thread(stage, self.root, blob, sha, rel["version"])
        finally:
            self.busy = False
        return self.info()

    async def daily(self) -> None:
        """While the Gallery runs: with the switch on, check once a day (looked at every hour)."""
        await asyncio.sleep(60)  # not while it's starting up
        while True:
            if self.due():
                try:
                    await self.check()
                except UpdateError:
                    pass  # said in Settings; tried again tomorrow
            await asyncio.sleep(3600)


def _release(data: dict) -> dict:
    """What matters of GitHub's answer for a release."""
    if not isinstance(data, dict) or not version_tuple(str(data.get("tag_name", ""))):
        raise UpdateError("The newest release on GitHub has no version number (like v1.2.0).")
    v = ".".join(map(str, version_tuple(data["tag_name"])))
    assets = {a.get("name"): a for a in data.get("assets") or [] if isinstance(a, dict)}
    a = assets.get(f"mlgallery-{v}.zip")
    zip_ = None
    if a and a.get("browser_download_url"):
        digest = str(a.get("digest") or "")
        sha = assets.get(f"mlgallery-{v}.zip.sha256")
        zip_ = {"url": a["browser_download_url"], "size": int(a.get("size") or 0),
                "sha256": digest[7:].lower() if digest.startswith("sha256:") else None,
                "sha_url": sha.get("browser_download_url") if sha else None}
    return {"version": v, "name": str(data.get("name") or "")[:200], "notes": str(data.get("body") or "")[:4000],
            "page": str(data.get("html_url") or ""), "published": str(data.get("published_at") or ""), "zip": zip_}


def _sha_from_file(text: bytes) -> str | None:
    m = re.match(rb"\s*([0-9a-fA-F]{64})\b", text or b"")
    return m.group(1).decode().lower() if m else None


def stage(root: str, blob: bytes, sha256: str, version: str) -> str:
    """Check the downloaded zip and unpack it as update\\app, ready for the launcher; the folder it's in."""
    if hashlib.sha256(blob).hexdigest() != sha256.lower():
        raise UpdateError("The download doesn't match the release (its SHA-256 differs): it wasn't installed.")
    upd = os.path.join(root, "update")
    part, ready_dir, ready = os.path.join(upd, "app.part"), os.path.join(upd, "app"), os.path.join(upd, "ready")
    os.makedirs(upd, exist_ok=True)
    for old in (part, ready):
        if os.path.isdir(old):
            shutil.rmtree(old, ignore_errors=True)
        elif os.path.exists(old):
            os.remove(old)
    try:
        try:
            zf = zipfile.ZipFile(io.BytesIO(blob))
        except zipfile.BadZipFile:
            raise UpdateError("The download isn't a zip: it wasn't installed.")
        with zf:
            total = 0
            for m in zf.infolist():
                name = m.filename.replace("\\", "/")
                if name.startswith("/") or re.match(r"[A-Za-z]:", name) or ".." in name.split("/"):
                    raise UpdateError("The update has a file outside its own folder: it wasn't installed.")
                total += m.file_size
                if total > MAX_ZIP * 4:
                    raise UpdateError("The update unpacks far bigger than the Gallery: it wasn't installed.")
            zf.extractall(part)
        init = os.path.join(part, "mlgallery", "__init__.py")
        if not os.path.isfile(os.path.join(part, "start.pyw")) or not os.path.isfile(init):
            raise UpdateError("The update isn't a Gallery (no mlgallery or start.pyw in it): it wasn't installed.")
        with open(init, encoding="utf-8") as f:
            m = re.search(r'^VERSION\s*=\s*"([^"]+)"', f.read(), re.M)
        if not m or m.group(1) != version:
            raise UpdateError(f"The update says it's {m.group(1) if m else 'no version'}, not {version}: it wasn't installed.")
        here = _requirements(os.path.join(root, "app", "requirements.txt"))
        if here is None or _requirements(os.path.join(part, "requirements.txt")) != here:
            raise UpdateError(f"{version} needs other packages than this folder has: download the full portable folder from the release page.")
        if os.path.isdir(ready_dir):
            shutil.rmtree(ready_dir)
        os.replace(part, ready_dir)
        with open(ready, "w", encoding="utf-8") as f:
            f.write(version)
    finally:
        shutil.rmtree(part, ignore_errors=True)
    return ready_dir
