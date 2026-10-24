"""Start the Gallery: python -m mlgallery [--data DIR] [--browser | --server] [--port N]

  (default)   its own window (Edge WebView2, part of Windows)
  --browser   in your web browser instead
  --server    the server only: prints its address (tests, or another device's browser via a tunnel of your own)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading
import webbrowser

from . import APP_NAME, VERSION
from .config import default_data_dir
from .library import Library
from .server import serve


class _Instance:
    """One Gallery per data folder: a second start would fight the first over the index."""

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "gallery.lock")
        self.f = None

    def acquire(self) -> bool:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.f = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False


def _message(text: str) -> None:
    print(text, file=sys.stderr)
    if os.name == "nt" and not sys.stderr.isatty():
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, 0x40)
        except Exception:
            pass


ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "icon.ico")


def _own_taskbar_entry() -> None:
    """Group under the Gallery's own name on the taskbar, not under Python's."""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("EY4O.ModelLibrarianGallery")
        except Exception:
            pass


def _set_window_icon(hwnd=None) -> None:
    """pywebview sets no icon on Windows (the window would show Python's), so set it here."""
    if os.name != "nt" or not os.path.exists(ICON):
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.FindWindowW.restype = ctypes.c_void_p
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_void_p]
        hwnd = hwnd or user32.FindWindowW(None, APP_NAME)
        if not hwnd:
            return
        IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x10, 0x80
        for which, size in ((0, user32.GetSystemMetrics(49)), (1, user32.GetSystemMetrics(11))):  # small, big
            h = user32.LoadImageW(None, ICON, IMAGE_ICON, size, size, LR_LOADFROMFILE)
            if h:
                user32.SendMessageW(hwnd, WM_SETICON, which, h)
    except Exception:
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mlgallery", description=f"{APP_NAME} {VERSION}")
    ap.add_argument("--data", default=default_data_dir(), help="where the index, thumbnails and settings live")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--browser", action="store_true", help="open in your web browser instead of its own window")
    mode.add_argument("--server", action="store_true", help="run the server only and print its address")
    ap.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: any free one)")
    args = ap.parse_args(argv)

    lock = _Instance(args.data)
    if not lock.acquire():
        _message(f"{APP_NAME} is already open (data folder: {args.data}).")
        return 1

    lib = Library(args.data)
    window = {"w": None}

    def pick_folder():
        w = window["w"]
        if not w:
            return None
        import webview
        kind = getattr(getattr(webview, "FileDialog", None), "FOLDER", None) or getattr(webview, "FOLDER_DIALOG")
        chosen = w.create_file_dialog(kind)
        return chosen[0] if chosen else None

    loop = asyncio.new_event_loop()
    ready = threading.Event()
    info = {}

    def run_server():
        asyncio.set_event_loop(loop)
        runner, port, token = loop.run_until_complete(serve(lib, args.port, pick_folder=pick_folder if not (args.browser or args.server) else None))
        info.update(runner=runner, port=port, token=token)
        ready.set()
        loop.run_forever()
        loop.run_until_complete(runner.cleanup())

    t = threading.Thread(target=run_server, name="server", daemon=True)
    t.start()
    ready.wait(30)
    lib.start()
    url = f"http://127.0.0.1:{info['port']}/?t={info['token']}"
    try:
        if args.server:
            print(url, flush=True)
            threading.Event().wait()
        elif args.browser:
            webbrowser.open(url)
            print(f"{APP_NAME} is open in your browser. Close this window to quit.", flush=True)
            threading.Event().wait()
        else:
            import webview
            _own_taskbar_entry()
            window["w"] = webview.create_window(APP_NAME, url, width=1440, height=920, min_size=(900, 600), background_color="#262626")

            def shown():
                try:
                    hwnd = int(window["w"].native.Handle.ToInt64())  # the WinForms window itself
                except Exception:
                    hwnd = None
                _set_window_icon(hwnd)
            window["w"].events.shown += shown
            webview.start(private_mode=False, storage_path=os.path.join(args.data, "webview"))
    except KeyboardInterrupt:
        pass
    finally:
        lib.stop()
        loop.call_soon_threadsafe(loop.stop)
        t.join(5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
