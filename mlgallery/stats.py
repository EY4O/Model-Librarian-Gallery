"""Statistics: what you made over a time range, with what, and how.

One call gathers everything the Statistics screen shows. Every count is of files in the index (not missing,
on a drive that's there); a range is the last N days by when each file was made, in your local time. In a library
for more than one kind, `parts` keeps it to some of them: ai, photo, illustration (library.FILE_IS).
"""
from __future__ import annotations

import os
import time

from . import families
from .library import DAY

TOP = 12
SERIES = 7  # families shown in the timeline; the rest are "Other families"


def parts_of(parts) -> tuple[str, ...]:
    """The kinds of file asked for, in one order (none or all of them: every file)."""
    from .library import FILE_IS
    got = tuple(k for k in FILE_IS if k in set(parts or ()))
    return () if len(got) == len(FILE_IS) else got


def overview(lib, days: int | None = 30, models=None, *, fresh: bool = False, parts=None) -> dict:
    """Counting up a library of 250,000 takes a few seconds, so the last count is shown at once and a new one made
    in the background when the index has changed (`fresh` waits for an up-to-date one)."""
    parts = parts_of(parts)
    key = ("stats", days, models is not None and models.data_dir(), parts, time.strftime("%Y-%m-%d"))  # (a new day: counted again)
    if fresh:
        return lib.db.cached(key, lambda: _overview(lib, days, models, parts))
    out, is_fresh = lib.db.cached_lazy(key, lambda: _overview(lib, days, models, parts))
    return {**out, "stale": not is_fresh}


def _overview(lib, days: int | None = 30, models=None, parts: tuple[str, ...] = ()) -> dict:
    from .library import FILE_IS
    since = _midnight(days - 1) if days else 0  # (the last N days: today and the N - 1 before it, each whole)
    which = f" AND ({' OR '.join(f'({FILE_IS[k]})' for k in parts)})" if parts else ""  # (only some kinds of file)
    scope = f"f.missing=0 AND d.online=1 AND f.shot >= ?{which}"
    base = f"FROM files f JOIN folders d ON d.id=f.folder_id WHERE {scope}"
    a = [since]
    out: dict = {"days": days, "parts": list(parts)}
    with lib.db.read() as c:
        t = c.execute(f"SELECT COUNT(*) n, SUM(f.kind='image') images, SUM(f.kind='video') videos, COALESCE(SUM(f.size), 0) size,"
                      f" SUM(f.meta=1) with_data, SUM({FILE_IS['photo']}) photos, SUM({FILE_IS['illustration']}) illustrations {base}", a).fetchone()
        out["totals"] = {k: t[k] or 0 for k in ("n", "images", "videos", "size", "with_data", "photos", "illustrations")}
        # the timeline: per day for a short range, per week (from Monday) for a longer one
        week = not days or days > 62
        bucket = f"date(f.shot, 'unixepoch', 'localtime', 'weekday 0', '-6 days')" if week else DAY
        out["timeline"] = {"by": "week" if week else "day", "points": [dict(r) for r in c.execute(
            f"SELECT {bucket} AS at, SUM(f.kind='image') images, SUM(f.kind='video') videos {base} GROUP BY at ORDER BY at", a)]}
        # what it was made with: each file under its checkpoint's family (the model stored with what made it)
        made_with = c.execute(
            f"SELECT {bucket} AS at, g.model AS model, COUNT(*) n FROM files f JOIN folders d ON d.id=f.folder_id LEFT JOIN generations g ON g.file_id=f.id"
            f" WHERE {scope} GROUP BY at, g.model ORDER BY at", a).fetchall()
        model_files = {r[0]: r[1] for r in c.execute("SELECT name, MIN(file) FROM uses WHERE kind IN ('checkpoint', 'unet') GROUP BY name")}
        uses = f"FROM uses u JOIN files f ON f.id=u.file_id JOIN folders d ON d.id=f.folder_id WHERE {scope}"
        out["checkpoints"] = [dict(r) for r in c.execute(
            f"SELECT u.name, COUNT(DISTINCT u.file_id) n, MAX(f.shot) last {uses} AND u.kind IN ('checkpoint', 'unet')"
            f" GROUP BY u.name COLLATE NOCASE ORDER BY n DESC LIMIT ?", (*a, TOP))]
        out["loras"] = [dict(r) for r in c.execute(
            f"SELECT u.name, COUNT(DISTINCT u.file_id) n, AVG(u.strength) strength, MAX(f.shot) last {uses} AND u.kind='lora'"
            f" GROUP BY u.name COLLATE NOCASE ORDER BY n DESC LIMIT ?", (*a, TOP))]
        out["models_used"] = c.execute(f"SELECT COUNT(DISTINCT lower(u.name)) {uses} AND u.kind IN ('checkpoint', 'unet')", a).fetchone()[0]
        out["loras_used"] = c.execute(f"SELECT COUNT(DISTINCT lower(u.name)) {uses} AND u.kind='lora'", a).fetchone()[0]
        out["characters"] = [dict(r) for r in c.execute(
            "SELECT ch.id, ch.name, COUNT(*) n FROM file_characters fc JOIN characters ch ON ch.id=fc.character_id JOIN files f ON f.id=fc.file_id"
            f" JOIN folders d ON d.id=f.folder_id WHERE fc.state IN ('auto','confirmed') AND {scope}"
            " GROUP BY ch.id ORDER BY n DESC LIMIT ?", (*a, TOP))]
        out["prompt_tags"] = [dict(r) for r in c.execute(  # a file's parts are stored once each: COUNT(*) counts files
            f"SELECT p.tag AS name, COUNT(*) n FROM prompt_tags p JOIN files f ON f.id=p.file_id JOIN folders d ON d.id=f.folder_id"
            f" WHERE {scope} GROUP BY p.tag ORDER BY n DESC LIMIT ?", (*a, 15))]
        gen = f"FROM generations g JOIN files f ON f.id=g.file_id JOIN folders d ON d.id=f.folder_id WHERE {scope}"
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
        # colours: how much of the pictures each colour family covers (each picture counts once in all)
        col_rows = c.execute(f"SELECT {bucket} AS at, k.family, SUM(k.ratio) s FROM colors k JOIN files f ON f.id=k.file_id JOIN folders d ON d.id=f.folder_id"
                             f" WHERE {scope} GROUP BY at, k.family ORDER BY at", a).fetchall()
    resolve = families.Resolver(models)
    fam_of = {}
    totals: dict[str, int] = {}
    rows = []
    for r in made_with:
        name = r["model"] or ""
        fam = (resolve.family(name, model_files.get(name)) or "Unknown family") if name else "No model recorded"
        fam_of[name] = fam
        totals[fam] = totals.get(fam, 0) + r["n"]
        rows.append((r["at"], fam, r["n"]))
    order = [f for f, _ in sorted(totals.items(), key=lambda x: -x[1]) if f not in ("No model recorded", "Unknown family")]
    shown = order[:SERIES]
    folded = "Other families"
    series = shown + ([folded] if len(order) > SERIES or "Unknown family" in totals else []) + (["No model recorded"] if "No model recorded" in totals else [])
    points: dict[str, dict] = {}
    for at, fam, n in rows:
        key = fam if fam in shown or fam == "No model recorded" else folded
        pt = points.setdefault(at, {"at": at})
        pt[key] = pt.get(key, 0) + n
    out["made_with"] = {"by": out["timeline"]["by"], "series": [{"name": f, "color": families.color(f) if f in shown else ("#707070" if f == folded else "#4b4b4b"),
                                                                   "n": totals.get(f, 0) if f != folded else sum(v for k, v in totals.items() if k not in shown and k != "No model recorded")}
                                                                  for f in series],
                        "points": [points[k] for k in sorted(points)]}
    out["colors"] = _colors(col_rows, out["timeline"]["by"])
    for row in out["checkpoints"]:
        row["family"] = resolve.family(row["name"])
        row["color"] = families.color(row["family"]) if row["family"] else None
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


def _midnight(days_back: int) -> float:
    """The start of the day `days_back` days before today, in local time."""
    import datetime
    d = datetime.date.today() - datetime.timedelta(days=max(0, days_back))
    return time.mktime((d.year, d.month, d.day, 0, 0, 0, 0, 0, -1))


def _colors(rows, by: str) -> dict:
    """The colour families over time, each day (or week) as shares of 100, and over the whole range."""
    from . import colors
    totals: dict[str, float] = {}
    points: dict[str, dict] = {}
    for r in rows:
        totals[r["family"]] = totals.get(r["family"], 0) + r["s"]
        pt = points.setdefault(r["at"], {"at": r["at"]})
        pt[r["family"]] = pt.get(r["family"], 0) + r["s"]
    for pt in points.values():
        t = sum(v for k, v in pt.items() if k != "at") or 1
        for k in [k for k in pt if k != "at"]:
            pt[k] = round(100 * pt[k] / t, 1)
    whole = sum(totals.values()) or 1
    return {"by": by, "series": [{"name": f, "color": colors.FAMILIES.get(f, "#707070"), "n": round(100 * v / whole, 1)}
                                 for f, v in sorted(totals.items(), key=lambda x: -x[1])],
            "points": [points[k] for k in sorted(points)]}
