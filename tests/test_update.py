"""Updates from GitHub Releases (against a stand-in for GitHub's API), the release zip, and the launcher's swap.

Everything happens in made-up portable folders: never the real one, never GitHub.
"""
import asyncio
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import zipfile

import pytest
from aiohttp import web

from mlgallery import VERSION, update
from mlgallery.config import Settings

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import build_portable  # noqa: E402
import release  # noqa: E402

NEXT = ".".join(map(str, update.version_tuple(VERSION)[:2] + (update.version_tuple(VERSION)[2] + 1,)))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def portable(tmp_path, name="pkg"):
    """A made-up portable folder: the launcher's place, python\\ and the app as build_portable makes it."""
    root = tmp_path / name
    (root / "python").mkdir(parents=True)
    (root / "Model Librarian Gallery.exe").write_bytes(b"MZ")
    build_portable.make_app(str(root / "app"))
    return str(root)


def app_zip(version=NEXT, requirements=None, extra=None):
    """An update zip for `version`, built from this checkout like release.py's."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("start.pyw", build_portable.START)
        z.writestr("mlgallery/__init__.py", f'VERSION = "{version}"\n')
        with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as f:
            z.writestr("requirements.txt", requirements if requirements is not None else f.read())
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


async def fake_github(tag=f"v{NEXT}", blob=None, digest=True, status=200, sha_file=False):
    """GitHub's latest-release answer and the asset downloads."""
    blob = app_zip() if blob is None else blob
    seen = []

    async def latest(request):
        seen.append(request.headers.get("User-Agent", ""))
        if status != 200:
            return web.json_response({"message": "nope"}, status=status)
        base = f"http://127.0.0.1:{request.url.port}"
        v = tag.lstrip("v")
        assets = [{"name": f"mlgallery-{v}.zip", "size": len(blob), "browser_download_url": f"{base}/dl/app.zip",
                   **({"digest": "sha256:" + hashlib.sha256(blob).hexdigest()} if digest else {})}]
        if sha_file:
            assets.append({"name": f"mlgallery-{v}.zip.sha256", "browser_download_url": f"{base}/dl/app.zip.sha256"})
        return web.json_response({"tag_name": tag, "name": v, "body": "What's new: the lighthouse.", "html_url": f"{base}/release",
                                  "published_at": "2026-10-24T10:00:00Z", "assets": assets})

    async def dl(request):
        if request.match_info["name"] == "app.zip.sha256":
            return web.Response(text=f"{hashlib.sha256(blob).hexdigest()}  mlgallery.zip\n")
        if request.match_info["name"] == "app.zip":  # like github.com: a redirect with a huge security header
            raise web.HTTPFound("/dl/stored.zip", headers={"Content-Security-Policy": "default-src 'none'; " + "img-src x.example; " * 800})
        return web.Response(body=blob)
    app = web.Application()
    app.router.add_get(f"/repos/{update.REPO}/releases/latest", latest)
    app.router.add_get("/dl/{name}", dl)
    runner = web.AppRunner(app)
    await runner.setup()
    port = free_port()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner, f"http://127.0.0.1:{port}", seen


def with_github(monkeypatch, fn, **kw):
    async def go():
        runner, url, seen = await fake_github(**kw)
        monkeypatch.setenv("MLG_UPDATE_API", url)
        try:
            return await fn(seen)
        finally:
            await runner.cleanup()
    return asyncio.run(go())


def updater(tmp_path, root=None):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    return update.Updater(str(data), Settings(str(data)), root=root)


# ---------------------------------------------------------------- checking
def test_versions():
    assert update.version_tuple("v1.10.2") == (1, 10, 2) and update.version_tuple("1.2.3") == (1, 2, 3)
    assert update.version_tuple("v1.2") is None and update.version_tuple("nightly") is None and update.version_tuple("") is None
    assert update.version_tuple("1.10.0") > update.version_tuple("1.9.9")


def test_check_finds_a_newer_release(tmp_path, monkeypatch):
    up = updater(tmp_path)

    async def fn(seen):
        info = await up.check()
        assert info["newer"]["version"] == NEXT and info["latest"] == NEXT and info["error"] is None
        assert info["newer"]["notes"] == "What's new: the lighthouse." and info["newer"]["zip"]["sha256"]
        assert seen == [f"Model Librarian Gallery/{VERSION}"]  # who's asking, and nothing else
    with_github(monkeypatch, fn)
    assert up.available() == NEXT
    again = updater(tmp_path)  # remembered across starts
    assert again.available() == NEXT and again.state["checked"] > 0


def test_check_when_this_is_the_newest(tmp_path, monkeypatch):
    up = updater(tmp_path)

    async def fn(_):
        info = await up.check()
        assert info["newer"] is None and info["latest"] == VERSION
    with_github(monkeypatch, fn, tag=f"v{VERSION}")
    assert up.available() is None


@pytest.mark.parametrize("kw,msg", [({"status": 404}, "no releases"), ({"status": 403}, "limiting"), ({"tag": "nightly"}, "no version number")])
def test_check_says_what_went_wrong(tmp_path, monkeypatch, kw, msg):
    up = updater(tmp_path)

    async def fn(_):
        with pytest.raises(update.UpdateError, match=msg):
            await up.check()
    with_github(monkeypatch, fn, **kw)
    assert msg in up.info()["error"] and up.available() is None


def test_check_when_github_cant_be_reached(tmp_path, monkeypatch):
    monkeypatch.setenv("MLG_UPDATE_API", f"http://127.0.0.1:{free_port()}")
    up = updater(tmp_path)
    with pytest.raises(update.UpdateError, match="Couldn't reach GitHub"):
        asyncio.run(up.check())


def test_daily_only_with_the_switch_on(tmp_path):
    up = updater(tmp_path)
    assert not up.due()  # off unless you turn it on
    up.settings.update({"updates": True})
    assert up.due()
    up.state["checked"] = 1000.0
    assert not up.due(now=1000.0 + 3600) and up.due(now=1000.0 + update.DAY)


# ---------------------------------------------------------------- installing
@pytest.mark.parametrize("kw", [{}, {"digest": False, "sha_file": True}])
def test_install_stages_the_update(tmp_path, monkeypatch, kw):
    root = portable(tmp_path)
    up = updater(tmp_path, root)

    async def fn(_):
        await up.check()
        info = await up.install()
        assert info["staged"] == NEXT
    with_github(monkeypatch, fn, **kw)
    staged = os.path.join(root, "update", "app")
    assert open(os.path.join(root, "update", "ready"), encoding="utf-8").read() == NEXT
    assert os.path.isfile(os.path.join(staged, "start.pyw")) and not os.path.exists(os.path.join(root, "update", "app.part"))
    assert up.available() is None  # already waiting for the next start: not told again
    assert open(os.path.join(root, "app", "mlgallery", "__init__.py"), encoding="utf-8").read().count(VERSION)  # app\ itself untouched


def test_install_refuses_a_download_that_doesnt_match(tmp_path, monkeypatch):
    root = portable(tmp_path)
    up = updater(tmp_path, root)
    good = app_zip()

    async def fn(_):
        await up.check()
        up.state["latest"]["zip"]["sha256"] = hashlib.sha256(good + b"x").hexdigest()
        with pytest.raises(update.UpdateError, match="SHA-256"):
            await up.install()
    with_github(monkeypatch, fn, blob=good)
    assert up.staged() is None and not os.path.exists(os.path.join(root, "update", "app"))


@pytest.mark.parametrize("blob,msg", [
    (lambda: app_zip(requirements="aiohttp==9.9.9\n"), "other packages"),
    (lambda: app_zip(version="9.9.9"), "not " + NEXT),
    (lambda: app_zip(extra={"../evil.txt": "x"}), "outside its own folder"),
    (lambda: b"not a zip at all", "isn't a zip"),
])
def test_stage_refuses(tmp_path, blob, msg):
    root = portable(tmp_path)
    b = blob()
    with pytest.raises(update.UpdateError, match=msg):
        update.stage(root, b, hashlib.sha256(b).hexdigest(), NEXT)
    assert not os.path.exists(os.path.join(root, "update", "app")) and not os.path.exists(os.path.join(tmp_path, "evil.txt"))
    assert not os.path.exists(os.path.join(root, "update", "app.part"))


def test_install_only_in_the_portable_folder(tmp_path, monkeypatch):
    up = updater(tmp_path, root=None)

    async def fn(_):
        await up.check()
        with pytest.raises(update.UpdateError, match="git pull"):
            await up.install()
    with_github(monkeypatch, fn)
    assert update.portable_root() is None  # this checkout isn't one


def test_release_zip_is_what_the_updater_takes(tmp_path):
    z = release.update_zip(str(tmp_path / "dist"))
    blob = open(z, "rb").read()
    names = set(zipfile.ZipFile(z).namelist())
    assert {"start.pyw", "requirements.txt", "mlgallery/__init__.py", "mlgallery/update.py", "mlgallery/ui/index.html"} <= names
    assert not any("__pycache__" in n for n in names)
    assert open(z + ".sha256", encoding="utf-8").read().split()[0] == hashlib.sha256(blob).hexdigest()
    assert release.update_zip(str(tmp_path / "again")) and open(str(tmp_path / "again" / os.path.basename(z)), "rb").read() == blob  # same bytes
    root = portable(tmp_path)
    update.stage(root, blob, hashlib.sha256(blob).hexdigest(), VERSION)
    assert os.path.isfile(os.path.join(root, "update", "app", "mlgallery", "update.py"))


# ---------------------------------------------------------------- the server
def test_update_routes(lib, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer
    from mlgallery.server import make_app
    H = {"X-MLG-Token": "t"}

    async def fn(_):
        client = TestClient(TestServer(make_app(lib, "t"), host="127.0.0.1"))
        await client.start_server()
        try:
            s = await (await client.get("/api/settings", headers=H)).json()
            assert s["settings"]["updates"] is False and s["update"]["current"] == VERSION and s["update"]["can_restart"] is False
            r = await client.post("/api/update/check", headers=H)
            assert r.status == 200 and (await r.json())["update"]["newer"]["version"] == NEXT
            assert (await (await client.get("/api/status", headers=H)).json())["update"] == NEXT
            r = await client.post("/api/update/install", headers=H)
            assert r.status == 502 and "git pull" in (await r.json())["error"]  # a checkout: no self-update
            assert (await client.post("/api/update/restart", headers=H)).status == 409
        finally:
            await client.close()
    with_github(monkeypatch, fn)


# ---------------------------------------------------------------- the launcher
@pytest.fixture(scope="module")
def launcher(tmp_path_factory):
    if os.name != "nt":
        pytest.skip("the launcher is a Windows program")
    try:
        build_portable.csc()
    except SystemExit:
        pytest.skip("no C# compiler (.NET Framework 4)")
    exe = str(tmp_path_factory.mktemp("launcher") / "Model Librarian Gallery.exe")
    build_portable.make_launcher(exe)
    return exe


def run_launcher(exe, root, data, *args):
    shutil.copy2(exe, os.path.join(root, "Model Librarian Gallery.exe"))
    r = subprocess.run([os.path.join(root, "Model Librarian Gallery.exe"), "--apply-update-only", "--data", str(data), *args],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_launcher_swaps_in_the_update(tmp_path, launcher):
    root = portable(tmp_path)
    b = app_zip()
    update.stage(root, b, hashlib.sha256(b).hexdigest(), NEXT)
    (tmp_path / "data").mkdir()
    assert run_launcher(launcher, root, tmp_path / "data") == NEXT
    assert f'VERSION = "{NEXT}"' in open(os.path.join(root, "app", "mlgallery", "__init__.py"), encoding="utf-8").read()
    assert sorted(os.listdir(os.path.join(root, "update"))) == []  # the old app, the marker and the staged copy all gone
    assert run_launcher(launcher, root, tmp_path / "data") == "nothing applied"


def test_launcher_waits_while_a_gallery_is_open(tmp_path, launcher):
    from mlgallery.__main__ import _Instance
    root = portable(tmp_path)
    b = app_zip()
    update.stage(root, b, hashlib.sha256(b).hexdigest(), NEXT)
    lock = _Instance(str(tmp_path / "data"))
    assert lock.acquire()
    try:
        assert run_launcher(launcher, root, tmp_path / "data") == "nothing applied"
        assert VERSION in open(os.path.join(root, "app", "mlgallery", "__init__.py"), encoding="utf-8").read()
    finally:
        lock.f.close()
    assert run_launcher(launcher, root, tmp_path / "data") == NEXT  # once it's closed


def test_shortcut_points_at_the_launcher(tmp_path, monkeypatch):
    from mlgallery import shortcut
    root = portable(tmp_path)
    monkeypatch.setattr(shortcut, "HERE", os.path.join(root, "app", "mlgallery"))
    prog, args, wd = shortcut.target()
    assert prog == os.path.join(root, "Model Librarian Gallery.exe") and args == "" and wd == root
