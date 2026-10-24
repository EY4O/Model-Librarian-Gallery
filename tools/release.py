"""Make a release: the update zip (and with --full, the whole portable folder) in dist/; with --publish, the GitHub release.

    python tools/release.py [--full] [--publish]

  dist/mlgallery-<version>.zip          the portable folder's app\\ (mlgallery\\, start.pyw, requirements.txt): what a
                                        Gallery with Updates on downloads; the same bytes for the same code
  dist/mlgallery-<version>.zip.sha256   its SHA-256 (the Gallery also takes GitHub's own digest of the asset)
  --full     also dist/Model-Librarian-Gallery-<version>-portable.zip: the whole folder (about 270 MB), for a first
             install and for a release that needs other packages (the Gallery won't update itself across those)
  --publish  creates the release v<version> on GitHub with gh and attaches the files (the tree must be clean,
             HEAD pushed to origin/main, and the tag new). Its notes are HEAD's commit message.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import build_portable  # noqa: E402
from mlgallery import VERSION  # noqa: E402

DIST = os.path.join(ROOT, "dist")
STAMP = (2020, 1, 1, 0, 0, 0)  # every entry dated the same, so the zip's bytes depend only on its contents


def zip_folder(src: str, out: str, prefix: str = "") -> None:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for d, dirs, names in os.walk(src):
            dirs.sort()
            for n in sorted(names):
                p = os.path.join(d, n)
                info = zipfile.ZipInfo(prefix + os.path.relpath(p, src).replace(os.sep, "/"), STAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                with open(p, "rb") as f:
                    z.writestr(info, f.read())


def update_zip(dist: str = DIST) -> str:
    """dist/mlgallery-<version>.zip and its .sha256 -> the zip's path."""
    os.makedirs(dist, exist_ok=True)
    out = os.path.join(dist, f"mlgallery-{VERSION}.zip")
    with tempfile.TemporaryDirectory() as tmp:
        build_portable.make_app(tmp)
        zip_folder(tmp, out)
    with open(out, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    with open(out + ".sha256", "w", encoding="utf-8", newline="\n") as f:
        f.write(f"{sha}  {os.path.basename(out)}\n")
    return out


def full_zip(dist: str = DIST) -> str:
    out = os.path.join(dist, f"Model-Librarian-Gallery-{VERSION}-portable.zip")
    with tempfile.TemporaryDirectory() as tmp:
        pkg = build_portable.main(["--out", tmp])
        zip_folder(pkg, out, prefix=build_portable.NAME + "/")
    return out


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def publish(files: list[str]) -> None:
    tag = f"v{VERSION}"
    if git("status", "--porcelain"):
        sys.exit("Commit everything first: the release is made from HEAD.")
    git("fetch", "-q", "origin", "main")
    if subprocess.run(["git", "merge-base", "--is-ancestor", "HEAD", "origin/main"], cwd=ROOT).returncode:
        sys.exit("Push to origin/main first: the release points at a commit GitHub has.")
    if subprocess.run(["gh", "release", "view", tag], cwd=ROOT, capture_output=True).returncode == 0:
        sys.exit(f"{tag} is already released: bump VERSION in mlgallery/__init__.py.")
    notes = git("log", "-1", "--format=%B")
    subprocess.run(["gh", "release", "create", tag, *files, "--title", VERSION, "--notes", notes, "--target", git("rev-parse", "HEAD")],
                   cwd=ROOT, check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="also zip the whole portable folder")
    ap.add_argument("--publish", action="store_true", help="create the GitHub release with gh")
    a = ap.parse_args()
    if os.path.isdir(DIST):
        shutil.rmtree(DIST)  # only this version's files go up
    z = update_zip()
    files = [z, z + ".sha256"]
    print("made", z)
    if a.full:
        files.append(full_zip())
        print("made", files[-1])
    if a.publish:
        publish(files)
        print(f"released v{VERSION}")


if __name__ == "__main__":
    main()
