"""When a picture was taken (or a video recorded), so photos copied off a phone or a card land on their own day.

The grid groups and sorts by `files.shot`: the date taken when it's known, else the file's modified date (all a
generated image or a download usually has). Where the date taken comes from, best first:

* a picture's EXIF DateTimeOriginal (else DateTimeDigitized; not DateTime, which is when it was last edited):
  JPEG, HEIC, TIFF, PNG, WebP, AVIF;
* a video's recorded date: Apple's creationdate (with its offset), else the creation_time tag, read with ffprobe;
* a date in the file's name: IMG_20240612_143005, PXL_20240612_..., VID-20240612-WA0003, Screenshot_2024-06-12-14-30-05.

The name is read while indexing (it costs nothing, so a phone's photos land on their day straight away); the rest
in the background (`Dater`), newest first. Times are the camera's own wall clock, shown as they were taken.
"""
from __future__ import annotations

import calendar
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime

EARLIEST = 1980  # anything before is a camera that was never set (1970, 0000) rather than a date
_NAME = re.compile(r"(?<!\d)((?:19|20)\d\d)[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])"
                   r"(?:[-_ T.]?([01]\d|2[0-3])[-_.:]?([0-5]\d)[-_.:]?([0-5]\d)(?:[.,]?\d{1,3})?)?(?!\d)")  # (Pixel: milliseconds)


def _local(y, mo, d, h=0, mi=0, s=0) -> float | None:
    if not EARLIEST <= y <= datetime.now().year + 1:
        return None
    try:
        datetime(y, mo, d, h, mi, s)  # a real day (no 31 February rolled over into March)
        return time.mktime((y, mo, d, h, mi, s, 0, 0, -1))
    except (OverflowError, ValueError):
        return None


def from_name(name: str, mtime: float | None = None) -> float | None:
    """A date (and time, when there is one) written in a file's name, as phones and screenshot tools do. A name with
    only a day: the file's own time when it was modified that day (a generation named by its date), else noon."""
    m = _NAME.search(os.path.splitext(name)[0])
    if not m:
        return None
    y, mo, d, h, mi, s = (int(g) if g else None for g in m.groups())
    if h is not None:
        return _local(y, mo, d, h, mi, s or 0)
    day = _local(y, mo, d, 12)
    if day and mtime and time.localtime(mtime)[:3] == (y, mo, d):
        return None  # its modified time says when that day, better than a made-up hour: shown by that
    return day


def _exif_time(v) -> float | None:
    m = re.match(r"\s*(\d{4})[:\-](\d\d)[:\-](\d\d)[ T](\d\d):(\d\d):(\d\d)", str(v or ""))
    return _local(*(int(g) for g in m.groups())) if m else None


def from_image(path: str) -> float | None:
    """The EXIF date a picture was taken, if it has one."""
    from PIL import Image
    with Image.open(path) as im:
        if im.format == "PNG" and "exif" not in im.info:
            return None  # (a generation's PNG: no camera EXIF, and looking past its pixels means decoding them all)
        exif = im.getexif()
        if not exif:
            return None
        sub = exif.get_ifd(0x8769)  # the Exif IFD
        for v in (sub.get(0x9003), sub.get(0x9004)):  # DateTimeOriginal, DateTimeDigitized
            t = _exif_time(v)
            if t:
                return t
    return None


def _ffprobe(ffmpeg: str | None) -> str | None:
    if ffmpeg:
        p = os.path.join(os.path.dirname(ffmpeg), "ffprobe" + (".exe" if os.name == "nt" else ""))
        if os.path.isfile(p):
            return p
    return shutil.which("ffprobe")


def from_video(path: str, ffmpeg: str | None) -> float | None:
    """A video's recorded date from its container tags (needs ffprobe, which comes with ffmpeg)."""
    probe = _ffprobe(ffmpeg)
    if not probe:
        return None
    r = subprocess.run([probe, "-v", "error", "-show_entries", "format_tags=com.apple.quicktime.creationdate,creation_time",
                        "-of", "default=noprint_wrappers=1", path], capture_output=True, timeout=30,
                       **({"creationflags": 0x08000000} if os.name == "nt" else {}))
    tags = dict(ln.split("=", 1) for ln in r.stdout.decode(errors="replace").splitlines() if "=" in ln)
    tags = {k.split(":", 1)[-1].lower(): v.strip() for k, v in tags.items()}
    apple = tags.get("com.apple.quicktime.creationdate")
    if apple:  # 2024-06-12T14:30:05+0200: the wall clock where it was filmed
        m = re.match(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)", apple)
        if m:
            return _local(*(int(g) for g in m.groups()))
    ct = tags.get("creation_time")
    if ct:  # 2024-06-12T12:30:05.000000Z: UTC
        m = re.match(r"(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)", ct)
        if m and int(m.group(1)) >= EARLIEST:
            return float(calendar.timegm(tuple(int(g) for g in m.groups()) + (0, 0, 0)))
    return None


def taken(path: str, kind: str, name_date: float | None, ffmpeg: str | None = None) -> float | None:
    """The best date taken there is for a file: its own (EXIF, recorded date), else the one in its name."""
    try:
        own = from_video(path, ffmpeg) if kind == "video" else from_image(path)
    except Exception:  # unreadable, or a format without EXIF: the name still may have it
        own = None
    return own or name_date


class Dater:
    """Finds each file's date taken in the background, newest first; `moved` counts the files that changed day."""

    def __init__(self, db, settings=None):
        self.db, self.settings = db, settings
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.moved = 0

    def start(self) -> None:
        threading.Thread(target=self._work, name="dates", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def nudge(self) -> None:
        self._wake.set()

    def pending(self) -> int:
        with self.db.read() as c:
            return c.execute("SELECT COUNT(*) FROM files f JOIN folders d ON d.id=f.folder_id WHERE f.dated=0 AND f.missing=0 AND d.online=1").fetchone()[0]

    def run_once(self, limit: int = 64) -> int:
        """Date one batch -> how many were looked at (0 when nothing's waiting)."""
        from .thumbs import find_ffmpeg
        with self.db.read() as c:
            rows = c.execute("SELECT f.id, f.rel, f.name, f.kind, f.mtime, f.shot, d.path FROM files f JOIN folders d ON d.id=f.folder_id"
                             " WHERE f.dated=0 AND f.missing=0 AND d.online=1 ORDER BY f.mtime DESC LIMIT ?", (limit,)).fetchall()
        if not rows:
            return 0
        ffmpeg = find_ffmpeg(self.settings.get("ffmpeg") if self.settings else "")
        done, moved = [], 0
        for r in rows:
            if self._stop.is_set():
                break
            t = taken(os.path.join(r["path"], *r["rel"].split("/")), r["kind"], from_name(r["name"], r["mtime"]), ffmpeg)
            shot = t or r["mtime"]
            done.append((t, shot, r["id"]))
            moved += abs((r["shot"] or 0) - shot) > 1
        with self.db.write() as c:
            c.executemany("UPDATE files SET taken=?, shot=?, dated=1 WHERE id=?", done)
        self.moved += moved  # after they're saved: the grid redraws when this changes
        return len(done)

    def _work(self) -> None:
        while not self._stop.is_set():
            try:
                n = self.run_once()
            except Exception:
                n = 0
            if not n:
                self._wake.wait(5)
                self._wake.clear()
