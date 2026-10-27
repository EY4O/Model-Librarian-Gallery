"""Tailscale, as Phone and tablet uses it: is it running here, this PC's Tailscale addresses and name, who a device
on the tailnet is (its node and its account), and an HTTPS certificate for this PC's name (tailscale cert).

Everything goes through the Tailscale client's own command (tailscale.exe, from where Tailscale installs it), read-only
but for the certificate. Nothing here changes the tailnet. A Tailscale that isn't installed, isn't running or doesn't
answer is "not found": never an error, and never a way in (a device whose account can't be told is refused).
"""
from __future__ import annotations

import ipaddress
import json
import os
import subprocess
import threading
import time

TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
_WHOIS_FOR = 60           # seconds who a device is stays known
_STATUS_FOR = 30          # seconds this PC's status stays known (Settings asks every few seconds while pairing)

_lock = threading.Lock()
_whois: dict[str, tuple[float, dict | None]] = {}
_asking: dict[str, threading.Lock] = {}   # one question about a device at a time (a page of pictures asks at once)
_status: tuple[float, dict | None] | None = None


def cli() -> str | None:
    """The tailscale command where Tailscale installs it, or None (not installed, or MLG_NO_TAILSCALE: the tests).
    Never one found in the current folder, or a script by that name on PATH."""
    if os.environ.get("MLG_NO_TAILSCALE"):
        return None
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432"), os.environ.get("ProgramFiles(x86)")):
        p = os.path.join(base or "", "Tailscale", "tailscale.exe")
        if base and os.path.isabs(p) and os.path.isfile(p):
            return p
    for d in os.environ.get("PATH", "").split(os.pathsep):  # (installed elsewhere: an .exe in a folder named in full)
        d = d.strip().strip('"')
        p = os.path.join(d, "tailscale.exe" if os.name == "nt" else "tailscale")
        if d and os.path.isabs(d) and os.path.isfile(p):
            return p
    return None


def _run(*args: str, timeout: float = 8) -> str | None:
    exe = cli()
    if not exe:
        return None
    try:
        r = subprocess.run([exe, *args], capture_output=True, timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return r.stdout.decode("utf-8", errors="replace") if r.returncode == 0 else None  # (names in any script: never Windows' code page)


def in_tailnet(addr: str | None) -> bool:
    """Is this a Tailscale address (100.64.0.0/10, or Tailscale's IPv6 range)?"""
    try:
        ip = ipaddress.ip_address((addr or "").split("%")[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip in (TAILNET_V4 if ip.version == 4 else TAILNET_V6)


def status(fresh: bool = False) -> dict | None:
    """This PC on the tailnet, or None when Tailscale isn't running here:
    {ips: [its Tailscale addresses, IPv4 first], name: its MagicDNS name, login: its account, uid: that account's
    number, tagged: it's a tagged device (no person's account), https: the name a certificate can be had for (None while
    the tailnet's HTTPS certificates are off)}. Known for a little while (`fresh`: asked now)."""
    global _status
    now = time.monotonic()
    with _lock:
        if not fresh and _status and now - _status[0] < _STATUS_FOR:
            return _status[1]
    out = _run("status", "--json")
    try:
        st = json.loads(out) if out else None
    except ValueError:
        st = None
    info = None
    if isinstance(st, dict) and st.get("BackendState") == "Running" and isinstance(st.get("Self"), dict):
        me = st["Self"]
        ips = sorted((ip for ip in me.get("TailscaleIPs") or [] if in_tailnet(ip)), key=lambda ip: ":" in ip)
        user = (st.get("User") or {}).get(str(me.get("UserID"))) or {}
        name = str(me.get("DNSName") or "").rstrip(".") or None
        certs = [str(d).rstrip(".") for d in st.get("CertDomains") or []]
        login = user.get("LoginName") or None
        if ips:
            info = {"ips": ips, "name": name, "login": login, "uid": me.get("UserID"),
                    "tagged": bool(me.get("Tags")) or login == "tagged-devices",
                    "https": name if name and name in certs else (certs[0] if certs else None)}
    with _lock:
        _status = (now, info)
    return info


def whois(addr: str) -> dict | None:
    """Who the device at this Tailscale address is: {login: its account, uid: that account's number, tagged: a tagged
    device, node: its name}, or None (not known)."""
    now = time.monotonic()
    with _lock:
        got = _whois.get(addr)
        if got and now - got[0] < _WHOIS_FOR:
            return got[1]
        asking = _asking.setdefault(addr, threading.Lock())
    with asking:  # (asked once, however many of its requests come at once)
        with _lock:
            got = _whois.get(addr)
            if got and time.monotonic() - got[0] < _WHOIS_FOR:
                return got[1]
        out = _run("whois", "--json", addr)
        info = None
        try:
            w = json.loads(out) if out else None
        except ValueError:
            w = None
        if isinstance(w, dict):
            node, user = w.get("Node") or {}, w.get("UserProfile") or {}
            host = (node.get("Hostinfo") or {}).get("Hostname") if isinstance(node.get("Hostinfo"), dict) else None
            name = str(node.get("ComputedName") or host or str(node.get("Name") or "").split(".")[0] or "")
            if user.get("LoginName"):
                info = {"login": str(user["LoginName"]), "uid": user.get("ID"), "node": name[:40] or None,
                        "tagged": bool(node.get("Tags")) or user.get("LoginName") == "tagged-devices"}
        with _lock:
            if len(_whois) > 200:
                _whois.clear()
                _asking.clear()
            _whois[addr] = (time.monotonic(), info)
    return info


def same_owner(me: dict | None, who: dict | None) -> bool:
    """Is this device signed in to this PC's own account? Never for a tagged device (on either side): a tag isn't a
    person, and two tagged devices would otherwise look alike. By the account's number where Tailscale gives it."""
    if not me or not who or me.get("tagged") or who.get("tagged") or not me.get("login"):
        return False
    if me.get("uid") is not None and who.get("uid") is not None:
        return str(me["uid"]) == str(who["uid"])
    return who.get("login") == me["login"]


def cert(domain: str, folder: str) -> tuple[str, str] | None:
    """This PC's certificate for its tailnet name, from Tailscale (which gets it from Let's Encrypt, keeps it and renews
    it as it nears its end), written to `folder` -> (certificate file, key file), or None if none could be had. Asked
    each time (Tailscale answers from what it keeps), so the certificate is always the one Tailscale has now."""
    os.makedirs(folder, exist_ok=True)
    crt, key = os.path.join(folder, f"{domain}.crt"), os.path.join(folder, f"{domain}.key")
    if _run("cert", "--cert-file", crt, "--key-file", key, domain, timeout=90) is None:
        good = os.path.isfile(crt) and os.path.isfile(key) and time.time() - os.path.getmtime(crt) < 60 * 86400
        return (crt, key) if good else None  # (the one from before while it's surely good: certificates last 90 days, or less)
    return crt, key
