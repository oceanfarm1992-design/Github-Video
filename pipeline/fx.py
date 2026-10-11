"""Visual effects for the motion engine: everything here is deterministic (seeded), so a re-render of the
same script gives the same video.

Backdrop    - 2.5D parallax: a drifting grid (far) and glowing particles (near) over a graded gradient
              with film grain; both layers move periodically, so the background loops without a jump
Transitions - whip-pan with directional motion blur, diagonal panel wipe, circle wipe with a light ring
Camera      - spring easing (small overshoot) and damped procedural shake on impact frames
Finishing   - colour grade for captured images, vignette, light sweep across cards, draw-on outlines
"""
import math
import re
from functools import lru_cache

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter

W, H = 1080, 1920
GOLD = (255, 214, 102)
ACCENT = (90, 160, 255)


# ------------------------------------------------------------------ easing
def ease_in_out(t):
    t = max(0.0, min(1.0, t))
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def spring(t):
    """0 -> 1 with one soft overshoot (~8%), settled by ~0.6 s."""
    if t <= 0:
        return 0.0
    return 1 - math.exp(-8 * t) * math.cos(10 * t)


def shake(t, amp, seed=0):
    """Damped camera offset (dx, dy) in px, t seconds after an impact; (0, 0) once it has died out."""
    if t < 0 or t > 0.5:
        return 0, 0
    d = amp * math.exp(-t * 9)
    return round(d * math.sin(t * 60 + seed)), round(0.6 * d * math.sin(t * 47 + seed * 2.1))


# ---------------------------------------------------------------- backdrop
class Backdrop:
    """Graded gradient + grain + grid (drifts diagonally, slow) + soft particles (rise, faster = nearer)."""
    CELL = 90
    GRID_SPEED = 0.6  # px per frame

    def __init__(self, top=(14, 16, 28), bottom=(44, 48, 72), seed=7):
        rng = np.random.default_rng(seed)
        c = self.CELL
        h, w = H + c, W + c
        f = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
        img = np.array(top, np.float32) * (1 - f) + np.array(bottom, np.float32) * f
        img = np.repeat(img, w, axis=1)
        lines = np.zeros((h, w), np.float32)
        lines[::c, :] = 7
        lines[:, ::c] = 7
        lines[::c, ::c] = 22  # brighter grid crossings
        img += lines[:, :, None] * np.array([0.8, 0.9, 1.2], np.float32)
        img += rng.normal(0, 2.4, (h, w, 1)).astype(np.float32)  # film grain (background only)
        self.big = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
        self.dots = [(rng.uniform(0, W), rng.uniform(0, H), rng.uniform(0.25, 1.0)) for _ in range(22)]
        self.sprites = {}

    def _sprite(self, depth):
        key = round(depth, 1)
        if key not in self.sprites:
            r = int(4 + 9 * key)
            spr = Image.new("RGBA", (r * 6, r * 6), (0, 0, 0, 0))
            ImageDraw.Draw(spr).ellipse([r * 2, r * 2, r * 4, r * 4], fill=ACCENT + (int(40 + 70 * key),))
            self.sprites[key] = spr.filter(ImageFilter.GaussianBlur(r * 0.9))
        return self.sprites[key]

    def frame(self, fi):
        off = int(fi * self.GRID_SPEED) % self.CELL
        img = self.big.crop((off, off, off + W, off + H))
        for x0, y0, depth in self.dots:
            spr = self._sprite(depth)
            span = H + spr.height
            y = (y0 - fi * 1.6 * depth) % span - spr.height  # nearer particles move faster (parallax)
            x = x0 + 18 * math.sin(fi / 90 + x0)
            img.paste(spr, (int(x - spr.width / 2), int(y)), spr)
        return img


# --------------------------------------------------------------- finishing
def grade(img):
    """One look for every captured screenshot and card: a touch more contrast and colour."""
    return ImageEnhance.Color(ImageEnhance.Contrast(img).enhance(1.06)).enhance(1.08)


@lru_cache(maxsize=1)
def _vignette():
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    d = np.sqrt(((x - W / 2) / (W / 2)) ** 2 + ((y - H / 2) / (H / 2)) ** 2) / math.sqrt(2)
    v = 255 * (1 - 0.30 * np.clip((d - 0.45) / 0.55, 0, 1) ** 1.6)
    return Image.fromarray(np.repeat(v[:, :, None], 3, axis=2).astype(np.uint8))


def vignette(img):
    return ImageChops.multiply(img, _vignette())


@lru_cache(maxsize=8)
def _sweep_band(w, h):
    """A soft diagonal highlight, 3 widths wide, cropped per frame as it slides across."""
    band = np.zeros(3 * w, np.float32)
    x = np.arange(3 * w)
    band += np.exp(-((x - 1.5 * w) / (w * 0.09)) ** 2)
    m = Image.fromarray((band[None, :] * 255).repeat(h, axis=0).astype(np.uint8))
    return m.transform(m.size, Image.AFFINE, (1, 0.35, -0.35 * h / 2, 0, 1, 0), Image.BILINEAR)


def light_sweep(img, u, strength=0.28):
    """A light sweep across `img` (u 0..1), like light catching glass."""
    if not 0 < u < 1:
        return img
    w, h = img.size
    band = _sweep_band(w, h)
    off = int((1 - u) * 2 * w)
    m = band.crop((off, 0, off + w, h)).point(lambda v: int(v * strength))
    return Image.composite(Image.new("RGB", (w, h), (255, 255, 255)), img, m)


def outline_points(box, radius, steps=6):
    """Clockwise points around a rounded rectangle, starting at the top-left corner's end."""
    x0, y0, x1, y1 = box
    r = max(1, min(radius, (x1 - x0) / 2, (y1 - y0) / 2))
    pts = []
    for cx, cy, a0 in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        for k in range(steps + 1):
            a = math.radians(a0 + 90 * k / steps)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts + [pts[0]]


def partial_path(pts, frac):
    """The first `frac` (0..1) of a polyline, by length."""
    if frac >= 1:
        return pts
    seg = [math.dist(a, b) for a, b in zip(pts, pts[1:])]
    goal, out = sum(seg) * max(0.0, frac), [pts[0]]
    for (a, b), s in zip(zip(pts, pts[1:]), seg):
        if goal <= s:
            k = goal / s if s else 0
            out.append((a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k))
            return out
        goal -= s
        out.append(b)
    return out


def draw_on_outline(img, box, frac, radius=18, colour=GOLD):
    """A rounded outline that draws itself (frac 0..1), with a soft glow underneath."""
    pts = partial_path(outline_points(box, radius), frac)
    if len(pts) < 2:
        return img
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.line(pts, fill=colour + (60,), width=16, joint="curve")  # glow
    d.line(pts, fill=colour + (255,), width=5, joint="curve")
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


# ------------------------------------------------------------- transitions
MAX_BLUR = 140  # px of horizontal motion blur at the fastest point of a whip-pan


def _box_blur_x(a, length):
    c = np.cumsum(a, axis=1, dtype=np.int32)
    c = np.concatenate([np.zeros((a.shape[0], 1, 3), np.int32), c], axis=1)
    x = np.arange(a.shape[1])
    lo = np.clip(x - length // 2, 0, a.shape[1])
    hi = np.clip(x + length - length // 2, 0, a.shape[1])
    return ((c[:, hi] - c[:, lo]) // (hi - lo)[None, :, None]).astype(np.uint8)


def whip(prev, cur, u, direction=1, top=0):
    """Whip-pan: the old shot leaves and the new one arrives in one fast move, blurred along the motion.
    Rows above `top` (the progress bar) stay still."""
    k = ease_in_out(u)
    a, b = np.asarray(prev), np.asarray(cur)
    pa, pb = a[top:], b[top:]
    shift = int(round(k * W))
    canvas = np.empty_like(pa)
    if direction > 0:  # content moves left; the new shot comes in from the right
        canvas[:, :W - shift] = pa[:, shift:]
        canvas[:, W - shift:] = pb[:, :shift]
    else:
        canvas[:, shift:] = pa[:, :W - shift]
        canvas[:, :shift] = pb[:, W - shift:]
    blur = int(MAX_BLUR * math.sin(math.pi * k))
    if blur > 2:
        canvas = _box_blur_x(canvas, blur)
    out = (b if k >= 0.5 else a).copy()
    out[top:] = canvas
    return Image.fromarray(out)


def circle_wipe(prev, cur, u, center=None):
    """The new shot opens from a growing circle with a thin light ring on its edge."""
    k = ease_in_out(u)
    cx, cy = center or (W / 2, H / 2)
    r = math.hypot(max(cx, W - cx), max(cy, H - cy)) * k
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    out = Image.composite(cur, prev, mask)
    if 0 < k < 1:
        ImageDraw.Draw(out).ellipse([cx - r, cy - r, cx + r, cy + r], outline=GOLD, width=6)
    return out


def panel_wipe(prev, cur, u, skew=520):
    """A diagonal panel sweeps across left to right, revealing the new shot behind its bright edge."""
    k = ease_in_out(u)
    p = k * (W + skew) - skew  # x of the edge at the bottom; the top leads by `skew`
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).polygon([(0, 0), (p + skew, 0), (p, H), (0, H)], fill=255)
    out = Image.composite(cur, prev, mask)
    if 0 < k < 1:
        ImageDraw.Draw(out).line([(p + skew, 0), (p, H)], fill=GOLD, width=8)
    return out


TRANSITIONS = {"whip": whip, "panel": panel_wipe, "circle": circle_wipe}


# ----------------------------------------------------------------- counters
NUM_RE = re.compile(r"^(\d+(?:\.\d+)?)([kKmM]?)(\+?)([.,!?:;]*)$")


def count_text(word, k):
    """`word` counted up to a fraction k of its value ("1.7k" at k=0.5 -> "0.9k"); None if not a number.
    Used only for numbers already in the script (real star counts), never invented ones."""
    m = NUM_RE.match(word)
    if not m:
        return None
    num, suffix, plus, punct = m.groups()
    decimals = len(num.split(".")[1]) if "." in num else 0
    return f"{float(num) * max(0.0, min(1.0, k)):.{decimals}f}{suffix}{plus}{punct}"
