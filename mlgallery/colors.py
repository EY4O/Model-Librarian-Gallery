"""Colour sets: each picture's main colours and how much of it each covers, like Eagle's.

Worked out from the file's small thumbnail (median cut to a few colours, near-twins merged), in the background
next to the picture fingerprint; a file brought in from Eagle starts with the colours Eagle found. Each colour is
kept with its CIE Lab value (to find pictures with a colour that looks alike) and a family (red, orange, ...,
black, gray, white: for `color:red`, and for Statistics).

    color:#140d17     a colour that looks like it, covering at least 5% of the picture
    color:red         a colour of that family covering at least 10%
"""
from __future__ import annotations

import colorsys
import math
import re

MAX = 8           # colours kept per picture
MIN_RATIO = 0.01  # less than this isn't one of a picture's colours
MERGE = 8.0       # colours closer than this (Lab distance) are one
NEAR = 22.0       # color:#hex finds colours within this (Lab distance: clearly the same colour, a little lighter or darker)
HEX_MIN, FAMILY_MIN = 0.05, 0.10

# each family's own swatch (Statistics, the search suggestions)
FAMILIES = {"red": "#e53935", "orange": "#fb8c00", "yellow": "#fdd835", "green": "#43a047", "cyan": "#00acc1", "blue": "#1e88e5",
            "purple": "#8e24aa", "pink": "#ec407a", "brown": "#795548", "black": "#212121", "gray": "#9e9e9e", "white": "#f5f5f5"}


def hex_of(rgb) -> str:
    return "#%02x%02x%02x" % tuple(int(v) for v in rgb[:3])


def rgb_of(text: str) -> tuple[int, int, int] | None:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})", str(text or "").strip())
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def lab(rgb) -> tuple[float, float, float]:
    """sRGB (0-255) -> CIE Lab (D65)."""
    def lin(v):
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(v) for v in rgb[:3])
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def distance(l1, l2) -> float:
    return math.dist(l1, l2)


CELL = 12.0  # the Lab grid's cube size


def _cells(lv: float) -> int:
    return int(lv // CELL)


def cell(lb) -> int:
    """A Lab colour's cube in the grid, as one number (indexed: finding a colour looks in a few cubes, not everywhere)."""
    li, ai, bi = _cells(lb[0]), _cells(lb[1] + 128), _cells(lb[2] + 128)
    return (li * 32 + ai) * 32 + bi


def cells_near(lb, r: float) -> list[int]:
    """Every cube that holds colours within r of lb."""
    rng = lambda v: range(_cells(v - r), _cells(v + r) + 1)  # noqa: E731
    return [(li * 32 + ai) * 32 + bi for li in rng(lb[0]) for ai in rng(lb[1] + 128) for bi in rng(lb[2] + 128)]


def family(rgb) -> str:
    """The colour's everyday name: red, orange, yellow, green, cyan, blue, purple, pink, brown, black, gray, white."""
    h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb[:3]))
    if v < 0.16:
        return "black"
    if s < 0.16 or (s < 0.25 and v < 0.35):
        return "white" if v > 0.88 else "black" if v < 0.24 else "gray"
    deg = h * 360
    if 12 <= deg < 45 and v < 0.62:
        return "brown"
    if deg < 12 or deg >= 345:
        return "red"
    if deg < 42:
        return "orange"
    if deg < 68:
        return "yellow"
    if deg < 165:
        return "green"
    if deg < 195:
        return "cyan"
    if deg < 255:
        return "blue"
    if deg < 290:
        return "purple"
    return "pink"


def palette(im) -> list[tuple[str, float]]:
    """A picture's main colours -> [(#rrggbb, share 0..1)], the biggest first."""
    from PIL import Image
    if im.mode in ("RGBA", "LA", "P", "PA"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        im = bg
    im = im.convert("RGB")
    w, h = im.size
    k = max(w, h) / 96
    if k > 1:  # sampled, not blended: smoothing would make up colours along every edge
        im = im.resize((max(1, round(w / k)), max(1, round(h / k))), Image.NEAREST)
    q = im.quantize(colors=16, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette() or []
    counts = sorted(q.getcolors(16 * 16) or [], reverse=True)
    total = sum(c for c, _ in counts) or 1
    out: list[list] = []  # [rgb, lab, count]
    for n, i in counts:
        rgb = tuple(pal[3 * i:3 * i + 3])
        lb = lab(rgb)
        twin = next((o for o in out if distance(o[1], lb) < MERGE), None)
        if twin:  # near-twins are one colour, where most of it is
            twin[2] += n
            continue
        out.append([rgb, lb, n])
    out.sort(key=lambda o: -o[2])
    return [(hex_of(o[0]), round(o[2] / total, 4)) for o in out if o[2] / total >= MIN_RATIO][:MAX]


def from_eagle(palettes) -> list[tuple[str, float]]:
    """Eagle's own palette ([{color: [r, g, b], ratio: percent}]) in the same form."""
    out = []
    for p in palettes or []:
        try:
            rgb, ratio = p["color"], float(p["ratio"]) / 100
            if len(rgb) >= 3 and ratio >= MIN_RATIO:
                out.append((hex_of(rgb), round(ratio, 4)))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out, key=lambda o: -o[1])[:MAX]


def store(c, file_id: int, pal: list[tuple[str, float]]) -> None:
    """A file's colour set (replacing any it had); an empty one still marks it done."""
    c.execute("DELETE FROM colors WHERE file_id=?", (file_id,))
    rows = []
    for rank, (hx, ratio) in enumerate(pal):
        rgb = rgb_of(hx)
        if rgb:
            lb = lab(rgb)
            rows.append((file_id, rank, hx, ratio, round(lb[0], 2), round(lb[1], 2), round(lb[2], 2), family(rgb), cell(lb)))
    c.executemany("INSERT INTO colors (file_id, rank, hex, ratio, l, a, b, family, cell) VALUES (?,?,?,?,?,?,?,?,?)", rows)
    c.execute("UPDATE files SET colors=1 WHERE id=?", (file_id,))


def of_file(c, file_id: int) -> list[dict]:
    return [{"hex": r[0], "ratio": r[1], "family": r[2]} for r in c.execute("SELECT hex, ratio, family FROM colors WHERE file_id=? ORDER BY rank", (file_id,))]


def condition(value: str) -> tuple[str, list] | None:
    """The search condition for color:<value> (on files f), or None when it's neither a colour nor a family."""
    v = str(value or "").strip().lower()
    if v in ("grey",):
        v = "gray"
    if v in FAMILIES:
        return "f.id IN (SELECT file_id FROM colors WHERE family = ? AND ratio >= ?)", [v, FAMILY_MIN]
    rgb = rgb_of(v)
    if not rgb:
        return None
    lv, av, bv = lb = lab(rgb)
    near = cells_near(lb, NEAR)
    return (f"f.id IN (SELECT file_id FROM colors WHERE cell IN ({','.join('?' * len(near))}) AND ratio >= ?"
            " AND (l-?)*(l-?) + (a-?)*(a-?) + (b-?)*(b-?) <= ?)",
            [*near, HEX_MIN, lv, lv, av, av, bv, bv, NEAR * NEAR])
