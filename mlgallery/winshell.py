"""The Gallery's identity on the taskbar: so pinning it pins the Gallery, not the Python that runs it.

The Gallery's window belongs to pythonw.exe (Model Librarian Gallery.exe only starts it), so Windows would pin
"Python". Instead the window carries the Gallery's own app id and says how to start it again: the launcher, under
the Gallery's name and icon (`window_identity`). The Start menu shortcut carries the same app id
(`shortcut_identity`), so the window groups under a pin made from either. Both are Windows property stores,
set through the shell's COM interfaces with ctypes (no extra package).
"""
from __future__ import annotations

import ctypes
import os
import uuid
from ctypes import POINTER, byref, c_void_p, c_wchar_p

APP_ID = "EY4O.ModelLibrarianGallery"
_AUMID = "9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"  # PKEY_AppUserModel_*
RELAUNCH_COMMAND, RELAUNCH_ICON, RELAUNCH_NAME, ID = 2, 3, 4, 5
VT_LPWSTR = 31
IID_PROPERTY_STORE = "886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"
CLSID_SHELL_LINK = "00021401-0000-0000-C000-000000000046"
IID_SHELL_LINK = "000214F9-0000-0000-C000-000000000046"
IID_PERSIST_FILE = "0000010B-0000-0000-C000-000000000046"


class GUID(ctypes.Structure):
    _fields_ = [("data", ctypes.c_ubyte * 16)]


def _guid(s: str) -> GUID:
    return GUID.from_buffer_copy(uuid.UUID(s).bytes_le)


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", ctypes.c_ulong)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                ("value", c_void_p), ("pad", c_void_p)]


def _key(pid: int) -> PROPERTYKEY:
    return PROPERTYKEY(_guid(_AUMID), pid)


def _call(obj, index: int, *args):
    """Method `index` of a COM interface pointer (a failing HRESULT raises OSError)."""
    vtable = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *(type(a) for a in args))
    return proto(vtable[index])(obj, *args)


def _release(obj) -> None:
    if obj:
        ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents[2])(obj)


def _set(store, pid: int, text: str) -> None:
    buf = ctypes.create_unicode_buffer(text)
    pv = PROPVARIANT(VT_LPWSTR, 0, 0, 0, ctypes.cast(buf, c_void_p), None)
    _call(store, 6, ctypes.pointer(_key(pid)), ctypes.pointer(pv))  # IPropertyStore::SetValue (it copies the value)


def _get(store, pid: int) -> str | None:
    pv = PROPVARIANT()
    _call(store, 5, ctypes.pointer(_key(pid)), ctypes.pointer(pv))  # IPropertyStore::GetValue
    try:
        return ctypes.wstring_at(pv.value) if pv.vt == VT_LPWSTR and pv.value else None
    finally:
        ctypes.oledll.ole32.PropVariantClear(byref(pv))


class _Com:
    """COM for this thread, for as long as it's needed (the server calls in from its worker threads)."""

    def __enter__(self):
        try:
            ctypes.oledll.ole32.CoInitializeEx(None, 2)  # apartment-threaded; S_FALSE when it already was
            self.mine = True
        except OSError:
            self.mine = False  # already initialised another way on this thread: use it as it is
        return self

    def __exit__(self, *exc):
        if self.mine:
            ctypes.windll.ole32.CoUninitialize()


def _window_store(hwnd: int):
    store = c_void_p()
    ctypes.oledll.shell32.SHGetPropertyStoreForWindow(c_void_p(hwnd), byref(_guid(IID_PROPERTY_STORE)), byref(store))
    return store


def window_identity(hwnd: int, relaunch: str | None = None, name: str | None = None, icon: str | None = None) -> None:
    """Give a window the Gallery's app id and, when there's a launcher to start again, how pinning it relaunches it."""
    with _Com():
        store = _window_store(hwnd)
        try:
            if relaunch:
                _set(store, RELAUNCH_COMMAND, relaunch)
                _set(store, RELAUNCH_NAME, name or "")
                if icon:
                    _set(store, RELAUNCH_ICON, icon)
            _set(store, ID, APP_ID)
        finally:
            _release(store)


def read_window(hwnd: int) -> dict:
    with _Com():
        store = _window_store(hwnd)
        try:
            return {"id": _get(store, ID), "relaunch": _get(store, RELAUNCH_COMMAND), "name": _get(store, RELAUNCH_NAME),
                    "icon": _get(store, RELAUNCH_ICON)}
        finally:
            _release(store)


def _open_link(lnk: str, write: bool):
    """(IShellLink, IPersistFile, IPropertyStore) for a saved shortcut."""
    link, persist, store = c_void_p(), c_void_p(), c_void_p()
    ctypes.oledll.ole32.CoCreateInstance(byref(_guid(CLSID_SHELL_LINK)), None, 1, byref(_guid(IID_SHELL_LINK)), byref(link))
    try:
        _call(link, 0, ctypes.pointer(_guid(IID_PERSIST_FILE)), ctypes.pointer(persist))  # QueryInterface
        _call(persist, 5, c_wchar_p(lnk), ctypes.c_ulong(2 if write else 0))  # IPersistFile::Load (STGM_READWRITE / READ)
        _call(link, 0, ctypes.pointer(_guid(IID_PROPERTY_STORE)), ctypes.pointer(store))
    except OSError:
        for o in (store, persist, link):
            _release(o)
        raise
    return link, persist, store


def shortcut_identity(lnk: str) -> None:
    """Put the Gallery's app id on a shortcut, so the window groups under a pin made from it."""
    with _Com():
        link, persist, store = _open_link(os.path.abspath(lnk), True)
        try:
            _set(store, ID, APP_ID)
            _call(store, 7)  # IPropertyStore::Commit
            _call(persist, 6, c_wchar_p(None), ctypes.c_int(1))  # IPersistFile::Save, to the file it came from
        finally:
            for o in (store, persist, link):
                _release(o)


# ---------------------------------------------------------------- where the window was, and coming to the front
class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _PLACEMENT(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint), ("flags", ctypes.c_uint), ("showCmd", ctypes.c_uint),
                ("ptMin", ctypes.c_long * 2), ("ptMax", ctypes.c_long * 2), ("normal", _RECT)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT), ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]


_U32 = None


def _user32():
    """user32 with argument types set: a handle of its own, so pywebview's calls through windll aren't changed."""
    global _U32
    if _U32:
        return _U32
    u = ctypes.WinDLL("user32")
    u.MonitorFromWindow.restype = u.MonitorFromRect.restype = c_void_p
    u.MonitorFromWindow.argtypes = [c_void_p, ctypes.c_ulong]
    u.MonitorFromRect.argtypes = [POINTER(_RECT), ctypes.c_ulong]
    u.GetForegroundWindow.restype = c_void_p
    for name in ("GetWindowPlacement", "GetWindowRect", "SetForegroundWindow", "BringWindowToTop", "GetDpiForWindow"):
        getattr(u, name).argtypes = [c_void_p] + ([c_void_p] if name in ("GetWindowPlacement", "GetWindowRect") else [])
    u.GetWindowThreadProcessId.argtypes = [c_void_p, c_void_p]
    u.GetMonitorInfoW.argtypes = [c_void_p, c_void_p]
    _U32 = u
    return u


def placement(hwnd: int) -> dict:
    """The window's normal (not maximised) place and size in logical pixels, and whether it's maximised:
    what pywebview takes back (x, y, width, height, maximized)."""
    u = _user32()
    wp = _PLACEMENT()
    wp.length = ctypes.sizeof(_PLACEMENT)
    if not u.GetWindowPlacement(c_void_p(hwnd), byref(wp)):
        raise OSError("no window placement")
    maximized = wp.showCmd == 3 or (wp.showCmd == 2 and bool(wp.flags & 2))  # maximised, or minimised from maximised
    if wp.showCmd == 1:  # shown normally: its real place on screen
        r = _RECT()
        u.GetWindowRect(c_void_p(hwnd), byref(r))
    else:  # the place it goes back to, in work-area coordinates: moved by the taskbar when that's at the top or left
        r = wp.normal
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if u.GetMonitorInfoW(u.MonitorFromWindow(c_void_p(hwnd), 2), byref(mi)):
            dx, dy = mi.rcWork.left - mi.rcMonitor.left, mi.rcWork.top - mi.rcMonitor.top
            r = _RECT(r.left + dx, r.top + dy, r.right + dx, r.bottom + dy)
    scale = (u.GetDpiForWindow(c_void_p(hwnd)) or 96) / 96
    return {"x": round(r.left / scale), "y": round(r.top / scale), "width": round((r.right - r.left) / scale),
            "height": round((r.bottom - r.top) / scale), "maximized": maximized}


def on_screen(x: int, y: int, width: int) -> bool:
    """Is the top of a window at this logical place on a screen now (a monitor unplugged since, a smaller one)?"""
    scale = (_user32().GetDpiForSystem() or 96) / 96
    strip = _RECT(int((x + 40) * scale), int(y * scale), int((x + max(width - 40, 41)) * scale), int((y + 30) * scale))
    return bool(_user32().MonitorFromRect(byref(strip), 0))  # MONITOR_DEFAULTTONULL


def allow_next_to_front() -> None:
    """Before this Gallery closes for another one (switching libraries, Restart now): let that window come to the front."""
    ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY


def to_front(hwnd: int) -> bool:
    """Bring a window just opened to the front. Windows only lets the program the user is in do that, so if it's
    refused: borrow the front window's input queue for a moment (the usual way round), and if even that's refused,
    flash the taskbar button so it's easy to find."""
    u = _user32()
    h = c_void_p(hwnd)
    if u.SetForegroundWindow(h) and u.GetForegroundWindow() == hwnd:
        return True
    front = u.GetForegroundWindow()
    theirs = u.GetWindowThreadProcessId(c_void_p(front), None) if front else 0
    mine = ctypes.windll.kernel32.GetCurrentThreadId()
    if theirs and theirs != mine and u.AttachThreadInput(theirs, mine, True):
        try:
            u.BringWindowToTop(h)
            u.SetForegroundWindow(h)
        finally:
            u.AttachThreadInput(theirs, mine, False)
    if u.GetForegroundWindow() == hwnd:
        return True

    class FLASHWINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("hwnd", c_void_p), ("dwFlags", ctypes.c_ulong), ("uCount", ctypes.c_uint), ("dwTimeout", ctypes.c_ulong)]
    fw = FLASHWINFO(ctypes.sizeof(FLASHWINFO), h, 0x3 | 0xC, 0, 0)  # caption and button, until it's brought up
    u.FlashWindowEx(byref(fw))
    return False


def read_shortcut(lnk: str) -> str | None:
    with _Com():
        link, persist, store = _open_link(os.path.abspath(lnk), False)
        try:
            return _get(store, ID)
        finally:
            for o in (store, persist, link):
                _release(o)
