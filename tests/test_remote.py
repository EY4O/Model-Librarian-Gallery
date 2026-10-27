"""Phones and tablets: who's answered (private networks, paired devices), what a device may do, pairing, and the QR code."""
import asyncio
import json
import shutil
import subprocess
import time

import pytest

from mlgallery import remote

HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))


# ---------------------------------------------------------------- the QR code (ui/qr.js), against segno
@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_the_qr_code_is_the_standards(monkeypatch):
    segno = pytest.importorskip("segno")
    from segno import encoder
    # (segno pads to the next byte even when the data already ends on one - always, in byte mode - writing one 00 more
    # than the standard asks for: both scan the same. The reference here pads only when it's needed, as ours does.)
    monkeypatch.setattr(encoder, "write_padding_bits", lambda buff, version, length: buff.extend([0] * (-length % 8)))
    texts = ["http://192.168.1.20:8766/pair?c=ABCD2345", "http://100.101.102.103:8766/pair?c=QWERTYUP", "x",
             "http://[fd7a:115c:a1e0::1]:8766/pair?c=" + "Z" * 60, "€ and ü: bytes, not just ASCII"]
    cases = [[t, None, m] for t in texts for m in range(8)] + [[t, None, None] for t in texts] + [["a" * 180, 9, 3], ["b" * 200, 10, 6]]
    out = json.loads(subprocess.run(["node", f"{HERE}/qr_dump.mjs", json.dumps(cases)], capture_output=True, text=True, check=True).stdout)
    for (text, version, mask), got in zip(cases, out):
        q = segno.make_qr(text, error="m", mode="byte", version=version or got["version"], mask=mask if mask is not None else got["mask"], boost_error=False)
        want = ["".join(str(b) for b in row) for row in q.matrix]
        assert got["version"] == q.version and got["rows"] == want, (text, version, mask)
    # left to choose: the smallest version that holds it, and the mask that's easiest to read, as segno picks them
    for text in texts + [f"http://192.168.0.{i}:8766/pair?c=K{i:07d}" for i in range(24)]:
        got = json.loads(subprocess.run(["node", f"{HERE}/qr_dump.mjs", json.dumps([[text, None, None]])], capture_output=True, text=True, check=True).stdout)[0]
        want = segno.make_qr(text, error="m", mode="byte", boost_error=False)
        assert (got["version"], got["mask"]) == (want.version, want.mask), text


# ---------------------------------------------------------------- who's answered, and what a device may ask
def test_only_private_networks_are_answered():
    for ip in ("192.168.1.5", "10.0.0.13", "172.16.4.2", "100.89.50.9", "::ffff:192.168.1.5", "fd7a:115c:a1e0::1", "fe80::1%12"):
        assert remote.private(ip), ip
    for ip in ("8.8.8.8", "172.32.0.1", "100.128.0.1", "2001:db8::1", "", None, "junk", "::ffff:8.8.8.8"):
        assert not remote.private(ip), ip


def test_what_a_device_may_ask():
    yes = [("GET", "/api/files"), ("GET", "/api/thumb/3"), ("GET", "/api/file/3"), ("GET", "/api/file/3/info"), ("GET", "/api/folders"),
           ("GET", "/api/folders/2/dirs"), ("GET", "/api/albums"), ("POST", "/api/file/3/mark"), ("POST", "/api/file/3/tags"),
           ("POST", "/api/ops/trash"), ("POST", "/api/ops/restore"), ("POST", "/api/ops/bulk"), ("POST", "/api/ops/7/undo"), ("POST", "/api/sort")]
    no = [("GET", "/api/settings"), ("POST", "/api/settings"), ("POST", "/api/folders"), ("DELETE", "/api/folders/1"), ("POST", "/api/ops/move"),
          ("POST", "/api/ops/rename"), ("POST", "/api/ops/empty"), ("POST", "/api/remote"), ("POST", "/api/remote/code"), ("GET", "/api/remote"),
          ("POST", "/api/albums/move"), ("DELETE", "/api/albums/1"), ("POST", "/api/eagle/import"), ("POST", "/api/inbox/file"), ("GET", "/api/file/3/x"),
          ("GET", "/api/files/../settings"), ("POST", "/api/folders/1/reveal"), ("POST", "/api/library/kind")]
    assert all(remote.allowed(m, p) for m, p in yes), [x for x in yes if not remote.allowed(*x)]
    assert not any(remote.allowed(m, p) for m, p in no), [x for x in no if remote.allowed(*x)]


def test_pairing_a_device(lib, monkeypatch):
    d = remote.Devices(lib.settings)
    assert d.pair("", "") is None  # no code shown: nothing pairs
    code = d.new_code()
    assert len(code) == 8 and not set(code) & set("01IO")
    assert d.pair("WRONGONE", "") is None
    key = d.pair(f"{code[:4].lower()}-{code[4:].lower()}", "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)")
    assert key and d.pair(code, "") is None  # one device per code
    assert d.check(key)["name"] == "iPhone" and d.check(key.split(".")[0] + ".nope") is None and d.check("") is None
    assert "hash" not in d.list()[0] and key.split(".")[1] not in str(lib.settings.get("remote_devices"))  # (only a hash is kept)
    later = d.new_code()
    now = time.time()
    monkeypatch.setattr(remote.time, "time", lambda: now + remote.CODE_FOR + 1)
    assert d.pair(later, "") is None  # run out
    monkeypatch.undo()
    assert d.rename(d.list()[0]["id"], "  Sam's   phone ") and d.list()[0]["name"] == "Sam's phone"
    assert d.forget(d.list()[0]["id"]) and d.check(key) is None


# ---------------------------------------------------------------- the server, as a device on the home network sees it
def test_a_device_is_let_in_only_once_paired_and_only_so_far(lib, media, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer
    from mlgallery import server
    from test_server import H, TOKEN, indexed
    fid = indexed(lib, media)
    file_id = lib.files()["items"][0]["id"]
    peer = {"ip": "192.168.1.50"}
    monkeypatch.setattr(server, "_peer", lambda request: peer["ip"])
    COOKIE = remote.cookie_name()
    lib.settings.update({"remote_mode": "wifi", "remote_wifi_ok": True})
    app = server.make_app(lib, TOKEN)

    async def go():
        c = TestClient(TestServer(app, host="127.0.0.1"))
        await c.start_server()
        try:
            assert (await c.get("/")).status == 403  # Phone and tablet is off: nothing
            lib.settings.update({"remote": True})
            r = await c.get("/")
            assert r.status == 403 and "isn't paired" in await r.text() and "action='/pair'" in await r.text()
            assert (await c.get("/api/status", headers={"X-MLG-Token": "device"})).status == 403
            assert (await c.get("/pair?c=NOPENOPE", allow_redirects=False)).status == 403
            code = app[server.DEVICES].new_code()
            r = await c.get(f"/pair?c={code}", allow_redirects=False)
            assert r.status == 302 and r.headers["Location"] == "/"
            cookie = r.headers["Set-Cookie"]
            assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
            dev = {"Cookie": f"{COOKIE}={r.cookies[COOKIE].value}"}
            c.session.cookie_jar.clear()  # (sent by hand from here, so a request without it is one)
            page = await (await c.get("/", headers=dev)).text()
            assert 'id="p-app"' in page and TOKEN not in page  # the phone layout; the PC's token never leaves the PC
            assert (await c.get("/api/status", headers=dev)).status == 403  # the API wants its header too
            api = {**dev, "X-MLG-Token": "device"}
            assert (await c.get("/api/status", headers=api)).status == 200
            assert (await c.get("/api/files", headers=api)).status == 200
            assert (await c.get(f"/api/thumb/{file_id}", headers=dev)).status != 403  # pictures: <img> can't send the header
            assert (await c.post(f"/api/file/{file_id}/mark", json={"rating": 4}, headers=api)).status == 200
            assert lib.info(file_id)["rating"] == 4
            for method, path in (("GET", "/api/settings"), ("POST", "/api/settings"), ("POST", "/api/folders"), ("POST", "/api/remote/code"),
                                 ("POST", "/api/ops/move"), ("GET", "/api/remote"), (("DELETE", f"/api/folders/{fid}"))):
                assert (await c.request(method, path, json={}, headers=api)).status == 403, path
            assert (await c.get("/somewhere", headers=dev)).status == 404
            assert (await c.post("/api/characters/1%2Freview", json={}, headers=api)).status == 403  # (read as the router reads it)
            # undo: what a device did (or could have), yes; a rename on the PC, no
            from mlgallery import rename
            mark_op = (await (await c.post("/api/ops/bulk", json={"selection": [file_id], "action": "rate", "value": 3}, headers=api)).json())["op"]
            assert (await c.post(f"/api/ops/{mark_op}/undo", headers=api)).status == 200
            rename.apply(lib.db, [file_id], "kept_{n}")  # (on the PC)
            ren = next(o["id"] for o in (await (await c.get("/api/ops", headers=api)).json())["ops"] if o["kind"] == "rename")
            assert (await c.post(f"/api/ops/{ren}/undo", headers=api)).status == 403
            # removed in another Gallery on this PC (another library: the same settings file): out here too
            from mlgallery.config import Settings
            other = Settings(lib.home)
            other.update({"remote_devices": []})
            assert (await c.get("/api/status", headers=api)).status == 403
            app[server.DEVICES].settings.update({"remote_devices": []})
            code = app[server.DEVICES].new_code()
            r = await c.get(f"/pair?c={code}", allow_redirects=False)
            c.session.cookie_jar.clear()
            dev = {"Cookie": f"{COOKIE}={r.cookies[COOKIE].value}"}
            api = {**dev, "X-MLG-Token": "device"}
            assert (await c.get("/api/status", headers={"X-MLG-Token": "device"})).status == 403  # no cookie, no way in
            peer["ip"] = "8.8.8.8"  # the cookie, from the open internet (a forwarded port): no
            assert (await c.get("/api/status", headers=api)).status == 403
            peer["ip"] = "192.168.1.50"
            app[server.DEVICES].forget(app[server.DEVICES].list()[0]["id"])
            assert (await c.get("/api/status", headers=api)).status == 403  # removed in Settings: out

            peer["ip"] = "127.0.0.1"  # this PC: Phone and tablet's settings, with the token as ever
            assert (await c.get("/api/remote")).status == 403
            v = await (await c.get("/api/remote", headers=H)).json()
            assert v["on"] is True and v["running"] is False and v["devices"] == []
            assert (await c.post("/api/remote", json={"port": 80}, headers=H)).status == 422
            assert (await c.post("/api/remote/code", headers=H)).status == 409  # (not listening: no code to show)
            assert (await c.get("/pair?c=x")).status == 404
            s = await (await c.get("/api/settings", headers=H)).json()
            assert "remote_devices" not in s["settings"]
        finally:
            await c.close()
    asyncio.run(go())


def test_pairing_details_from_the_qa_pass(lib):
    d = remote.Devices(lib.settings)
    ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)"
    keys = []
    for _ in range(3):
        code = d.new_code()
        keys.append(d.pair(f" {code[:4]} {code[4:].lower()} ", ua))  # (spaces, a space after: typed as people type)
    assert all(keys)
    assert [x["name"] for x in d.list()] == ["iPhone", "iPhone 2", "iPhone 3"]
    d.forget(d.list()[1]["id"])
    code = d.new_code()
    d.pair(code, ua)
    assert sorted(x["name"] for x in d.list()) == ["iPhone", "iPhone 2", "iPhone 3"]  # (the lowest number free, never two alike)
    code = d.new_code()
    d.cancel()
    assert d.pair(code, ua) is None  # (the dialog closed: its code is gone)
    assert remote.device_name("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari") == "iPad or Mac"
    # another Gallery on this PC removes one meanwhile: the "last here" written now doesn't bring it back
    from mlgallery.config import Settings
    gone = d.list()[0]["id"]
    Settings(lib.home).change("remote_devices", lambda devices: [x for x in devices if x["id"] != gone])
    d.check(keys[0])
    assert gone not in [x["id"] for x in Settings(lib.home).get("remote_devices")]


# ---------------------------------------------------------------- keys that change, a stolen one caught, devices forgotten, code tries
def test_keys_change_and_a_copied_one_unpairs_the_device(lib, monkeypatch):
    d = remote.Devices(lib.settings)
    code = d.new_code()
    key = d.pair(code, "Mozilla/5.0 (iPhone)")
    now = [time.time()]
    monkeypatch.setattr(remote.time, "time", lambda: now[0])
    assert d.visit(key, rotate=True).get("key") is None  # (just paired: no new key yet)
    now[0] += remote.ROTATE_EVERY + 1
    got = d.visit(key, rotate=True)
    new = got["key"]
    assert new and new != key and new.split(".")[0] == key.split(".")[0]
    assert d.visit(key)["device"]  # the key before, a moment later (its pictures were asked for with it): still in
    assert d.visit(new, rotate=True).get("key") is None and d.visit(new)["device"]
    now[0] += remote.GRACE + 1
    copied = d.visit(key)  # the old key again, after the device moved on: a copy
    assert copied["device"] is None and copied["stolen"]["name"] == "iPhone"
    assert d.list() == [] and d.visit(new)["device"] is None  # unpaired: the real one pairs again


def test_devices_not_seen_for_30_days_are_forgotten(lib, monkeypatch):
    d = remote.Devices(lib.settings)
    key = d.pair(d.new_code(), "")
    later = time.time() + remote.FORGET_AFTER + 60
    monkeypatch.setattr(remote.time, "time", lambda: later)
    assert d.list() == [] and d.check(key) is None and lib.settings.get("remote_devices") == []


def test_wrong_codes_lock_out_the_device_guessing_and_many_cancel_the_code(lib):
    d = remote.Devices(lib.settings)
    code = d.new_code()
    for _ in range(remote.CODE_TRIES):
        assert d.pair("WRONGONE", "", peer="192.168.1.66") is None
    assert d.pair(code, "", peer="192.168.1.66") is None  # (that device can't try any more, even the right code)
    assert d.code and d.stopped is None  # ... but the code still pairs yours
    assert d.pair("", "", peer="192.168.1.70") is None and not d.tries.get("192.168.1.70")  # (an empty code isn't a try)
    assert d.pair(code, "", peer="192.168.1.50")
    code = d.new_code()
    for i in range(remote.CODE_TRIES_ALL):  # guessed at from all sides: the code is cancelled
        d.pair("WRONGONE", "", peer=f"192.168.2.{i}")
    assert d.code is None and "wrong codes" in d.stopped and d.pair(code, "", peer="192.168.1.50") is None


def test_a_lost_answer_never_locks_the_device_out(lib, monkeypatch):
    d = remote.Devices(lib.settings)
    key = d.pair(d.new_code(), "")
    now = [time.time() + remote.ROTATE_EVERY + 1]
    monkeypatch.setattr(remote.time, "time", lambda: now[0])
    lost = d.visit(key, rotate=True)["key"]  # offered; the answer never reaches the phone
    assert lost
    now[0] += 3600  # an hour later, still the key it had: in, and a new key offered again
    again = d.visit(key, rotate=True)
    assert again["device"] and again["key"] and again["key"] != lost
    assert d.visit(again["key"])["device"]  # back with it: its key now
    now[0] += remote.GRACE + 1
    assert d.visit(key).get("stolen")  # the one before, after that: a copy


def test_tailscale_is_found_only_where_it_installs(tmp_path, monkeypatch):
    from mlgallery import tailnet
    monkeypatch.delenv("MLG_NO_TAILSCALE")
    planted = tmp_path / "here"
    planted.mkdir()
    (planted / "tailscale.bat").write_text("@echo off\r\necho planted")
    (planted / "tailscale.exe").write_text("not really")
    monkeypatch.chdir(planted)
    monkeypatch.setenv("PATH", f".{__import__('os').pathsep}{planted.name}")  # (relative entries: never)
    for k in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        monkeypatch.setenv(k, str(tmp_path / "none"))
    assert tailnet.cli() is None
    real = tmp_path / "pf" / "Tailscale"
    real.mkdir(parents=True)
    (real / "tailscale.exe").write_text("x")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    assert tailnet.cli() == str(real / "tailscale.exe")


def test_tailscale_answers_in_any_script_and_tags_are_no_ones_account(monkeypatch):
    from mlgallery import tailnet
    out = json.dumps({"BackendState": "Running", "Self": {"UserID": 7, "DNSName": "pc.tail.ts.net.", "TailscaleIPs": ["100.64.0.7"]},
                      "User": {"7": {"LoginName": "me@example.com", "DisplayName": "Ханна Łucja"}}, "Peer": {"x": {"HostName": "Áにほん"}}})

    class Done:
        returncode, stdout = 0, out.encode("utf-8")
    monkeypatch.setattr(tailnet, "cli", lambda: "tailscale.exe")
    monkeypatch.setattr(tailnet.subprocess, "run", lambda *a, **k: Done())
    st = tailnet.status(fresh=True)
    assert st and st["login"] == "me@example.com" and st["uid"] == 7 and not st["tagged"]  # (Cyrillic, Polish, Japanese: read)
    me = {"login": "me@example.com", "uid": 7, "tagged": False}
    assert tailnet.same_owner(me, {"login": "me@example.com", "uid": 7, "tagged": False})
    assert not tailnet.same_owner(me, {"login": "me@example.com", "uid": 8, "tagged": False})  # (another account by the same name)
    assert not tailnet.same_owner(me, {"login": "me@example.com", "uid": 7, "tagged": True})
    tagged = {"login": "tagged-devices", "uid": 1, "tagged": True}
    assert not tailnet.same_owner(tagged, tagged)  # (two tagged devices look alike: never)


# ---------------------------------------------------------------- Over Tailscale: only this PC's own account, on Tailscale's addresses
def test_over_tailscale_only_this_accounts_devices(lib, media, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer
    from mlgallery import server, tailnet
    from test_server import H, TOKEN, indexed
    indexed(lib, media)
    peer = {"ip": "100.101.1.2"}
    monkeypatch.setattr(server, "_peer", lambda request: peer["ip"])
    owners = {"100.101.1.2": {"login": "me@example.com", "node": "pixel-9"}, "100.101.1.3": {"login": "someone@example.com", "node": "their-phone"}}
    monkeypatch.setattr(tailnet, "whois", lambda ip: owners.get(ip))
    monkeypatch.setattr(tailnet, "status", lambda fresh=False: {"ips": ["100.64.0.7"], "name": "pc.example.ts.net", "login": "me@example.com", "https": "pc.example.ts.net"})
    lib.settings.update({"remote": True, "remote_mode": "tailscale"})
    app = server.make_app(lib, TOKEN)
    app[server.REMOTE].update(mode="tailscale", ts=tailnet.status())
    COOKIE = remote.cookie_name()

    async def go():
        c = TestClient(TestServer(app, host="127.0.0.1"))
        await c.start_server()
        try:
            code = app[server.DEVICES].new_code()
            peer["ip"] = "100.101.1.3"  # another account on the tailnet, with the code: no
            r = await c.get(f"/pair?c={code}", allow_redirects=False)
            assert r.status == 403 and "Not this Tailscale account" in await r.text()
            peer["ip"] = "100.101.1.9"  # Tailscale doesn't know it: no
            assert (await c.get("/", allow_redirects=False)).status == 403
            peer["ip"] = "192.168.1.50"  # the home Wi-Fi, while it's Tailscale: no
            assert (await c.get("/", allow_redirects=False)).status == 403
            peer["ip"] = "100.101.1.2"  # mine: pairs, named as Tailscale names it
            r = await c.get(f"/pair?c={code}", allow_redirects=False)
            assert r.status == 302
            assert app[server.DEVICES].list()[0]["name"] == "pixel-9" and app[server.DEVICES].list()[0]["via"] == "tailscale"
            dev = {"Cookie": f"{COOKIE}={r.cookies[COOKIE].value}", "X-MLG-Token": "device"}
            c.session.cookie_jar.clear()
            assert (await c.get("/api/status", headers=dev)).status == 200
            owners["100.101.1.2"] = {"login": "someone@example.com", "node": "pixel-9"}  # signed in as someone else since: no
            tailnet_whois_changed = await c.get("/api/status", headers=dev)
            assert tailnet_whois_changed.status == 403
            app[server.REMOTE]["hosts"] = {"pc.example.ts.net"}  # (as listening sets it)
            owners["100.101.1.2"] = {"login": "me@example.com", "node": "pixel-9"}
            assert (await c.get("/api/status", headers=dev)).status == 421  # this test server's own address isn't one of them
            assert (await c.get("/api/status", headers={**dev, "Host": "pc.example.ts.net:8766"})).status == 200
            assert (await c.get("/api/status", headers={**dev, "Host": "evil.example:8766"})).status == 421
            app[server.REMOTE]["hosts"] = set()
            peer["ip"] = "127.0.0.1"
            v = await (await c.get("/api/remote", headers=H)).json()
            assert v["mode"] == "tailscale" and v["tailscale"]["found"] and v["tailscale"]["https_possible"]
            # Phone and tablet's settings never through the general settings
            await c.post("/api/settings", json={"remote_mode": "wifi", "remote_wifi_ok": True, "remote_devices": []}, headers=H)
            assert lib.settings.get("remote_mode") == "tailscale" and not lib.settings.get("remote_wifi_ok") and lib.settings.get("remote_devices")
            # Home Wi-Fi only once agreed to
            assert (await c.post("/api/remote", json={"mode": "wifi"}, headers=H)).status == 409
        finally:
            await c.close()
    asyncio.run(go())


def test_the_way_is_tailscale_when_it_runs_here_and_wifi_waits_for_a_yes(lib, monkeypatch):
    from aiohttp import web
    from mlgallery import server, tailnet
    from test_server import TOKEN
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setenv("MLG_REMOTE_HOST", "127.0.0.1")
    lib.settings.update({"remote": True, "remote_port": port})
    app = server.make_app(lib, TOKEN)

    async def go():
        runner = web.AppRunner(app)
        await runner.setup()
        app[server.REMOTE]["runner"] = runner
        try:
            monkeypatch.setattr(tailnet, "status", lambda fresh=False: {"ips": ["100.64.0.7"], "name": "pc.example.ts.net", "login": "me@example.com", "https": None})
            assert await server.remote_start(app) is None
            assert app[server.REMOTE]["mode"] == "tailscale" and lib.settings.get("remote_mode") == ""  # (not saved: asked again next time)
            assert app[server.REMOTE]["hosts"] == {"127.0.0.1", "pc.example.ts.net"}
            await server.remote_stop(app)
            lib.settings.update({"remote_mode": ""})
            monkeypatch.setattr(tailnet, "status", lambda fresh=False: None)  # no Tailscale here: Home Wi-Fi, which waits for a yes
            assert await server.remote_switch(app, True) == remote.NEED_WIFI_OK
            assert lib.settings.get("remote") is True and not app[server.REMOTE]["sites"]  # (on, waiting: Settings asks)
            lib.settings.update({"remote_wifi_ok": True})
            assert await server.remote_start(app) is None and app[server.REMOTE]["mode"] == "wifi"
            await server.remote_stop(app)
            lib.settings.update({"remote_mode": "tailscale"})  # chosen, but Tailscale isn't running: said so
            assert "Tailscale isn't running" in await server.remote_start(app)
        finally:
            await server.remote_stop(app)
            await runner.cleanup()
    asyncio.run(go())


def test_dismiss_only_dismisses_and_upgraders_are_told_once(lib, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer
    from mlgallery import server, tailnet
    from test_server import H, TOKEN
    monkeypatch.setattr(tailnet, "status", lambda fresh=False: None)
    lib.settings.update({"remote": True, "remote_mode": "tailscale", "remote_alerts": [{"at": 1, "text": "x"}]})
    app = server.make_app(lib, TOKEN)

    async def go():
        c = TestClient(TestServer(app, host="127.0.0.1"))
        await c.start_server()
        try:
            r = await c.post("/api/remote", json={"dismiss": True}, headers=H)  # on but not listening (no Tailscale): still on
            assert r.status == 200 and (await r.json())["alerts"] == [] and lib.settings.get("remote") is True
        finally:
            await c.close()
    asyncio.run(go())
    # paired on the Wi-Fi before there was a choice, Tailscale chosen by itself now: told, once, and the way kept
    lib.settings.update({"remote_mode": "", "remote_alerts": [], "remote_devices": [{"id": "a", "name": "iPhone", "hash": "h", "seen": time.time()}]})
    server._tell_upgraders(lib)
    server._tell_upgraders(lib)
    assert len(lib.settings.get("remote_alerts")) == 1 and lib.settings.get("remote_mode") == "tailscale"


def test_the_phone_app_installs_and_keeps_only_its_own_files(lib, media, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer
    from mlgallery import VERSION, server
    from test_server import TOKEN, indexed
    indexed(lib, media)
    monkeypatch.setattr(server, "_peer", lambda request: "192.168.1.50")
    lib.settings.update({"remote": True, "remote_mode": "wifi", "remote_wifi_ok": True})
    app = server.make_app(lib, TOKEN)

    async def go():
        c = TestClient(TestServer(app, host="127.0.0.1"))
        await c.start_server()
        try:
            assert (await c.get("/sw.js")).status == 403  # not paired: nothing, not even the app
            r = await c.get(f"/pair?c={app[server.DEVICES].new_code()}", allow_redirects=False)
            dev = {"Cookie": f"{remote.cookie_name()}={r.cookies[remote.cookie_name()].value}"}
            c.session.cookie_jar.clear()
            sw = await c.get("/sw.js", headers=dev)
            text = await sw.text()
            assert sw.status == 200 and sw.headers["Service-Worker-Allowed"] == "/" and "javascript" in sw.headers["Content-Type"]
            assert f"mlg-{VERSION}" in text and '"/ui/phone.js"' in text and 'url.pathname.startsWith("/api/")' in text  # the library never kept
            man = await (await c.get("/ui/manifest.json", headers=dev)).json()
            assert {i["sizes"] for i in man["icons"]} == {"192x192", "512x512"} and any(i["purpose"] == "maskable" for i in man["icons"])
            assert [s["url"] for s in man["shortcuts"]] == ["/?go=swipe", "/?go=fav", "/?go=recent"] and man["id"] == "/"
            for icon in man["icons"]:
                assert (await c.get(icon["src"], headers=dev)).status == 200
            page = await (await c.get("/", headers=dev)).text()
            assert "apple-touch-icon.png" in page and 'rel="manifest"' in page
        finally:
            await c.close()
    asyncio.run(go())
