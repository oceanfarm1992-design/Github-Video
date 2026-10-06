"""Motion engine: draws every frame with Pillow and yields raw RGB bytes for FFmpeg.

Techniques: Ken Burns pan/zoom on the preview card, ease-out entrance + text slide/fade,
word-by-word karaoke subtitles synced to the narration, audio-reactive pulse (RMS envelope),
optional watermark. Fade-in/out to black is applied by FFmpeg.
"""
import array
import os
import subprocess
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont, ImageOps

W, H = 1080, 1920
FPS = 30
CARD_W = 960
BG_TOP, BG_BOT = (14, 16, 28), (44, 48, 72)
COL_DONE, COL_NOW, COL_NEXT = (255, 255, 255), (255, 214, 102), (120, 130, 160)
MARGIN = 60


# ---------------------------------------------------------------- easing
def ease_out(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def ease_in_out(t):
    t = max(0.0, min(1.0, t))
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


# ----------------------------------------------------------------- assets
def font(size):
    for name in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf", "LiberationSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


@lru_cache(maxsize=1)
def background():
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        f = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(a + (b - a) * f) for a, b in zip(BG_TOP, BG_BOT)))
    return img


def wrap_words(words, fnt, width):
    """Greedy wrap -> list of lines, each a list of word indices."""
    lines, cur, w = [], [], 0
    space = fnt.getlength(" ")
    for i, word in enumerate(words):
        ww = fnt.getlength(word)
        if cur and w + space + ww > width:
            lines.append(cur)
            cur, w = [], 0
        w += (space if cur else 0) + ww
        cur.append(i)
    if cur:
        lines.append(cur)
    return lines


def _truncate(word, fnt, limit):
    while len(word) > 1 and fnt.getlength(word + "...") > limit:
        word = word[:-1]
    return word + "..."


def text_layout(text):
    words = text.split()
    size = 66 if len(text) < 90 else 54
    fnt = font(size)
    while size > 30 and max(fnt.getlength(w) for w in words) > W - 2 * MARGIN:  # long repo names
        size -= 4
        fnt = font(size)
    limit = W - 2 * MARGIN
    words = [w if fnt.getlength(w) <= limit else _truncate(w, fnt, limit) for w in words]  # still too wide at min size
    lines = wrap_words(words, fnt, limit)
    return words, fnt, lines, size, int(size * 1.3)


def text_height(text):
    _, _, lines, _, lh = text_layout(text)
    return len(lines) * lh


def render_text_layer(text, active):
    """RGBA layer with words coloured by progress: done / current (accent) / upcoming."""
    words, fnt, lines, size, lh = text_layout(text)
    layer = Image.new("RGBA", (W - 2 * MARGIN, len(lines) * lh + 10), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    space = fnt.getlength(" ")
    for row, idxs in enumerate(lines):
        x = 0
        for i in idxs:
            col = COL_DONE if i < active else (COL_NOW if i == active else COL_NEXT)
            d.text((x, row * lh), words[i], font=fnt, fill=col + (255,))
            x += fnt.getlength(words[i]) + space
    return layer


def word_starts(text, duration):
    """Approximate start time of each word within the narrated part (weighted by length)."""
    words = text.split()
    weights = [len(w) + 2 for w in words]
    total, t, out = sum(weights), 0.0, []
    speak = max(0.5, duration * 0.92)
    for w in weights:
        out.append(t)
        t += speak * w / total
    return out


# ------------------------------------------------------------------ audio
def envelope(audio_path, n_frames):
    """Per-frame loudness 0..1 from the narration (RMS), smoothed with attack/release."""
    if not audio_path:
        return [0.0] * n_frames
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(audio_path), "-f", "s16le", "-ac", "1", "-ar", "8000", "-"],
                         capture_output=True, check=True, timeout=120).stdout
    s = array.array("h")
    s.frombytes(raw[: len(raw) // 2 * 2])
    win = 8000 // FPS
    rms = []
    for i in range(n_frames):
        seg = s[i * win:(i + 1) * win]
        rms.append((sum(v * v for v in seg) / len(seg)) ** 0.5 if seg else 0.0)
    peak = max(rms) or 1.0
    env, cur = [], 0.0
    for r in rms:
        v = r / peak
        cur = v if v > cur else cur * 0.85  # fast attack, slow release
        env.append(cur)
    return env


# -------------------------------------------------------------- the frames
class Card:
    """Preview image with Ken Burns pan/zoom and audio-reactive pulse."""

    def __init__(self, path):
        src = Image.open(path).convert("RGB")
        self.h = min(int(src.height * CARD_W / src.width), 760)
        self.big = ImageOps.fit(src, (int(CARD_W * 1.2), int(self.h * 1.2)), Image.LANCZOS)
        self.mask = Image.new("L", (CARD_W, self.h), 0)
        ImageDraw.Draw(self.mask).rounded_rectangle([0, 0, CARD_W, self.h], 28, fill=255)

    def frame(self, progress, pulse):
        z = 1.0 + 0.04 * progress + 0.01 * pulse            # slow zoom-in + beat pulse
        bw, bh = self.big.size
        cw, ch = bw / z, bh / z                              # z=1 shows the whole card, nothing cropped
        slack = (bw - cw) / 2
        cx = bw / 2 + slack * (2 * progress - 1) * 0.8       # pan left -> right within the zoom slack
        cy = bh / 2
        box = (cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)
        return self.big.resize((CARD_W, self.h), Image.BILINEAR, box=box)


class Panel:
    """Browser window (chrome bar + address) showing a tall page capture, scrolled smoothly."""
    BAR = 70
    # scroll speed in output px/s (env SCROLL_SPEED); slower = more readable, longer pages scroll partway
    MAX_SPEED = max(60.0, min(600.0, float(os.environ.get("SCROLL_SPEED", "200") or 200)))

    def __init__(self, page_path, url, height):
        src = Image.open(page_path).convert("RGB")
        self.page = src.resize((CARD_W, int(src.height * CARD_W / src.width)), Image.LANCZOS)
        self.h = height
        self.view_h = height - self.BAR
        self.chrome = Image.new("RGB", (CARD_W, height), (13, 17, 23))
        d = ImageDraw.Draw(self.chrome)
        d.rectangle([0, 0, CARD_W, self.BAR], fill=(36, 41, 47))
        for i, col in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
            d.ellipse([24 + i * 34, 25, 44 + i * 34, 45], fill=col)
        d.rounded_rectangle([140, 14, CARD_W - 24, self.BAR - 14], 20, fill=(22, 27, 34))
        f = font(28)
        label = url.replace("https://", "")
        while f.getlength(label) > CARD_W - 210 and len(label) > 8:
            label = label[:-2]
        d.text((166, 20), label, font=f, fill=(201, 209, 217))
        self.mask = Image.new("L", (CARD_W, height), 0)
        ImageDraw.Draw(self.mask).rounded_rectangle([0, 0, CARD_W, height], 26, fill=255)

    def frame(self, progress, span_seconds):
        dist = min(self.page.height - self.view_h, self.MAX_SPEED * span_seconds)
        y = int(max(0, dist) * progress)
        img = self.chrome.copy()
        img.paste(self.page.crop((0, y, CARD_W, y + self.view_h)), (0, self.BAR))
        return img


def frames(scenes, card_path=None, audio_path=None, watermark=None, page_path=None, page_url=None):
    """scenes: [(text, seconds)]. Yields raw RGB24 bytes, FPS per second.
    With a page capture, the middle scenes show it scrolling in a browser window; the first and last
    scenes show the preview card (or the page too, when there is no card)."""
    total = sum(d for _, d in scenes)
    n = round(total * FPS)
    env = envelope(audio_path, n)
    card = Card(card_path) if card_path else None
    bg = background().copy()
    d = ImageDraw.Draw(bg)
    if watermark:
        f = font(34)
        wlen = f.getlength(watermark)
        d.text(((W - wlen) / 2, H - 120), watermark, font=f, fill=(110, 120, 150))

    last = len(scenes) - 1
    kinds = []
    for i in range(len(scenes)):
        if page_path and (0 < i < last or not card):
            kinds.append("scroll")
        else:
            kinds.append("card" if card else "text")

    bounds, t0 = [], 0.0
    for text, dur in scenes:
        bounds.append((t0, t0 + dur))
        t0 += dur

    def max_h(kind):
        hs = [text_height(t) for (t, _), k in zip(scenes, kinds) if k == kind]
        return max(hs) if hs else 0

    if card:
        y_card = max(260, (H - (card.h + 80 + max_h("card"))) // 2 - 40)
    panel = None
    if "scroll" in kinds:
        y_panel = 230
        ph = max(700, min(1180, H - y_panel - 60 - max_h("scroll") - 200))
        try:
            panel = Panel(page_path, page_url or "", ph)
        except Exception:
            panel = None
            kinds = ["card" if card else "text" if k == "scroll" else k for k in kinds]
    if panel:
        idx = [i for i, k in enumerate(kinds) if k == "scroll"]
        s_start, s_end = bounds[idx[0]][0], bounds[idx[-1]][1]
    # first frame time of each contiguous run of a visual, for entrance animations
    run_start = [bounds[i][0] if i == 0 or kinds[i] != kinds[i - 1] else None for i in range(len(scenes))]
    for i in range(1, len(scenes)):
        run_start[i] = run_start[i] if run_start[i] is not None else run_start[i - 1]

    starts = [word_starts(t, dur - 0.4) for t, dur in scenes]
    layers = {}

    for fi in range(n):
        t = fi / FPS
        si = next(i for i, (a, b) in enumerate(bounds) if t < b) if t < total else last
        a, b = bounds[si]
        lt, text = t - a, scenes[si][0]
        img = bg.copy()
        idraw = ImageDraw.Draw(img)
        idraw.rectangle([MARGIN, 120, MARGIN + int((W - 2 * MARGIN) * (t / total)), 132], fill=(90, 160, 255))
        e = ease_out((t - run_start[si]) / 0.6)              # entrance: rise + fade in

        if kinds[si] == "card":
            c = card.frame(t / total, env[fi])
            m = card.mask.point(lambda v, e=e: int(v * e)) if e < 1 else card.mask
            yy = y_card + int(70 * (1 - e))
            if e >= 1:
                idraw.rounded_rectangle([MARGIN - 6, yy - 6, MARGIN + CARD_W + 6, yy + card.h + 6], 32,
                                        fill=(60, 80, 130))
            img.paste(c, (MARGIN, yy), m)
            ty = y_card + card.h + 80
        elif kinds[si] == "scroll":
            span = s_end - s_start
            prog = ease_in_out((t - s_start - 0.8) / max(0.1, span - 1.6))  # hold at top and bottom
            pimg = panel.frame(prog, span)
            m = panel.mask.point(lambda v, e=e: int(v * e)) if e < 1 else panel.mask
            img.paste(pimg, (MARGIN, y_panel + int(70 * (1 - e))), m)
            ty = y_panel + panel.h + 60
        else:
            ty = (H - text_height(text)) // 2

        active = sum(1 for s in starts[si] if lt >= s) - 1 if lt >= starts[si][0] else -1
        key = (si, active)
        if key not in layers:
            layers[key] = render_text_layer(text, active if active >= 0 else -1)
        layer = layers[key]
        fade_in, fade_out = ease_out(lt / 0.35), 1 - ease_in_out((lt - (b - a - 0.25)) / 0.25)
        alpha = max(0.0, min(1.0, fade_in * fade_out))
        r, g, bl, al = layer.split()
        if alpha < 1:
            al = al.point(lambda v, k=alpha: int(v * k))
        img.paste(Image.merge("RGB", (r, g, bl)), (MARGIN, ty + int(36 * (1 - fade_in))), al)
        yield img.tobytes()
