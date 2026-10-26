"""The Gallery in the notification area (the tray): Settings, Keep running in the tray.

With it on, closing the window only hides it: the Gallery keeps running (paired phones and tablets, the folder
watcher, new-download notifications), with an icon by the clock. A click on it opens the window; its menu also has
New downloads, Phone and tablet, Pause watching folders and Quit. Starting the Gallery again while it's in the tray
opens that one (`wake`). It can also start at sign-in, in the tray (`set_login`: the Run key of this Windows user).

Written against Windows' own Shell_NotifyIcon, so it needs no package: an update can still install it.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import sys
import threading

from . import APP_NAME

WM_APP = 0x8000
CALLBACK = WM_APP + 1   # the icon's mouse messages
WAKE = WM_APP + 2       # another start of this Gallery: open the window
SHOW = WM_APP + 3       # the icon shown (wParam 1) or not (0)
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = APP_NAME


def class_name(data_dir: str) -> str:
    """The tray window's class: one per library, so a second start finds its own Gallery, not another library's."""
    return "MLGalleryTray-" + hashlib.sha1(os.path.normcase(os.path.abspath(data_dir)).encode("utf-8")).hexdigest()[:12]


def wake(data_dir: str) -> bool:
    """The Gallery already running with this library: open its window. -> whether there was one to ask."""
    if os.name != "nt":
        return False
    try:
        u = ctypes.windll.user32
        u.FindWindowW.restype = ctypes.c_void_p
        u.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        u.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        hwnd = u.FindWindowW(class_name(data_dir), None)
        if not hwnd:
            return False
        u.AllowSetForegroundWindow(-1)  # (ASFW_ANY: it may come to the front)
        return bool(u.PostMessageW(hwnd, WAKE, 0, 0))
    except Exception:
        return False


# ---------------------------------------------------------------- starting at sign-in
def login_command() -> str:
    """How Windows starts the Gallery at sign-in, in the tray: the portable folder's launcher, or this checkout's Python."""
    from . import update
    pkg = update.portable_root()
    if pkg:
        return f'"{os.path.join(pkg, APP_NAME + ".exe")}" --tray'
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    boot = f"import sys; sys.path.insert(0, r'{repo}'); from mlgallery.__main__ import main; sys.exit(main(['--tray']))"
    return f'"{pyw if os.path.isfile(pyw) else exe}" -c "{boot}"'


def set_login(on: bool) -> None:
    """Start at sign-in (this Windows user's Run key), or not. Raises OSError if Windows won't."""
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        if on:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, login_command())
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except FileNotFoundError:
                pass


def at_login() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return bool(winreg.QueryValueEx(k, RUN_NAME)[0])
    except OSError:
        return False


# ---------------------------------------------------------------- the icon
if os.name == "nt":
    from ctypes import wintypes as wt

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", ctypes.c_uint), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", ctypes.c_void_p), ("hIcon", ctypes.c_void_p), ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
                    ("lpszMenuName", ctypes.c_wchar_p), ("lpszClassName", ctypes.c_wchar_p)]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("hWnd", ctypes.c_void_p), ("uID", ctypes.c_uint), ("uFlags", ctypes.c_uint),
                    ("uCallbackMessage", ctypes.c_uint), ("hIcon", ctypes.c_void_p), ("szTip", ctypes.c_wchar * 128),
                    ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD), ("szInfo", ctypes.c_wchar * 256), ("uVersion", ctypes.c_uint),
                    ("szInfoTitle", ctypes.c_wchar * 64), ("dwInfoFlags", wt.DWORD), ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", ctypes.c_void_p)]

    class MSG(ctypes.Structure):
        _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint), ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
                    ("time", wt.DWORD), ("pt", wt.POINT), ("lPrivate", wt.DWORD)]


def _api():
    u, s, k = ctypes.windll.user32, ctypes.windll.shell32, ctypes.windll.kernel32
    vp, ui, sz, ss = ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t
    for name, res, args in [
        ("DefWindowProcW", LRESULT, [vp, ui, sz, ss]), ("RegisterClassW", ctypes.c_ushort, [ctypes.POINTER(WNDCLASSW)]),
        ("UnregisterClassW", ctypes.c_int, [ctypes.c_wchar_p, vp]),
        ("CreateWindowExW", vp, [wt.DWORD, ctypes.c_wchar_p, ctypes.c_wchar_p, wt.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, vp, vp, vp, vp]),
        ("DestroyWindow", ctypes.c_int, [vp]), ("PostMessageW", ctypes.c_int, [vp, ui, sz, ss]), ("PostQuitMessage", None, [ctypes.c_int]),
        ("GetMessageW", ctypes.c_int, [ctypes.c_void_p, vp, ui, ui]), ("TranslateMessage", ctypes.c_int, [ctypes.c_void_p]),
        ("DispatchMessageW", LRESULT, [ctypes.c_void_p]), ("RegisterWindowMessageW", ui, [ctypes.c_wchar_p]),
        ("LoadImageW", vp, [vp, ctypes.c_wchar_p, ui, ctypes.c_int, ctypes.c_int, ui]), ("DestroyIcon", ctypes.c_int, [vp]),
        ("CreatePopupMenu", vp, []), ("AppendMenuW", ctypes.c_int, [vp, ui, sz, ctypes.c_wchar_p]), ("SetMenuDefaultItem", ctypes.c_int, [vp, ui, ui]),
        ("TrackPopupMenu", ctypes.c_int, [vp, ui, ctypes.c_int, ctypes.c_int, ctypes.c_int, vp, vp]), ("DestroyMenu", ctypes.c_int, [vp]),
        ("SetForegroundWindow", ctypes.c_int, [vp]), ("GetCursorPos", ctypes.c_int, [ctypes.c_void_p]), ("GetSystemMetrics", ctypes.c_int, [ctypes.c_int]),
    ]:
        f = getattr(u, name)
        f.restype, f.argtypes = res, args
    s.Shell_NotifyIconW.restype, s.Shell_NotifyIconW.argtypes = ctypes.c_int, [wt.DWORD, ctypes.c_void_p]
    k.GetModuleHandleW.restype, k.GetModuleHandleW.argtypes = vp, [ctypes.c_wchar_p]
    return u, s, k


class Tray:
    """The icon, and the window that hears it (and `wake`), on a thread of their own.

    menu(): the menu as it is now, a list of {label, do, checked?, default?} and None for a line between;
    on_open(): what a click on the icon does."""

    def __init__(self, data_dir: str, icon_path: str, menu, on_open, tip: str = APP_NAME):
        self.cls, self.icon_path, self.menu, self.on_open, self.tip = class_name(data_dir), icon_path, menu, on_open, tip
        self.hwnd = None
        self.shown = False
        self._ready = threading.Event()
        self._icon = None
        self._proc = None

    def start(self) -> bool:
        if os.name != "nt":
            return False
        threading.Thread(target=self._run, name="tray", daemon=True).start()
        return self._ready.wait(5) and bool(self.hwnd)

    def show(self, on: bool) -> None:
        if self.hwnd:
            self._u.PostMessageW(self.hwnd, SHOW, 1 if on else 0, 0)

    def stop(self) -> None:
        if self.hwnd:
            self._u.PostMessageW(self.hwnd, 0x0010, 0, 0)  # WM_CLOSE

    # ---------------------------------------------------------------- on the tray's thread
    def _run(self) -> None:
        try:
            self._u, self._s, k = _api()
            hinst = k.GetModuleHandleW(None)
            self._taskbar = self._u.RegisterWindowMessageW("TaskbarCreated")  # (Explorer started again: the icon added again)
            self._proc = WNDPROC(self._wndproc)
            wc = WNDCLASSW(lpfnWndProc=self._proc, hInstance=hinst, lpszClassName=self.cls)
            self._u.RegisterClassW(ctypes.byref(wc))
            self.hwnd = self._u.CreateWindowExW(0, self.cls, APP_NAME, 0, 0, 0, 0, 0, None, None, hinst, None)
            size = self._u.GetSystemMetrics(49)  # SM_CXSMICON
            self._icon = self._u.LoadImageW(None, self.icon_path, 1, size, size, 0x10) if os.path.exists(self.icon_path) else None
        except Exception:
            self.hwnd = None
        finally:
            self._ready.set()
        if not self.hwnd:
            return
        msg = MSG()
        while self._u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            self._u.TranslateMessage(ctypes.byref(msg))
            self._u.DispatchMessageW(ctypes.byref(msg))
        self._notify(2)  # NIM_DELETE
        if self._icon:
            self._u.DestroyIcon(self._icon)
        self._u.UnregisterClassW(self.cls, k.GetModuleHandleW(None))
        self.hwnd = None

    def _notify(self, what: int) -> None:
        if what == 0 and self.shown:
            return
        if what == 2 and not self.shown:
            return
        d = NOTIFYICONDATAW(cbSize=ctypes.sizeof(NOTIFYICONDATAW), hWnd=self.hwnd, uID=1, uFlags=0x1 | 0x2 | 0x4,  # message, icon, tip
                            uCallbackMessage=CALLBACK, hIcon=self._icon)
        d.szTip = self.tip[:127]
        if self._s.Shell_NotifyIconW(what, ctypes.byref(d)) or what == 2:
            self.shown = what != 2

    def _wndproc(self, hwnd, msg, wp, lp):
        try:
            if msg == CALLBACK:
                if lp in (0x0202, 0x0203):  # WM_LBUTTONUP, WM_LBUTTONDBLCLK
                    self._do(self.on_open)
                elif lp == 0x0205:  # WM_RBUTTONUP
                    self._popup()
                return 0
            if msg == WAKE:
                self._do(self.on_open)
                return 0
            if msg == SHOW:
                self._notify(0 if wp else 2)  # NIM_ADD / NIM_DELETE
                return 0
            if msg == self._taskbar and self.shown:
                self.shown = False
                self._notify(0)
                return 0
            if msg == 0x0010:  # WM_CLOSE
                self._u.DestroyWindow(hwnd)
                return 0
            if msg == 0x0002:  # WM_DESTROY
                self._u.PostQuitMessage(0)
                return 0
        except Exception:
            return 0
        return self._u.DefWindowProcW(hwnd, msg, wp, lp)

    def _popup(self) -> None:
        items = self.menu() or []
        h = self._u.CreatePopupMenu()
        acts = {}
        for n, it in enumerate(items, start=1):
            if it is None:
                self._u.AppendMenuW(h, 0x800, 0, None)  # MF_SEPARATOR
                continue
            flags = (0x8 if it.get("checked") else 0) | (0x1 if it.get("disabled") else 0)  # MF_CHECKED, MF_GRAYED
            self._u.AppendMenuW(h, flags, n, it["label"])
            acts[n] = it["do"]
            if it.get("default"):
                self._u.SetMenuDefaultItem(h, n, 0)
        pt = wt.POINT()
        self._u.GetCursorPos(ctypes.byref(pt))
        self._u.SetForegroundWindow(self.hwnd)  # (or the menu won't close when you click elsewhere)
        chosen = self._u.TrackPopupMenu(h, 0x0100 | 0x0080 | 0x0002, pt.x, pt.y, 0, self.hwnd, None)  # RETURNCMD, NONOTIFY, RIGHTBUTTON
        self._u.PostMessageW(self.hwnd, 0, 0, 0)  # WM_NULL
        self._u.DestroyMenu(h)
        if chosen in acts:
            self._do(acts[chosen])

    @staticmethod
    def _do(fn) -> None:  # off the tray's thread: what it does may take a moment (or wait on the window)
        threading.Thread(target=fn, daemon=True).start()
