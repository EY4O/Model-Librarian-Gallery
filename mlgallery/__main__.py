"""Start the Gallery: python -m mlgallery [--library DIR | --local | --data DIR] [--browser | --server | --tray] [--port N]

  (default)   its own window (Edge WebView2, part of Windows), with the library opened last
  --tray      in the tray, its window hidden until you open it (how it starts at sign-in: tray.py)
  --library   open this library (a folder with a .mlgallery folder in it; see libraries.py)
  --local     open this PC's library (the index in %LOCALAPPDATA%)
  --data      a data folder of its own, settings included (a throwaway, the tests)
  --browser   in your web browser instead
  --server    the server only: prints its address (tests, or another device's browser via a tunnel of your own)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import threading
import webbrowser

from . import APP_NAME, VERSION, libraries, update
from .library import Library
from .server import serve

_Instance = libraries.Lock  # one Gallery per data folder


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
            from .winshell import APP_ID
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass


SIZE = {"width": 1440, "height": 920}  # the first time, or when the last place isn't on a screen any more
MIN_SIZE = (900, 600)


def _window_place(home: str) -> dict:
    """Where the window was last time on this PC (window.json): create_window's width, height, x, y, maximized."""
    try:
        with open(os.path.join(home, "window.json"), encoding="utf-8") as f:
            w = json.load(f)
        x, y, width, height = (int(w[k]) for k in ("x", "y", "width", "height"))
    except (OSError, ValueError, KeyError, TypeError):
        return dict(SIZE)
    width, height = max(width, MIN_SIZE[0]), max(height, MIN_SIZE[1])
    place = {"width": width, "height": height, "maximized": bool(w.get("maximized"))}
    try:
        from .winshell import on_screen
        if on_screen(x, y, width):
            place.update(x=x, y=y)
    except Exception:
        pass  # centred, at its last size
    return place


def _save_window_place(hwnd, home: str) -> None:
    if os.name != "nt" or not hwnd:
        return
    try:
        from .config import write_atomic
        from .winshell import placement
        write_atomic(os.path.join(home, "window.json"), json.dumps(placement(hwnd)).encode("utf-8"))
    except Exception:
        pass


def _pin_as_gallery(hwnd, pkg: str | None) -> None:
    """Pinning the window pins the portable folder's launcher, under the Gallery's name and icon (not "Python")."""
    if os.name != "nt" or not hwnd:
        return
    try:
        from . import winshell
        exe = os.path.join(pkg, f"{APP_NAME}.exe") if pkg else None
        if exe and os.path.isfile(exe):
            winshell.window_identity(hwnd, f'"{exe}"', APP_NAME, f"{exe},0")
        else:  # a checkout: no launcher to pin, but the window still groups as the Gallery
            winshell.window_identity(hwnd)
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
    which = ap.add_mutually_exclusive_group()
    which.add_argument("--library", help="open this library (a folder with a .mlgallery folder in it)")
    which.add_argument("--local", action="store_true", help="open this PC's library")
    which.add_argument("--data", help="a data folder of its own: index, thumbnails and settings (a throwaway, the tests)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--browser", action="store_true", help="open in your web browser instead of its own window")
    mode.add_argument("--server", action="store_true", help="run the server only and print its address")
    mode.add_argument("--tray", action="store_true", help="start in the tray, the window hidden (Settings, Keep running in the tray)")
    ap.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: any free one)")
    args = ap.parse_args(argv)

    pkg = update.portable_root()
    try:
        pick = libraries.choose(args.library, args.data, pkg, args.local)
    except ValueError as ex:
        _message(str(ex))
        return 1
    data, root = pick["data"], pick["root"]
    lock = _Instance(data)
    if not lock.acquire():
        from . import tray
        if args.tray or tray.wake(data):  # in the tray: its window opens (at sign-in, it's simply there already)
            return 0
        _message(f"{APP_NAME} is already open with {'the library ' + root if root else 'this library'}.")
        return 1
    running = None
    if pkg:  # while this is open, the launcher won't swap a downloaded update into this portable folder
        try:
            running = open(os.path.join(pkg, "running.lock"), "a+")
        except OSError:
            pass

    from .db import NewerLibrary
    try:
        lib = Library(data, pick["home"], root)
    except NewerLibrary as ex:
        _message(str(ex))
        lock.release()
        return 1
    lib.note = pick["note"]
    try:
        libraries.prepare(lib)  # opened from another drive letter or place: its folders follow it
    except Exception as ex:  # it still opens, and says so
        lib.note = f"This library's folders couldn't be brought up to where it is now ({ex}): some may show as not reachable."
    if pick["remember"]:
        libraries.remember(root)
    # how to open this same library again (Restart now, after an update)
    lib.reopen = ["--library", root] if root else ["--data", data] if args.data or os.environ.get("MLG_DATA") else ["--local"]
    window = {"w": None, "quitting": False, "told": False}
    stop = threading.Event()
    icon = None  # the tray icon (in its own window, on Windows)

    def pick_folder():
        w = window["w"]
        if not w:
            return None
        import webview
        kind = getattr(getattr(webview, "FileDialog", None), "FOLDER", None) or getattr(webview, "FOLDER_DIALOG")
        chosen = w.create_file_dialog(kind)
        return chosen[0] if chosen else None

    dragging = threading.Lock()

    def drag_files(paths: list[str]):
        """Windows' own file drag from the window, with these files: Explorer and the desktop copy them, a browser page
        (ComfyUI) gets them as files. Runs on the window's thread while the mouse button is still down. -> what the drop did."""
        w = window["w"]
        if not w or not paths:
            return None
        from System import Action, Array, String  # (pythonnet: what pywebview's window is made with)
        from System.Windows.Forms import Control, DataFormats, DataObject, DragDropEffects, MouseButtons
        if not dragging.acquire(blocking=False):
            return "None"  # (one at a time: a second waiting for the first would start once its button was long let go)
        try:
            form = w.native
            data = DataObject()
            data.SetData(DataFormats.FileDrop, Array[String](paths))
            done = {"effect": "None"}

            def go():
                if Control.MouseButtons & MouseButtons.Left:  # (let go already: no drop wherever the pointer happens to be)
                    done["effect"] = str(form.DoDragDrop(data, DragDropEffects.Copy))
            form.Invoke(Action(go))
            return done["effect"]
        finally:
            dragging.release()

    def hwnd_of():
        try:
            return int(window["w"].native.Handle.ToInt64())  # the WinForms window itself
        except Exception:
            return None

    def quit_app():
        window["quitting"] = True
        if window["w"]:
            _save_window_place(hwnd_of(), pick["home"])  # destroy() isn't a close: the closing event doesn't come
            window["w"].destroy()
        else:
            stop.set()

    def relaunch(target: list[str]) -> None:
        """Start a Gallery with `target` (another library, or this one after an update); quit_app() follows."""
        if os.name == "nt":
            try:
                from .winshell import allow_next_to_front
                allow_next_to_front()  # this window is in front now: the next one may take its place
            except Exception:
                pass
        cmd, cwd = libraries.launch_command(target, browser=args.browser)
        subprocess.Popen(cmd, cwd=cwd, close_fds=True,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))

    loop = asyncio.new_event_loop()
    ready = threading.Event()
    info = {}
    hooks = {}  # the server alone has no window to close, so it can't switch libraries or restart
    if not args.server:
        hooks.update(quit=quit_app, relaunch=relaunch)
    if not (args.browser or args.server):
        hooks.update(pick_folder=pick_folder)
        if os.name == "nt":
            hooks.update(drag_files=drag_files)

    # ---------------------------------------------------------------- the tray (Settings, Keep running in the tray)
    def in_tray() -> bool:
        return bool(lib.settings.get("tray") or (args.tray and icon and icon.wanted))  # (--tray: until the tray's turned off)

    def open_window(view: str | None = None):
        w = window["w"]
        if not w:
            return
        try:
            w.show()
            w.restore()
            hwnd = hwnd_of()
            if hwnd and os.name == "nt":
                from .winshell import to_front
                to_front(hwnd)
            if view:
                w.evaluate_js(f"window.mlgOpen && window.mlgOpen({json.dumps(view)})")
        except Exception:
            pass

    def tray_menu() -> list:
        items = [{"label": "Open the Gallery", "do": open_window, "default": True}]
        if "illustrations" in (lib.kinds or []):
            try:
                n = lib.inbox.count()
            except Exception:
                n = 0
            items.append({"label": f"New downloads ({n:,})" if n else "New downloads", "do": lambda: open_window("downloads")})
        items += [None,
                  {"label": "Phone and tablet", "checked": bool(lib.settings.get("remote")), "do": lambda: switch_remote(not lib.settings.get("remote"))},
                  {"label": "Pause watching folders", "checked": lib.watcher.paused, "do": lambda: lib.pause_watching(not lib.watcher.paused)},
                  None, {"label": "Quit", "do": quit_app}]
        return items

    def switch_remote(on: bool):
        from .server import remote_switch
        try:
            why = asyncio.run_coroutine_threadsafe(remote_switch(info["runner"].app, on), loop).result(15)
        except Exception as ex:
            why = str(ex)
        if why and os.name == "nt":
            from .winshell import notify
            notify("Phone and tablet didn't start", why)

    if not (args.browser or args.server) and os.name == "nt":
        from .tray import Tray
        icon = Tray(data, ICON, tray_menu, open_window, tip=f"{APP_NAME}{' - ' + os.path.basename(root) if root else ''}")
        if icon.start():
            icon.show(bool(args.tray or lib.settings.get("tray")))
            hooks.update(tray=icon)
        else:
            icon = None

    def run_server():
        asyncio.set_event_loop(loop)
        runner, port, token = loop.run_until_complete(serve(lib, args.port, **hooks))
        info.update(runner=runner, port=port, token=token)
        ready.set()
        loop.run_forever()
        loop.run_until_complete(runner.cleanup())

    t = threading.Thread(target=run_server, name="server", daemon=True)
    t.start()
    ready.wait(30)
    lib.start()
    if root:
        threading.Thread(target=libraries.bring_when_scanned, args=(lib, root), name="bring-marks", daemon=True).start()
    url = f"http://127.0.0.1:{info['port']}/?t={info['token']}"
    try:
        if args.server:
            print(url, flush=True)
            threading.Event().wait()
        elif args.browser:
            webbrowser.open(url)
            print(f"{APP_NAME} is open in your browser. Close this window to quit.", flush=True)
            stop.wait()
        else:
            import webview
            _own_taskbar_entry()
            hidden = bool(args.tray and icon)  # (started at sign-in: only the tray icon, until it's opened)
            window["w"] = webview.create_window(APP_NAME, url, min_size=MIN_SIZE, background_color="#282623", hidden=hidden, **_window_place(pick["home"]))

            def shown():
                hwnd = hwnd_of()
                _set_window_icon(hwnd)
                _pin_as_gallery(hwnd, pkg)
                if hwnd and os.name == "nt" and not hidden:
                    try:
                        from .winshell import to_front
                        to_front(hwnd)  # opened by another Gallery (switching libraries) Windows would leave it behind
                    except Exception:
                        pass

            def closing():
                _save_window_place(hwnd_of(), pick["home"])
                if icon and in_tray() and not window["quitting"]:  # into the tray: the Gallery keeps running
                    threading.Thread(target=window["w"].hide, daemon=True).start()
                    if not window["told"]:
                        window["told"] = True
                        try:
                            from .winshell import notify
                            notify(f"{APP_NAME} is still running", "In the tray, by the clock: click its icon to open it, or Quit from its menu.")
                        except Exception:
                            pass
                    return False
                return None
            window["w"].events.shown += shown
            window["w"].events.closing += closing
            webview.start(private_mode=False, storage_path=os.path.join(pick["home"], "webview"))
    except KeyboardInterrupt:
        pass
    finally:
        if icon:
            icon.stop()
        lib.stop()
        loop.call_soon_threadsafe(loop.stop)
        t.join(5)
        lock.release()
        if running:
            running.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
