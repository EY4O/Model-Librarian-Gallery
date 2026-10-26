"""The tray: keeping running with the window closed, starting there at sign-in, waking the Gallery that's in it,
pausing the folder watcher. Nothing here touches this PC's real sign-in list (the registry is a stand-in), and the
tray window that's made never shows an icon."""
import asyncio
import os
import sys
import threading
import time
import types

import pytest

from mlgallery import tray

TOKEN = "test-token"
H = {"X-MLG-Token": TOKEN}


class FakeReg:
    """winreg, as far as tray.py uses it: one key's values, in memory."""
    HKEY_CURRENT_USER, REG_SZ = "HKCU", 1

    def __init__(self):
        self.values = {}

    def CreateKey(self, root, path):
        return self._key()

    OpenKey = CreateKey

    def _key(self):
        outer = self

        class K:
            def __enter__(self): return self
            def __exit__(self, *a): return False
        outer.path = None
        return K()

    def SetValueEx(self, k, name, _, kind, value):
        self.values[name] = value

    def DeleteValue(self, k, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]

    def QueryValueEx(self, k, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ


@pytest.fixture
def reg(monkeypatch):
    fake = FakeReg()
    monkeypatch.setitem(sys.modules, "winreg", fake)
    return fake


def test_starting_at_sign_in(reg, monkeypatch, tmp_path):
    from mlgallery import update
    cmd = tray.login_command()  # a checkout: this Python, the checkout's mlgallery, in the tray
    assert "--tray" in cmd and "mlgallery.__main__" in cmd
    pkg = tmp_path / "Model Librarian Gallery"
    monkeypatch.setattr(update, "portable_root", lambda: str(pkg))
    assert tray.login_command() == f'"{pkg / "Model Librarian Gallery.exe"}" --tray'  # the portable folder's launcher
    tray.set_login(True)
    assert reg.values[tray.RUN_NAME].endswith("--tray") and tray.at_login()
    tray.set_login(False)
    tray.set_login(False)  # (already gone: fine)
    assert tray.RUN_NAME not in reg.values and not tray.at_login()


def test_the_settings(lib, reg, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer
    from mlgallery import server
    shown = []
    icon = types.SimpleNamespace(show=shown.append)

    async def go(app_tray):
        c = TestClient(TestServer(server.make_app(lib, TOKEN, tray=app_tray), host="127.0.0.1"))
        await c.start_server()
        try:
            return c, await fn(c)
        finally:
            await c.close()

    async def fn(c):
        s = await (await c.get("/api/settings", headers=H)).json()
        out = [s["tray_ok"], s["settings"]["tray"], s["settings"]["tray_login"]]
        s = await (await c.post("/api/settings", json={"tray_login": True}, headers=H)).json()  # sign-in: in the tray too
        out += [s["settings"]["tray"], s["settings"]["tray_login"], tray.RUN_NAME in reg.values]
        s = await (await c.post("/api/settings", json={"tray": False}, headers=H)).json()  # no tray: nothing to start at sign-in
        out += [s["settings"]["tray_login"], tray.RUN_NAME in reg.values]
        return out

    _, got = asyncio.run(go(icon))
    assert got == [True, False, False, True, True, True, False, False] and shown == [True, False]

    async def no_window(c):  # the server alone (or the browser): no tray to start in
        r = await c.post("/api/settings", json={"tray_login": True}, headers=H)
        return r.status, (await (await c.get("/api/settings", headers=H)).json())["tray_ok"]
    fn = no_window
    _, got = asyncio.run(go(None))
    assert got == (409, False) and tray.RUN_NAME not in reg.values


def test_pausing_the_watcher(lib, media, monkeypatch):
    from conftest import image
    from test_photos import folder
    f = folder(lib, media)
    looked = []
    monkeypatch.setattr(lib, "scan_all", lambda: looked.append(1))
    lib.pause_watching(True)
    lib.watcher.touched(f, str(image(media / "new.png")))
    assert lib.watcher.paused and not lib.watcher._pending  # (nothing noted while paused)
    lib.pause_watching(False)
    assert not lib.watcher.paused and looked == [1]  # started again: every folder looked through, for what came meanwhile
    lib.pause_watching(False)
    assert looked == [1]
    lib.watcher.touched(f, str(media / "new.png"))
    assert lib.watcher._pending


def test_pausing_and_phones_from_the_api(lib):
    from aiohttp.test_utils import TestClient, TestServer
    from mlgallery import server
    lib.scan_all = lambda: None

    async def go():
        app = server.make_app(lib, TOKEN)
        c = TestClient(TestServer(app, host="127.0.0.1"))
        await c.start_server()
        try:
            r = await (await c.post("/api/watching", json={"paused": True}, headers=H)).json()
            st = await (await c.get("/api/status", headers=H)).json()
            await c.post("/api/watching", json={"paused": False}, headers=H)
            st2 = await (await c.get("/api/status", headers=H)).json()
            on = await server.remote_switch(app, True)  # (the tray's Phone and tablet: no runner here, so not listening)
            was = lib.settings.get("remote")
            await server.remote_switch(app, False)
            return r["paused"], st["paused"], st2["paused"], on, was, lib.settings.get("remote")
        finally:
            await c.close()
    assert asyncio.run(go()) == (True, True, False, None, True, False)


@pytest.mark.skipif(os.name != "nt", reason="Windows")
def test_a_second_start_opens_the_one_in_the_tray(tmp_path):
    opened = threading.Event()
    t = tray.Tray(str(tmp_path / "a"), "no-icon-here.ico", lambda: [], opened.set)
    assert t.start() and t.hwnd and not t.shown  # (its window, but no icon: show() wasn't asked)
    assert tray.class_name(str(tmp_path / "a")) != tray.class_name(str(tmp_path / "b"))
    assert not tray.wake(str(tmp_path / "b"))  # another library's Gallery: not this one
    assert tray.wake(str(tmp_path / "a")) and opened.wait(5)
    t.stop()
    deadline = time.time() + 5
    while tray.wake(str(tmp_path / "a")) and time.time() < deadline:
        time.sleep(0.05)
    assert not tray.wake(str(tmp_path / "a"))  # gone with it
