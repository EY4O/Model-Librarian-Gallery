"""Thumbnails: WebP files in the data folder, named by the file's fingerprint (a moved file keeps its
thumbnail; two copies of one picture share one). The short side is the size asked for, so they fill square
tiles without being stretched. Videos get a poster frame from ffmpeg when it's there.

Made in the background, newest first (what you see first), and on demand for a tile that's on screen.
"""
from __future__ import annotations

import io
import math
import os
import shutil
import subprocess
import threading
import time

BG = (27, 28, 33)  # transparent parts are shown on the grid's own colour
MAX_RATIO = 4      # a very long image is cropped to 4:1 around its middle, not shrunk to a sliver


_N, _K = 32, 8
_COS = [[math.cos((2 * x + 1) * u * math.pi / (2 * _N)) for x in range(_N)] for u in range(_K)]


def phash(im) -> str:
    """A 64-bit fingerprint of what a picture looks like (perceptual hash: the lowest 8x8 frequencies of a 32x32
    grey copy, each above or below their median). The same picture saved again - another format, size or quality -
    lands within a few bits; different pictures are far apart. The DCT is done in two passes (rows, then columns)."""
    from PIL import Image
    g = im.convert("L").resize((_N, _N), Image.LANCZOS)
    px = g.tobytes()  # one byte per pixel
    rows = [[sum(_COS[u][x] * px[y * _N + x] for x in range(_N)) for u in range(_K)] for y in range(_N)]
    coef = [sum(_COS[v][y] * rows[y][u] for y in range(_N)) for v in range(_K) for u in range(_K)]
    med = sorted(coef[1:])[(len(coef) - 1) // 2]  # the first (the average brightness) left out of the median
    bits = 0
    for c in coef:
        bits = (bits << 1) | (c > med)
    return f"{bits:016x}"


def cache_path(thumb_dir: str, fp: str, size: int) -> str:
    return os.path.join(thumb_dir, fp[:2], f"{fp}_{size}.webp")


def _fit(im, size: int):
    from PIL import Image
    w, h = im.size
    short = min(w, h)
    if short <= 0:
        raise ValueError("empty image")
    scale = min(1.0, size / short)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    im = im.resize((nw, nh), Image.LANCZOS) if scale < 1 else im
    # a panorama or a long strip: the middle part, at most MAX_RATIO long
    if nw > nh * MAX_RATIO:
        x = (nw - nh * MAX_RATIO) // 2
        im = im.crop((x, 0, x + nh * MAX_RATIO, nh))
    elif nh > nw * MAX_RATIO:
        y = (nh - nw * MAX_RATIO) // 2
        im = im.crop((0, y, nw, y + nw * MAX_RATIO))
    return im


def _flatten(im):
    from PIL import Image
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, BG)
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB")


def image_thumb(path: str, size: int) -> bytes:
    from PIL import Image, ImageOps
    with Image.open(path) as im:
        im.seek(0)  # an animation: its first frame
        im.draft("RGB", (size * 2, size * 2))  # JPEG: decode at a smaller scale (much faster)
        im = ImageOps.exif_transpose(im)
        im = _fit(_flatten(im), size)
    out = io.BytesIO()
    im.save(out, "WEBP", quality=80, method=4)
    return out.getvalue()


def find_ffmpeg(configured: str = "") -> str | None:
    if configured and os.path.isfile(configured):
        return configured
    return shutil.which("ffmpeg")


def video_probe(ffmpeg: str, path: str) -> dict:
    """Width, height and duration from ffprobe (next to ffmpeg), or {} without it."""
    probe = os.path.join(os.path.dirname(ffmpeg), "ffprobe" + (".exe" if os.name == "nt" else ""))
    if not os.path.isfile(probe):
        probe = shutil.which("ffprobe")
    if not probe:
        return {}
    try:
        r = subprocess.run([probe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
                            "-of", "default=noprint_wrappers=1", path], capture_output=True, timeout=30, **_hidden())
        out = dict(line.split("=", 1) for line in r.stdout.decode(errors="replace").splitlines() if "=" in line)
        return {"width": int(out["width"]) if out.get("width", "").isdigit() else None,
                "height": int(out["height"]) if out.get("height", "").isdigit() else None,
                "duration": float(out["duration"]) if _num(out.get("duration")) else None}
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}


def _num(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _hidden() -> dict:
    """No console window flashing up for each video (Windows)."""
    return {"creationflags": 0x08000000} if os.name == "nt" else {}


def video_thumb(ffmpeg: str, path: str, size: int, at: float = 1.0) -> bytes:
    """A frame `at` seconds in (or the first one for a very short clip), as a WebP thumbnail."""
    from PIL import Image
    for t in (at, 0.0):
        r = subprocess.run([ffmpeg, "-v", "error", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                            "-vf", f"scale='if(lt(iw,ih),{size},-2)':'if(lt(iw,ih),-2,{size})'", "-f", "image2pipe", "-vcodec", "png", "-"],
                           capture_output=True, timeout=60, **_hidden())
        if r.returncode == 0 and r.stdout:
            with Image.open(io.BytesIO(r.stdout)) as im:
                im = _fit(_flatten(im), size)
            out = io.BytesIO()
            im.save(out, "WEBP", quality=80, method=4)
            return out.getvalue()
    raise ValueError("ffmpeg made no frame")


class Thumbnails:
    """Makes thumbnails in the background (a few at a time, newest files first) and on demand."""

    def __init__(self, db, thumb_dir: str, settings):
        self.db, self.dir, self.settings = db, thumb_dir, settings
        self._claimed: set[int] = set()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.made = 0
        self.failed = 0

    # ---------------------------------------------------------------- one file
    def make(self, row) -> int:
        """Make the thumbnail for a files row. -> its thumb state (1 made, -1 failed, -2 a video without ffmpeg)."""
        size = int(self.settings.get("thumb_size"))
        dest = cache_path(self.dir, row["fp"], size)
        if os.path.isfile(dest):
            return 1
        full = os.path.join(row["path"], row["rel"])
        extra = {}
        try:
            if row["kind"] == "video":
                ffmpeg = find_ffmpeg(self.settings.get("ffmpeg"))
                if not ffmpeg:
                    return -2
                extra = video_probe(ffmpeg, full)
                dur = extra.get("duration") or 0
                data = video_thumb(ffmpeg, full, size, at=min(1.0, dur / 3) if dur else 1.0)
            else:
                data = image_thumb(full, size)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            tmp = dest + f".{threading.get_ident()}.tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, dest)
        except Exception:
            return -1
        finally:
            if extra:
                with self.db.write() as c:
                    c.execute("UPDATE files SET width=COALESCE(?, width), height=COALESCE(?, height), duration=COALESCE(?, duration) WHERE id=?",
                              (extra.get("width"), extra.get("height"), extra.get("duration"), row["id"]))
        return 1

    def get(self, file_id: int) -> str | None:
        """The thumbnail file for a file id, made now if it isn't yet. None if it can't be made
        (one that failed before isn't tried again on every scroll: a rescan of a changed file resets it)."""
        row = self._row(file_id)
        if not row or not row["fp"] or row["thumb"] < 0:
            return None
        size = int(self.settings.get("thumb_size"))
        dest = cache_path(self.dir, row["fp"], size)
        if os.path.isfile(dest):
            if row["thumb"] != 1:  # another file (a copy) made it: this one has it too now
                self._record([(1, row["id"])])
            return dest
        state = self.make(row)
        self._record([(state, row["id"])])
        return dest if state == 1 else None

    def _row(self, file_id: int):
        with self.db.read() as c:
            return c.execute("SELECT f.id, f.rel, f.kind, f.fp, f.thumb, d.path FROM files f JOIN folders d ON d.id=f.folder_id WHERE f.id=?",
                             (file_id,)).fetchone()

    def _record(self, results: list[tuple[int, int]]) -> None:
        if not results:
            return
        with self.db.write() as c:
            c.executemany("UPDATE files SET thumb=? WHERE id=?", results)
        self._fingerprint([fid for state, fid in results if state == 1])
        for state, _ in results:
            if state == 1:
                self.made += 1
            elif state == -1:
                self.failed += 1

    # ---------------------------------------------------------------- the background
    def start(self) -> None:
        if find_ffmpeg(self.settings.get("ffmpeg")):  # ffmpeg installed since: the videos waiting for it get their posters
            with self.db.write() as c:
                c.execute("UPDATE files SET thumb=0 WHERE thumb=-2")
        n = max(1, int(self.settings.get("thumb_workers")))
        for i in range(n):
            t = threading.Thread(target=self._work, name=f"thumbs-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def nudge(self) -> None:
        """New files are in the index: get on with them."""
        self._wake.set()

    def _next(self, limit: int = 16) -> list:
        with self._lock:
            with self.db.read() as c:
                rows = c.execute("SELECT f.id, f.rel, f.kind, f.fp, d.path FROM files f JOIN folders d ON d.id=f.folder_id"
                                 " WHERE f.thumb=0 AND f.missing=0 AND d.online=1 AND f.fp IS NOT NULL ORDER BY f.mtime DESC LIMIT ?",
                                 (limit + len(self._claimed),)).fetchall()
            mine = [r for r in rows if r["id"] not in self._claimed][:limit]
            self._claimed.update(r["id"] for r in mine)
            return mine

    def _work(self) -> None:
        while not self._stop.is_set():
            rows = self._next()
            if not rows:
                self._wake.wait(5)
                self._wake.clear()
                continue
            results = []
            for r in rows:
                if self._stop.is_set():
                    break
                results.append((self.make(r), r["id"]))
                if len(results) >= 8:
                    self._record(results)
                    results = []
            self._record(results)
            with self._lock:
                self._claimed.difference_update(r["id"] for r in rows)
            time.sleep(0)

    def _fingerprint(self, ids: list[int]) -> None:
        """The picture fingerprint of files whose thumbnail exists (read from the small thumbnail: quick)."""
        if not ids:
            return
        from PIL import Image
        size = int(self.settings.get("thumb_size"))
        with self.db.read() as c:
            rows = c.execute(f"SELECT id, fp FROM files WHERE id IN ({','.join('?' * len(ids))}) AND phash IS NULL", ids).fetchall()
        out = []
        for r in rows:
            try:
                with Image.open(cache_path(self.dir, r["fp"], size)) as im:
                    out.append((phash(im), r["id"]))
            except (OSError, ValueError):
                continue
        if out:
            with self.db.write() as c:
                c.executemany("UPDATE files SET phash=? WHERE id=?", out)

    def backfill_fingerprints(self) -> None:
        """Fingerprints for files that had thumbnails before fingerprints existed."""
        while not self._stop.is_set():
            with self.db.read() as c:
                ids = [r[0] for r in c.execute("SELECT id FROM files WHERE thumb=1 AND phash IS NULL AND missing=0 LIMIT 200")]
            if not ids:
                return
            before = len(ids)
            self._fingerprint(ids)
            with self.db.read() as c:
                left = c.execute(f"SELECT COUNT(*) FROM files WHERE id IN ({','.join('?' * len(ids))}) AND phash IS NULL", ids).fetchone()[0]
            if left == before:  # thumbnails gone from the cache: they'll get one when the thumbnail is made again
                with self.db.write() as c:
                    c.executemany("UPDATE files SET thumb=0 WHERE id=?", [(i,) for i in ids])
                return

    def pending(self) -> int:
        with self.db.read() as c:
            return c.execute("SELECT COUNT(*) FROM files f JOIN folders d ON d.id=f.folder_id WHERE f.thumb=0 AND f.missing=0 AND d.online=1").fetchone()[0]
