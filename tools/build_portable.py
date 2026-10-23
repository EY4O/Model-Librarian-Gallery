"""Build the portable Gallery: one folder with its own Python, its packages and ffmpeg. Nothing to install.

    python tools/build_portable.py [--out DIR] [--python DIR] [--ffmpeg DIR]

  --out     where the folder goes (default: %LOCALAPPDATA%\\Programs, where Windows keeps per-user apps;
            not inside a OneDrive folder, which would sync its 270 MB)

  --python  a standalone CPython 3.12 folder to copy (default: the one `uv python find 3.12` names)
  --ffmpeg  a folder with ffmpeg.exe and ffprobe.exe (default: the one on PATH); left out if none

The packages come from requirements.txt through uv (from its cache when they're there). The result:

    Model Librarian Gallery/
      Model Librarian Gallery.cmd   start it (or use Settings -> Add to the Start menu)
      python/                       its own Python
      app/                          the Gallery (mlgallery/) and start.pyw
      ffmpeg/                       for video posters
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "Model Librarian Gallery"
SKIP_LIB = {"test", "idlelib", "tkinter", "turtledemo", "ensurepip", "site-packages", "lib2to3", "pydoc_data"}
SKIP_TOP = {"tcl", "include", "libs", "Scripts", "Tools", "Doc"}

START = '''"""Starts Model Librarian Gallery from its portable folder (python/, app/, ffmpeg/ side by side)."""
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
ff = os.path.join(os.path.dirname(here), "ffmpeg", "ffmpeg.exe")
if os.path.isfile(ff):
    os.environ.setdefault("MLG_FFMPEG", ff)
from mlgallery.__main__ import main  # noqa: E402

sys.exit(main())
'''

CMD = '''@echo off
rem Starts Model Librarian Gallery (portable: its own Python and ffmpeg are in this folder).
start "" "%~dp0python\\pythonw.exe" "%~dp0app\\start.pyw" %*
'''

README = '''Model Librarian Gallery (portable)

Start it with "Model Librarian Gallery.cmd" (or, once it's open, Settings > Add to the Start menu).
Everything it needs is in this folder; its index, thumbnails and settings live in
%LOCALAPPDATA%\\Model Librarian Gallery, so a newer build can replace this folder without losing anything.
'''


def find_python() -> str:
    out = subprocess.run(["uv", "python", "find", "3.12"], capture_output=True, text=True).stdout.strip()
    if not out:
        sys.exit("No Python 3.12 found: install one with `uv python install 3.12`, or pass --python.")
    exe = subprocess.run([out, "-c", "import sys; print(sys.base_prefix)"], capture_output=True, text=True).stdout.strip()
    return exe


def copy_python(src: str, dst: str) -> None:
    def ignore(d, names):
        rel = os.path.relpath(d, src)
        skip = {n for n in names if n == "__pycache__"}
        if rel == ".":
            skip |= {n for n in names if n in SKIP_TOP}
        if os.path.normcase(rel) == os.path.normcase("Lib"):
            skip |= {n for n in names if n in SKIP_LIB}
        return skip
    shutil.copytree(src, dst, ignore=ignore)
    os.makedirs(os.path.join(dst, "Lib", "site-packages"), exist_ok=True)
    for marker in ("EXTERNALLY-MANAGED", os.path.join("Lib", "EXTERNALLY-MANAGED")):
        try:
            os.remove(os.path.join(dst, marker))  # this copy is the Gallery's own: packages go straight into it
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Programs"))
    ap.add_argument("--python")
    ap.add_argument("--ffmpeg")
    a = ap.parse_args()
    pkg = os.path.join(a.out, NAME)
    if os.path.exists(pkg):
        shutil.rmtree(pkg)
    os.makedirs(pkg)
    src = a.python or find_python()
    print("python  from", src)
    copy_python(src, os.path.join(pkg, "python"))
    py = os.path.join(pkg, "python", "python.exe")
    print("packages from requirements.txt")
    subprocess.run(["uv", "pip", "install", "--python", py, "--system", "--break-system-packages", "-q", "-r", os.path.join(ROOT, "requirements.txt")], check=True)
    print("the Gallery")
    shutil.copytree(os.path.join(ROOT, "mlgallery"), os.path.join(pkg, "app", "mlgallery"), ignore=shutil.ignore_patterns("__pycache__"))
    with open(os.path.join(pkg, "app", "start.pyw"), "w", encoding="utf-8", newline="\n") as f:
        f.write(START)
    ff_dir = a.ffmpeg or (os.path.dirname(shutil.which("ffmpeg")) if shutil.which("ffmpeg") else None)
    if ff_dir and os.path.isfile(os.path.join(ff_dir, "ffmpeg.exe")):
        print("ffmpeg  from", ff_dir)
        dst = os.path.join(pkg, "ffmpeg")
        os.makedirs(dst)
        for n in os.listdir(ff_dir):
            if n.lower().endswith((".exe", ".dll")) and n.lower() != "ffplay.exe":
                shutil.copy2(os.path.join(ff_dir, n), dst)
    else:
        print("ffmpeg  not found: the package will make no video posters until it's installed")
    with open(os.path.join(pkg, f"{NAME}.cmd"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write(CMD)
    with open(os.path.join(pkg, "README.txt"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write(README)
    size = sum(os.path.getsize(os.path.join(d, n)) for d, _, ns in os.walk(pkg) for n in ns)
    print(f"built {pkg} ({size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
