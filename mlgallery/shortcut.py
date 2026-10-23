"""A Start menu shortcut to the Gallery (made when you ask, from Settings): a normal Windows .lnk with its icon."""
from __future__ import annotations

import os
import subprocess
import sys

from . import APP_NAME

HERE = os.path.dirname(os.path.abspath(__file__))
PS = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:MLG_LNK); $s.TargetPath = $env:MLG_TARGET; $s.Arguments = $env:MLG_ARGS;"
      " $s.WorkingDirectory = $env:MLG_WD; $s.IconLocation = $env:MLG_ICON; $s.Description = $env:MLG_DESC; $s.Save()")


def start_menu() -> str:
    return os.environ.get("MLG_SHORTCUT_DIR") or os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs")


def target() -> tuple[str, str, str]:
    """(program, arguments, working folder) that start this Gallery without a console window."""
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(pyw):
        pyw = sys.executable
    app = os.path.dirname(HERE)
    start = os.path.join(app, "start.pyw")
    if os.path.isfile(start):  # the portable folder
        return pyw, f'"{start}"', os.path.dirname(app)
    return pyw, "-m mlgallery", app  # a checkout (run.bat's .venv)


def make() -> str:
    if os.name != "nt":
        raise OSError("Only on Windows.")
    folder = start_menu()
    os.makedirs(folder, exist_ok=True)
    lnk = os.path.join(folder, f"{APP_NAME}.lnk")
    prog, args, wd = target()
    env = dict(os.environ, MLG_LNK=lnk, MLG_TARGET=prog, MLG_ARGS=args, MLG_WD=wd, MLG_ICON=os.path.join(HERE, "ui", "icon.ico"),
               MLG_DESC="Browse every image and video you've generated, and see what made each one")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", PS], env=env, capture_output=True, text=True, timeout=30,
                       creationflags=0x08000000)
    if r.returncode or not os.path.isfile(lnk):
        raise OSError((r.stderr or "the shortcut wasn't made").strip()[:300])
    return lnk


def exists() -> bool:
    return os.path.isfile(os.path.join(start_menu(), f"{APP_NAME}.lnk"))
