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


def read_shortcut(lnk: str) -> str | None:
    with _Com():
        link, persist, store = _open_link(os.path.abspath(lnk), False)
        try:
            return _get(store, ID)
        finally:
            for o in (store, persist, link):
                _release(o)
