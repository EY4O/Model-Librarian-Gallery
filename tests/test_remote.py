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
            dev = {"Cookie": f"{remote.COOKIE}={r.cookies[remote.COOKIE].value}"}
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
            dev = {"Cookie": f"{remote.COOKIE}={r.cookies[remote.COOKIE].value}"}
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
