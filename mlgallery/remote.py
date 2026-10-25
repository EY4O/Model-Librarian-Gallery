"""Phones and tablets: the library on another device on your home network or your Tailscale network.

Off until you turn it on (Settings, Phone and tablet). Then the Gallery also listens on the network, on a port of its
own, for devices you've paired: Settings shows a QR code (and a code to type) good for 10 minutes and one device; the
device that opens it is remembered by a key in a cookie (only a hash of it is kept here), named, and can be removed.

Only addresses of private networks are answered (home ranges, Tailscale's 100.64.0.0/10 and its IPv6 range): never
the open internet, even if a router forwarded the port. A device can browse, rate, tag, put in albums and characters,
set the artist, delete to the Trash and put back, and Swipe; never change Settings or folders, move or rename files,
bring in an Eagle library or file downloads (those stay on the PC). Home Wi-Fi carries it unencrypted, which is fine
on your own network; Tailscale's connection is encrypted.
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
COOKIE = "mlg_device"
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
UNDO = {"marks", "tags", "albums", "credits", "characters", "trash", "restore"}


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
                     (r"Macintosh", "Mac"), (r"Windows", "Windows PC"), (r"Linux", "Linux")):
        if re.search(rx, ua):
            return name
    return "Device"


class Devices:
    """The paired devices (in this PC's settings) and the pairing code shown now."""

    def __init__(self, settings):
        self.settings = settings
        self.code: tuple[str, float] | None = None  # (code, until)

    def list(self) -> list[dict]:
        return [{k: d.get(k) for k in ("id", "name", "added", "seen")} for d in self.settings.get("remote_devices") or [] if isinstance(d, dict)]

    def new_code(self) -> str:
        code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))  # (no 0/O or 1/I to misread)
        self.code = (code, time.time() + CODE_FOR)
        return code

    def pair(self, code: str, user_agent: str) -> str | None:
        """A device that came with the code -> its key for the cookie (the code is then used up)."""
        if not self.code or time.time() > self.code[1] or not secrets.compare_digest(str(code or "").upper().replace("-", ""), self.code[0]):
            return None
        self.code = None
        did, secret = secrets.token_hex(6), secrets.token_urlsafe(24)
        devices = [d for d in self.settings.get("remote_devices") or [] if isinstance(d, dict)]
        name = device_name(user_agent)
        n = sum(1 for d in devices if str(d.get("name", "")).startswith(name))
        devices.append({"id": did, "name": f"{name} {n + 1}" if n else name, "hash": _hash(secret), "added": time.time(), "seen": time.time()})
        self.settings.update({"remote_devices": devices[-20:]})
        return f"{did}.{secret}"

    def check(self, cookie: str | None) -> dict | None:
        """The device this cookie is (still paired), or None."""
        did, _, secret = str(cookie or "").partition(".")
        if not did or not secret:
            return None
        self.settings.fresh()  # (removed in another Gallery's Settings: gone here too)
        for d in self.settings.get("remote_devices") or []:
            if isinstance(d, dict) and d.get("id") == did and secrets.compare_digest(str(d.get("hash", "")), _hash(secret)):
                if time.time() - float(d.get("seen") or 0) > 300:  # (when it was last here: written now and then, not on every request)
                    self.settings.fresh()
                    devices = [dict(x, seen=time.time()) if isinstance(x, dict) and x.get("id") == did else x for x in self.settings.get("remote_devices") or []]
                    if any(isinstance(x, dict) and x.get("id") == did for x in devices):
                        self.settings.update({"remote_devices": devices})
                return d
        return None

    def forget(self, did: str) -> bool:
        devices = [d for d in self.settings.get("remote_devices") or [] if isinstance(d, dict)]
        rest = [d for d in devices if d.get("id") != did]
        self.settings.update({"remote_devices": rest})
        return len(rest) < len(devices)

    def rename(self, did: str, name: str) -> bool:
        name = re.sub(r"\s+", " ", str(name or "")).strip()[:40]
        if not name:
            raise ValueError("A device needs a name.")
        devices = [dict(d, name=name) if isinstance(d, dict) and d.get("id") == did else d for d in self.settings.get("remote_devices") or []]
        self.settings.update({"remote_devices": devices})
        return any(isinstance(d, dict) and d.get("id") == did for d in devices)
