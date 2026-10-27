"""Phones and tablets: the library on another device, over Tailscale or on your home Wi-Fi (one or the other).

Off until you turn it on (Settings, Phone and tablet). Then the Gallery also listens, on a port of its own and only on
the one network chosen, for devices you've paired: Settings shows a QR code (and a code to type) good for 10 minutes,
one device and 5 wrong tries; the device that opens it is remembered by a key in a cookie (only a hash of it is kept
here), named, and can be removed. A device not seen for 30 days is forgotten.

Over Tailscale (the way when Tailscale runs here): only Tailscale's addresses are listened on, only devices signed in
to this PC's own Tailscale account are answered (tailnet.whois), and with the tailnet's HTTPS certificates on (and
your yes) it's HTTPS with a real certificate for this PC's tailnet name. On home Wi-Fi: only the home network's address,
unencrypted (Settings says so, and asks the first time). Either way, never the open internet.

A device's key changes now and then as it opens the library: a new one is offered, and once the device comes back with
it, the one before is worth nothing. If one of those old keys turns up after that - a copy someone kept - the device
is unpaired at once and you're told. (Someone watching an unencrypted Wi-Fi all along sees each new key too: that's
what Tailscale is for.)

A device can browse, rate, tag, put in albums and characters, set the artist, delete to the Trash and put back, and
Swipe; never change Settings or folders, move or rename files, bring in an Eagle library or file downloads.
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
import secrets
import socket
import time

PORT = 8766
CODE_FOR = 600          # seconds a pairing code stays good
CODE_TRIES = 5          # wrong codes from one device before it can't try the code shown any more
CODE_TRIES_ALL = 50     # ... and from all of them together, before the code is cancelled
FORGET_AFTER = 30 * 86400  # a device not seen for this long is forgotten (it pairs again)
ROTATE_EVERY = 600      # a device's key changes when it opens the library, at most this often
OFFER_AGAIN = 30        # a new key offered and not come back with in this long: offered afresh (the answer was lost)
GRACE = 60              # the key before still works this long after the new one came back (pictures asked for with it, on their way)
OLD_KEYS = 5            # the hashes of the keys a device had before, to know one that turns up again
NEED_WIFI_OK = "wifi-unconfirmed"  # (why it didn't start: Home Wi-Fi hasn't been agreed to yet)


def cookie_name(https: bool = False) -> str:
    """The device cookie's name. One name whatever the port, so a port changed in Settings keeps paired devices (a
    browser sends a cookie to every port of an address anyway: the Host check and the key changing are what guard it).
    Over HTTPS it's a __Host- cookie, which nothing served without HTTPS can set or overwrite."""
    return "__Host-mlg_device" if https else "mlg_device"
PRIVATE = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "169.254.0.0/16",
                                              "fc00::/7", "fe80::/10")]

# what a paired device may ask (method, path): reading, and your marks; nothing that changes Settings, folders or files' places
ALLOWED = [(m, re.compile(p)) for m, p in [
    ("GET", r"/api/(status|files|days|shapes|suggest|albums|folders|artists|characters|characters/review|sort|ops|tags)"),
    ("GET", r"/api/(thumb|file)/\d+"),
    ("GET", r"/api/file/\d+/info"),
    ("GET", r"/api/(artists|characters)/\d+"),
    ("GET", r"/api/folders/\d+/dirs"),
    ("POST", r"/api/file/\d+/(mark|tags|note|credit|characters|viewed)"),
    ("POST", r"/api/ops/(trash|restore|bulk)"),
    ("POST", r"/api/ops/\d+/undo"),
    ("POST", r"/api/(select|sort|sort/undo|sort/reset)"),
    ("POST", r"/api/characters/\d+/review"),
]]


# the pictures themselves, which <img> and <video> fetch without the API's header (the cookie still has to come with them)
MEDIA = re.compile(r"/api/(thumb|file)/\d+")


# the History steps a device may undo: the kinds of change it can make itself (never a move, a rename, an edit, Eagle...)
UNDO = {"marks", "tags", "albums", "credits", "characters", "trash", "restore", "review"}


def allowed(method: str, path: str) -> bool:
    return any(m == method and rx.fullmatch(path) for m, rx in ALLOWED)


def private(addr: str | None) -> bool:
    """Is this a device on a private network (home, Tailscale)?"""
    try:
        ip = ipaddress.ip_address((addr or "").split("%")[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in n for n in PRIVATE if n.version == ip.version)


def addresses() -> list[dict]:
    """This PC's addresses a phone can reach it on: {ip, kind}. First the one Windows reaches the internet through (your
    Wi-Fi or Ethernet: "home"), then Tailscale's, then any other private one ("other": often a virtual adapter, WSL's say)."""
    found, main = set(), None
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    try:  # the address used to reach the internet, even if the name above doesn't list it
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # (nothing is sent: a UDP "connect" only picks the way out)
            main = s.getsockname()[0]
            found.add(main)
    except OSError:
        pass
    out = []
    for ip in found:
        if not private(ip) or ip.startswith("169.254."):
            continue
        kind = "tailscale" if ipaddress.ip_address(ip) in PRIVATE[3] else "home" if ip == main else "other"
        out.append({"ip": ip, "kind": kind})
    return sorted(out, key=lambda a: (["home", "tailscale", "other"].index(a["kind"]), a["ip"]))


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def device_name(user_agent: str) -> str:
    ua = user_agent or ""
    for rx, name in ((r"iPad", "iPad"), (r"iPhone", "iPhone"), (r"Android.*Mobile", "Android phone"), (r"Android", "Android tablet"),
                     (r"Macintosh", "iPad or Mac"), (r"Windows", "Windows PC"), (r"Linux", "Linux")):  # (an iPad's Safari says it's a Mac)
        if re.search(rx, ua):
            return name
    return "Device"


class Devices:
    """The paired devices (in this PC's settings) and the pairing code shown now."""

    def __init__(self, settings):
        self.settings = settings
        self.code: tuple[str, float] | None = None  # (code, until)
        self.tries: dict[str, int] = {}  # wrong codes typed for the code shown, by the address they came from
        self.stopped = None     # why the code shown was cancelled (too many wrong tries), for the dialog to say

    def _devices(self) -> list[dict]:
        """The paired devices, less those not seen for FORGET_AFTER (forgotten as they're found)."""
        now = time.time()
        devices = [d for d in self.settings.get("remote_devices") or [] if isinstance(d, dict)]
        if any(now - float(d.get("seen") or 0) > FORGET_AFTER for d in devices):
            self.settings.change("remote_devices", lambda ds: [d for d in ds or [] if isinstance(d, dict) and now - float(d.get("seen") or 0) <= FORGET_AFTER])
            devices = [d for d in devices if now - float(d.get("seen") or 0) <= FORGET_AFTER]
        return devices

    def list(self) -> list[dict]:
        return [{k: d.get(k) for k in ("id", "name", "added", "seen", "via")} for d in self._devices()]

    def new_code(self) -> str:
        code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))  # (no 0/O or 1/I to misread)
        self.code = (code, time.time() + CODE_FOR)
        self.tries, self.stopped = {}, None
        return code

    def pair(self, code: str, user_agent: str, via: str = "wifi", node: str | None = None, peer: str = "") -> str | None:
        """A device that came with the code -> its key for the cookie (the code is then used up). `via`: how it came
        (tailscale, wifi); `node`: its Tailscale name, which names it; `peer`: its address. CODE_TRIES wrong codes from
        one address and it can't try this code any more (one device guessing doesn't spoil pairing for yours); a great
        many from all of them, and the code is cancelled."""
        typed = re.sub(r"[\s-]", "", str(code or "")).upper()  # (ABCD-2345, abcd 2345, with a space after it...)
        if not self.code or time.time() > self.code[1] or not typed:
            return None
        if self.tries.get(peer, 0) >= CODE_TRIES:
            return None
        if not secrets.compare_digest(typed, self.code[0]):
            self.tries[peer] = self.tries.get(peer, 0) + 1
            if sum(self.tries.values()) >= CODE_TRIES_ALL:  # (guessed at from all sides: this code is done)
                self.code, self.stopped = None, "So many wrong codes were typed that this code was cancelled."
            return None
        self.code = None
        did, secret = secrets.token_hex(6), secrets.token_urlsafe(24)
        kind = (node or "").strip()[:40] or device_name(user_agent)

        def add(devices):
            devices = [d for d in devices or [] if isinstance(d, dict)]
            names = {str(d.get("name", "")) for d in devices}
            name = kind if kind not in names else next(f"{kind} {n}" for n in range(2, 1000) if f"{kind} {n}" not in names)  # (the lowest number free)
            now = time.time()
            return (devices + [{"id": did, "name": name, "hash": _hash(secret), "next": None, "offered": 0, "old": [], "rotated": now,
                                 "via": via, "added": now, "seen": now}])[-20:]
        self.settings.change("remote_devices", add)
        return f"{did}.{secret}"

    def cancel(self) -> None:
        """The pairing dialog closed: its code doesn't pair anything any more."""
        self.code = None

    def check(self, cookie: str | None) -> dict | None:
        """The device this cookie is (still paired), or None."""
        return self.visit(cookie)["device"]

    def visit(self, cookie: str | None, rotate: bool = False) -> dict:
        """A device's request -> {device: it (or None), key: a new key offered when `rotate` (to set in its cookie),
        stolen: the device unpaired just now because an old key of it turned up}.

        In two steps, so a lost answer never locks a device out: a new key is offered while the one it has stays good;
        only when the device comes back with the new one does that become its key, and the one before an old key. An
        old key used after that is a copy: the device is unpaired."""
        did, _, secret = str(cookie or "").partition(".")
        if not did or not secret:
            return {"device": None}
        self.settings.fresh()  # (removed in another Gallery's Settings: gone here too)
        h, now = _hash(secret), time.time()
        mine = lambda fn: self.settings.change("remote_devices", lambda ds: [fn(x) if isinstance(x, dict) and x.get("id") == did else x  # noqa: E731
                                                                             for x in ds or []])  # (one removed meanwhile stays removed)
        for d in self._devices():
            if d.get("id") != did:
                continue
            if d.get("next") and secrets.compare_digest(str(d["next"]), h):  # back with the key offered: it's the key now
                upd = {"hash": h, "next": None, "old": ([str(d.get("hash", ""))] + list(d.get("old") or []))[:OLD_KEYS], "rotated": now, "seen": now}
                mine(lambda x: dict(x, **upd))
                return {"device": dict(d, **upd)}
            if secrets.compare_digest(str(d.get("hash", "")), h):
                due = now - float(d.get("rotated") or 0) > ROTATE_EVERY and (not d.get("next") or now - float(d.get("offered") or 0) > OFFER_AGAIN)
                if rotate and due:  # a new key offered (again, if the last offer's answer was lost); this one still good meanwhile
                    new = secrets.token_urlsafe(24)
                    upd = {"next": _hash(new), "offered": now, "seen": now}
                    mine(lambda x: dict(x, **upd))
                    return {"device": dict(d, **upd), "key": f"{did}.{new}"}
                if now - float(d.get("seen") or 0) > 300:  # (when it was last here: written now and then, not on every request)
                    mine(lambda x: dict(x, seen=now))
                return {"device": d}
            old = [str(o) for o in d.get("old") or []]
            if old and secrets.compare_digest(old[0], h) and now - float(d.get("rotated") or 0) < GRACE:
                return {"device": d}  # (asked with the key before just as the new one came back)
            if any(secrets.compare_digest(o, h) for o in old):  # a key it had before, after it moved on: a copy
                self.forget(did)
                return {"device": None, "stolen": d}
            return {"device": None}
        return {"device": None}

    def forget(self, did: str) -> bool:
        had = []
        self.settings.change("remote_devices", lambda devices: [d for d in devices or [] if isinstance(d, dict) and not (d.get("id") == did and not had.append(d))])
        return bool(had)

    def rename(self, did: str, name: str) -> bool:
        name = re.sub(r"\s+", " ", str(name or "")).strip()[:40]
        if not name:
            raise ValueError("A device needs a name.")
        got = self.settings.change("remote_devices", lambda devices: [dict(d, name=name) if isinstance(d, dict) and d.get("id") == did else d for d in devices or []])
        return any(isinstance(d, dict) and d.get("id") == did for d in got or [])
