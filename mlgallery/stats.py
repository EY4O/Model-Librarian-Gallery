"""Statistics: what you made over a time range, with what, and how.

One call gathers everything the Statistics screen shows. Every count is of files in the index (not missing,
on a drive that's there); a range is the last N days by when each file was made, in your local time.
"""
from __future__ import annotations

import os
import time

from .library import DAY

TOP = 12


def overview(lib, days: int | None = 30, models=None) -> dict:
    since = time.time() - days * 86400 if days else 0
    base = "FROM files f JOIN folders d ON d.id=f.folder_id WHERE f.missing=0 AND d.online=1 AND f.mtime >= ?"
    a = [since]
    out: dict = {"days": days}
    with lib.db.read() as c:
        t = c.execute(f"SELECT COUNT(*) n, SUM(f.kind='image') images, SUM(f.kind='video') videos, COALESCE(SUM(f.size), 0) size,"
                      f" SUM(f.meta=1) with_data {base}", a).fetchone()
        out["totals"] = {k: t[k] or 0 for k in ("n", "images", "videos", "size", "with_data")}
        # the timeline: per day for a short range, per week (from Monday) for a longer one
        week = not days or days > 62
        bucket = f"date(f.mtime, 'unixepoch', 'localtime', 'weekday 0', '-6 days')" if week else DAY
        out["timeline"] = {"by": "week" if week else "day", "points": [dict(r) for r in c.execute(
            f"SELECT {bucket} AS at, SUM(f.kind='image') images, SUM(f.kind='video') videos {base} GROUP BY at ORDER BY at", a)]}
        uses = f"FROM uses u JOIN files f ON f.id=u.file_id JOIN folders d ON d.id=f.folder_id WHERE f.missing=0 AND d.online=1 AND f.mtime >= ?"
        out["checkpoints"] = [dict(r) for r in c.execute(
            f"SELECT u.name, COUNT(DISTINCT u.file_id) n, MAX(f.mtime) last {uses} AND u.kind IN ('checkpoint', 'unet')"
            f" GROUP BY u.name COLLATE NOCASE ORDER BY n DESC LIMIT ?", (*a, TOP))]
        out["loras"] = [dict(r) for r in c.execute(
            f"SELECT u.name, COUNT(DISTINCT u.file_id) n, AVG(u.strength) strength, MAX(f.mtime) last {uses} AND u.kind='lora'"
            f" GROUP BY u.name COLLATE NOCASE ORDER BY n DESC LIMIT ?", (*a, TOP))]
        out["models_used"] = c.execute(f"SELECT COUNT(DISTINCT lower(u.name)) {uses} AND u.kind IN ('checkpoint', 'unet')", a).fetchone()[0]
        out["loras_used"] = c.execute(f"SELECT COUNT(DISTINCT lower(u.name)) {uses} AND u.kind='lora'", a).fetchone()[0]
        out["prompt_tags"] = [dict(r) for r in c.execute(
            f"SELECT p.tag AS name, COUNT(DISTINCT p.file_id) n FROM prompt_tags p JOIN files f ON f.id=p.file_id JOIN folders d ON d.id=f.folder_id"
            f" WHERE f.missing=0 AND d.online=1 AND f.mtime >= ? GROUP BY p.tag ORDER BY n DESC LIMIT ?", (*a, 15))]
        gen = f"FROM generations g JOIN files f ON f.id=g.file_id JOIN folders d ON d.id=f.folder_id WHERE f.missing=0 AND d.online=1 AND f.mtime >= ?"
        out["settings"] = {}
        for col in ("sampler", "scheduler", "steps", "cfg"):
            out["settings"][col] = [{"name": r[0], "n": r[1]} for r in c.execute(
                f"SELECT g.{col}, COUNT(*) n {gen} AND g.{col} IS NOT NULL GROUP BY g.{col} ORDER BY n DESC LIMIT 8", a)]
        out["settings"]["size"] = [{"name": f"{r[0]}x{r[1]}", "n": r[2]} for r in c.execute(
            f"SELECT f.width, f.height, COUNT(*) n {base} AND f.width IS NOT NULL GROUP BY f.width, f.height ORDER BY n DESC LIMIT 8", a)]
        out["folders"] = [dict(r) for r in c.execute(
            f"SELECT d.id, d.path, COUNT(*) n, COALESCE(SUM(f.size), 0) size {base} GROUP BY d.id ORDER BY size DESC", a)]
        # storage of everything indexed, per drive (not limited to the range: it's what's on disk now)
        drives: dict[str, dict] = {}
        for r in c.execute("SELECT d.path, COUNT(*) n, COALESCE(SUM(f.size), 0) size FROM files f JOIN folders d ON d.id=f.folder_id"
                           " WHERE f.missing=0 GROUP BY d.id"):
            drive = os.path.splitdrive(r["path"])[0] or "/"
            x = drives.setdefault(drive, {"drive": drive, "n": 0, "size": 0})
            x["n"] += r["n"]
            x["size"] += r["size"]
        out["drives"] = sorted(drives.values(), key=lambda x: -x["size"])
        used = {r[0].lower() for r in c.execute("SELECT DISTINCT name FROM uses")}
    # models you have that no image in the library was made with (from Model Librarian's library)
    if models is not None:
        have = models.models()
        out["library"] = {"found": bool(have["data_dir"]), "root": have["root"], "total": len(have["models"])}
        unused = [m for m in have["models"] if m["name"].lower() not in used]
        unused.sort(key=lambda m: (m["kind"] != "checkpoint", m["kind"] != "unet", -(m["size"] or 0)))
        out["library"]["unused_count"] = {k: sum(1 for m in unused if m["kind"] == k) for k in ("checkpoint", "unet", "lora")}
        out["library"]["unused_size"] = sum(m["size"] or 0 for m in unused)
        out["library"]["unused"] = unused[:60]
    return out
