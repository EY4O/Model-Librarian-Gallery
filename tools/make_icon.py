"""Draws the Model Librarian Gallery icon: two holographic photo cards, the back
one leaning like Model Librarian's last book, the front one showing Prompto's
red orb as a sun over ink hills, on the same white rounded tile with a lavender
hairline (EY4O/ComfyUI-Model-Librarian, tools/make-icon.py) so the tools match.

    python tools/make_icon.py      # docs/images/icon.png, mlgallery/ui/icon.png and icon.ico, and the phone app's icons
                                   # (192 and 512, a maskable 512 and Apple's 180: the artwork on a full white square)

Needs Pillow and NumPy. Everything is drawn on a 512 grid at 4x and scaled
down with LANCZOS. The small sizes (taskbar, title bar) use simplified artwork
rather than a plain shrink: no far hill, a bigger orb.
"""

import os

import numpy as np
from PIL import Image, ImageDraw

SS = 4                      # supersampling factor
G = 512 * SS                # drawing grid
INK = (16, 17, 23)
HOLO = [(0.0, "#f7dccf"), (0.3, "#f4a7c0"), (0.62, "#b9a3e3"), (1.0, "#8fcfe3")]
ORB = [(0.0, "#fff1f1"), (0.22, "#ffb3b3"), (0.6, "#f26d6d"), (1.0, "#c23b4a")]

FRONT = (80, 192, 368, 418)                     # the front card (left, top, right, bottom)
BACK = {"w": 288, "h": 226, "angle": -11, "cx": 278, "cy": 226}
RADIUS = 26
BACK_ALPHA = 150            # the back card is the same sheet, fainter
HILL_ALPHA = 115            # the far hill, as faint as Model Librarian's bands


def S(v):
    return v * SS


def rgb(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float32)


def ramp(t, stops):
    """Colour at t (array, 0..1) along gradient stops."""
    t = np.clip(t, 0, 1)
    out = np.zeros(t.shape + (3,), dtype=np.float32)
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        m = (t >= a) & (t <= b)
        k = ((t - a) / (b - a))[..., None]
        out[m] = (rgb(ca) * (1 - k) + rgb(cb) * k)[m]
    return out


def layer(color_fn, mask):
    """An RGBA layer: colours from color_fn(xx, yy), alpha from an L mask."""
    yy, xx = np.mgrid[0:G, 0:G].astype(np.float32)
    a = np.asarray(mask, dtype=np.float32)[..., None]
    return Image.fromarray(np.concatenate([color_fn(xx, yy), a], axis=2).astype(np.uint8), "RGBA")


def holo(xx, yy):
    """One diagonal sweep across both cards, like the books cut from one sheet."""
    x0, y0, x1, y1 = S(80), S(108), S(428), S(418)
    return ramp(((xx - x0) / (x1 - x0) + (yy - y0) / (y1 - y0)) / 2, HOLO)


def tile(img):
    ImageDraw.Draw(img).rounded_rectangle([S(16), S(16), S(496), S(496)], radius=S(108),
                                          fill=(255, 255, 255, 255), outline=(220, 219, 227, 255), width=S(10))


def back_card(img):
    w, h = BACK["w"], BACK["h"]
    card = Image.new("L", (S(w), S(h)), 0)
    ImageDraw.Draw(card).rounded_rectangle([0, 0, S(w), S(h)], radius=S(RADIUS), fill=BACK_ALPHA)
    card = card.rotate(BACK["angle"], expand=True, resample=Image.BICUBIC)
    mask = Image.new("L", (G, G), 0)
    mask.paste(card, (S(BACK["cx"]) - card.width // 2, S(BACK["cy"]) - card.height // 2))
    img.alpha_composite(layer(holo, mask))


def front_card(img):
    """The front card, with a white gap round it so it reads as sitting on top."""
    x0, y0, x1, y1 = FRONT
    gap = Image.new("L", (G, G), 0)
    ImageDraw.Draw(gap).rounded_rectangle([S(x0 - 12), S(y0 - 12), S(x1 + 12), S(y1 + 12)], radius=S(RADIUS + 12), fill=255)
    white = Image.new("RGBA", (G, G), (255, 255, 255, 0))
    white.putalpha(gap)
    img.alpha_composite(white)
    mask = Image.new("L", (G, G), 0)
    ImageDraw.Draw(mask).rounded_rectangle([S(x0), S(y0), S(x1), S(y1)], radius=S(RADIUS), fill=255)
    img.alpha_composite(layer(holo, mask))
    return mask


def hills(img, card_mask, small):
    """Ink hills along the bottom of the front card, clipped to its corners."""
    x0, y0, x1, y1 = FRONT
    over = Image.new("RGBA", (G, G), (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    if not small:
        d.polygon([(S(x) , S(y)) for x, y in [(x0 - 10, y1), (x0 - 10, 326), (180, 276), (310, y1)]], fill=INK + (HILL_ALPHA,))
    d.polygon([(S(x), S(y)) for x, y in [(192, y1), (294, 326), (x1 + 10, y1 - 66), (x1 + 10, y1)]], fill=INK + (255,))
    clip = Image.new("RGBA", (G, G), (0, 0, 0, 0))
    clip.paste(over, (0, 0), card_mask)
    img.alpha_composite(clip)


def orb(img, cx, cy, radius):
    X, Y, R = S(cx), S(cy), S(radius)
    mask = Image.new("L", (G, G), 0)
    ImageDraw.Draw(mask).ellipse([X - R, Y - R, X + R, Y + R], fill=255)
    # radial gradient: focus at (34%, 30%) of the orb's box, radius 75% of its width
    fx, fy, fr = X - R + 0.34 * 2 * R, Y - R + 0.30 * 2 * R, 0.75 * 2 * R
    img.alpha_composite(layer(lambda xx, yy: ramp(np.hypot(xx - fx, yy - fy) / fr, ORB), mask))


def icon(small=False, bare=False):
    img = Image.new("RGBA", (G, G), (0, 0, 0, 0))
    if not bare:
        tile(img)
    back_card(img)
    mask = front_card(img)
    hills(img, mask, small)
    orb(img, 296, 248, 40 if small else 34)
    return img


def full(scale=0.8):
    """The artwork on a full white square, shrunk to `scale` round the middle: a maskable icon (Android shapes it, and
    keeps what's inside the middle 80%) and Apple's touch icon (iOS rounds its corners itself)."""
    art = icon(bare=True).resize((int(G * scale), int(G * scale)), Image.LANCZOS)
    img = Image.new("RGBA", (G, G), (255, 255, 255, 255))
    img.alpha_composite(art, ((G - art.width) // 2, (G - art.height) // 2))
    return img


def save(img, size, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.resize((size, size), Image.LANCZOS).save(path, optimize=True)
    print("wrote", path)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    big, small = icon(), icon(small=True)
    save(big, 512, os.path.join(root, "docs", "images", "icon.png"))
    save(big, 256, os.path.join(root, "mlgallery", "ui", "icon.png"))
    # Windows picks the closest size; 16-32 get the simplified artwork.
    ico = os.path.join(root, "mlgallery", "ui", "icon.ico")
    frames = [small.resize((s, s), Image.LANCZOS) for s in (16, 24, 32)] + [big.resize((s, s), Image.LANCZOS) for s in (48, 64, 128, 256)]
    frames[-1].save(ico, sizes=[f.size for f in frames], append_images=frames[:-1])
    print("wrote", ico)
    ui = os.path.join(root, "mlgallery", "ui")
    save(big, 192, os.path.join(ui, "icon-192.png"))
    save(big, 512, os.path.join(ui, "icon-512.png"))
    square = full()
    save(square, 512, os.path.join(ui, "icon-maskable-512.png"))
    save(full(0.86).convert("RGB"), 180, os.path.join(ui, "apple-touch-icon.png"))


if __name__ == "__main__":
    main()
