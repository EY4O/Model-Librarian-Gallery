"""Drive headless Edge over the DevTools protocol: the UI tests and the README screenshots.

    async with Edge(url, width, height) as page:
        await page.wait("document.querySelector('.tile')")
        await page.shot("docs/images/hero.png")
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request

EDGE_PATHS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]


def find_edge() -> str | None:
    return next((p for p in EDGE_PATHS if os.path.isfile(p)), None) or shutil.which("msedge")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Edge:
    def __init__(self, url: str, width: int = 1440, height: int = 900):
        self.url, self.width, self.height = url, width, height
        self.n = 0

    async def __aenter__(self):
        import websockets
        self.profile = tempfile.mkdtemp(prefix="mlg-edge-")
        port = free_port()
        self.proc = subprocess.Popen([find_edge(), "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={self.profile}",
                                      "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--hide-scrollbars",
                                      f"--window-size={self.width},{self.height}", "about:blank"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ws_url = None
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json") as r:
                    pages = [p for p in json.load(r) if p.get("type") == "page"]
                if pages:
                    ws_url = pages[0]["webSocketDebuggerUrl"]
                    break
            except OSError:
                pass
            await asyncio.sleep(0.1)
        if not ws_url:
            raise RuntimeError("Edge didn't start")
        self.ws = await websockets.connect(ws_url, max_size=64 * 1024 * 1024)
        await self.call("Emulation.setDeviceMetricsOverride", width=self.width, height=self.height, deviceScaleFactor=1, mobile=False)
        await self.call("Page.enable")
        await self.call("Runtime.enable")
        # page errors are collected, so a test can check there were none
        await self.call("Page.addScriptToEvaluateOnNewDocument", source=(
            "window.__errors = []; addEventListener('error', (e) => __errors.push(String(e.message)));"
            " addEventListener('unhandledrejection', (e) => __errors.push(String(e.reason)));"))
        await self.call("Page.navigate", url=self.url)
        return self

    async def __aexit__(self, *exc):
        try:
            await self.ws.close()
        finally:
            # the whole tree: Edge's own child processes would otherwise hold its profile folder open
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], capture_output=True)
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            for _ in range(20):  # (the last of them let go of their files a moment later)
                shutil.rmtree(self.profile, ignore_errors=True)
                if not os.path.exists(self.profile):
                    break
                time.sleep(0.25)

    async def call(self, method, **params):
        self.n += 1
        me = self.n
        await self.ws.send(json.dumps({"id": me, "method": method, "params": params}))
        while True:
            m = json.loads(await self.ws.recv())
            if m.get("id") == me:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})

    async def js(self, expr: str):
        if re.match(r"\s*(const|let)\b", expr):  # its own block, so the next call can declare the same names
            expr = "{" + expr + "\n}"
        r = await self.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        if r.get("exceptionDetails"):
            raise RuntimeError(str(r["exceptionDetails"])[:500])
        return r["result"].get("value")

    async def wait(self, expr: str, timeout: float = 30):
        t = time.time()
        last = None
        while time.time() - t < timeout:
            try:
                if await self.js(f"!!({expr})"):
                    return
            except RuntimeError as ex:
                last = ex
            await asyncio.sleep(0.15)
        raise TimeoutError(f"timed out waiting for {expr}" + (f" ({last})" if last else ""))

    async def key(self, key: str, **mods):
        """A keydown on the document, as the app listens for them."""
        opts = json.dumps({"key": key, "bubbles": True, **mods})
        await self.js(f"document.dispatchEvent(new KeyboardEvent('keydown', {opts})); true")

    async def click(self, selector: str):
        await self.js(f"document.querySelector({json.dumps(selector)}).click(); true")

    async def shot(self, path: str):
        r = await self.call("Page.captureScreenshot", format="png")
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "wb") as f:
            f.write(base64.b64decode(r["data"]))
