"""Motion engine: draws every frame with Pillow and yields raw RGB bytes for FFmpeg.

Techniques: Ken Burns pan/zoom on the preview card, kinetic typography (words rise in one by one
through a line mask, the spoken word pops, numbers and names in an accent colour), word-by-word karaoke
synced to the narration, audio-reactive pulse (RMS envelope), optional watermark. Fade-in/out to black
is applied by FFmpeg.
"""
import array
import math
import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

from . import fx

W, H = 1080, 1920
FPS = 30
CARD_W = 960
BG_TOP, BG_BOT = (14, 16, 28), (44, 48, 72)
COL_DONE, COL_NOW, COL_NEXT = (255, 255, 255), (255, 214, 102), (120, 130, 160)
COL_EMPH = (110, 180, 255)  # numbers, repo names, ALL-CAPS words once spoken
MARGIN = 60
STAGGER = 0.045      # s between word entrances
STAGGER_TOTAL = 0.8  # the last word is in by this time, however long the line
RISE = 0.32          # s for a word to rise through its line mask
POP = 0.10           # extra scale on the word being spoken


# ---------------------------------------------------------------- easing
def ease_out(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def ease_in_out(t):
    t = max(0.0, min(1.0, t))
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


# ----------------------------------------------------------------- assets
FONT_DIR = Path(__file__).resolve().parent / "fonts"  # bundled (SIL Open Font License): same look on every runner


@lru_cache(maxsize=64)
def font(size):
    for name in (str(FONT_DIR / "Inter-Bold.ttf"), "DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf", "LiberationSans-Bold.ttf"):
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


# ------------------------------------------------------ kinetic typography
EMPH_RE = re.compile(r"\d")
PLAIN_CAPS = {"AI", "I", "OK", "TV", "US"}  # in nearly every line: highlighting them would mean nothing


def is_emphasis(word):
    """Numbers (stars, counts), repo names (owner/repo) and ALL-CAPS words carry the meaning."""
    w = word.strip(".,!?:;\"'()")
    return bool(EMPH_RE.search(w)) or "/" in w or (len(w) >= 2 and w.isupper() and w not in PLAIN_CAPS)


def pop(t):
    """Scale bump for the spoken word: quick rise, then a soft decay."""
    if t < 0:
        return 0.0
    return ease_out(t / 0.09) if t < 0.09 else math.exp(-(t - 0.09) * 9)


@lru_cache(maxsize=1024)  # ~75 MB at most
def word_sprite(word, size, col):
    fnt = font(size)
    img = Image.new("RGBA", (int(fnt.getlength(word)) + 6, int(size * 1.3) + 6), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((0, 0), word, font=fnt, fill=col + (255,))
    return img


def word_colour(word, i, active, theme=None):
    if i == active:
        return theme.now if theme else COL_NOW
    if i < active:
        return (theme.emph if theme else COL_EMPH) if is_emphasis(word) else COL_DONE
    return COL_NEXT


COUNT_TIME = 0.9  # s a number takes to count up to its value


def draw_kinetic_text(img, text, ty, lt, active, active_since, alpha=1.0, count=False, theme=None):
    """Draws the scene text at y=ty, `lt` seconds into the scene. Each word rises into place through
    its own line box (masked reveal), staggered; the word being spoken pops slightly. With count,
    numbers count up to their real value while they rise in, well before they are spoken."""
    words, fnt, lines, size, lh = text_layout(text)
    space = fnt.getlength(" ")
    stagger = min(STAGGER, STAGGER_TOTAL / max(1, len(words)))
    for row, idxs in enumerate(lines):
        x, y = MARGIN, ty + row * lh
        for i in idxs:
            ww = fnt.getlength(words[i])
            e = ease_out((lt - i * stagger) / RISE)
            if e > 0:
                scale = 1 + POP * pop(lt - active_since) if i == active else 1.0
                wsize = max(8, round(size * scale))
                shown = words[i]
                if count and fx.countable(shown):  # counts up to its real value as it rises in
                    k = (lt - i * stagger) / COUNT_TIME
                    shown = fx.count_text(shown, ease_out(k)) if k < 1 else shown
                spr = word_sprite(shown, wsize, word_colour(words[i], i, active, theme))
                dy = int((1 - e) * lh * 0.8)  # rises from below; the part under the line box stays hidden
                if dy < spr.height:
                    if dy:
                        spr = spr.crop((0, 0, spr.width, max(1, min(spr.height, lh - dy))))
                    a = alpha * min(1.0, e * 1.6)
                    if a < 0.999:
                        spr = spr.copy()
                        spr.putalpha(spr.getchannel("A").point(lambda v, k=a: int(v * k)))
                    grow = (wsize - size) / 2  # scale around the word's centre
                    img.paste(spr, (int(x - grow * ww / size), int(y + dy - grow)), spr)
            x += ww + space


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
        src = fx.grade(Image.open(path).convert("RGB"))
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
        src = fx.grade(Image.open(page_path).convert("RGB"))
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
        self.edge = fx.GOLD  # spotlight outline colour (the series theme sets it)

    def frame(self, progress, span_seconds):
        dist = min(self.page.height - self.view_h, self.MAX_SPEED * span_seconds)
        y = int(max(0, dist) * progress)
        img = self.chrome.copy()
        img.paste(self.page.crop((0, y, CARD_W, y + self.view_h)), (0, self.BAR))
        return img

    def frame_at(self, cx, cy, zoom, spot=None, strength=0.0):
        """Camera view centred on (cx, cy) in page pixels, magnified by `zoom` (1 = full width).
        spot: (x, y, w, h) page-pixel box to spotlight (rest dimmed, gold outline), faded by `strength`."""
        if self.page.height < self.view_h:  # short page: pad so the camera window always fits
            pad = Image.new("RGB", (CARD_W, self.view_h), (13, 17, 23))
            pad.paste(self.page, (0, 0))
            self.page = pad
        ww, hh = CARD_W / zoom, self.view_h / zoom
        cx = min(max(cx, ww / 2), CARD_W - ww / 2)
        cy = min(max(cy, hh / 2), self.page.height - hh / 2)
        x0, y0 = cx - ww / 2, cy - hh / 2
        view = self.page.resize((CARD_W, self.view_h), Image.BILINEAR, box=(x0, y0, x0 + ww, y0 + hh))
        if spot and strength > 0.01:
            sx, sy, sw, sh = spot
            r = [(sx - x0) * zoom - 14, (sy - y0) * zoom - 14, (sx + sw - x0) * zoom + 14, (sy + sh - y0) * zoom + 14]
            shade = Image.new("L", view.size, int(150 * strength))
            ImageDraw.Draw(shade).rounded_rectangle(r, 18, fill=0)
            view = Image.composite(Image.new("RGB", view.size, (0, 0, 0)), view, shade)
            view = fx.draw_on_outline(view, r, strength, colour=self.edge)  # draws itself as the camera arrives
        img = self.chrome.copy()
        img.paste(view, (0, self.BAR))
        return img

    def target(self, box, css_width=540):
        """Camera (cx, cy, zoom, spot) for a page box in CSS px; None = top of the page, no spotlight.
        Zoom is gentle (text at phone width fills the page, so a strong zoom would cut words off)."""
        if not box:
            return CARD_W / 2, self.view_h / 2, 1.0, None
        f = CARD_W / css_width
        x, y, w, h = (v * f for v in box)
        zoom = min(CARD_W * 0.98 / max(w, 1), self.view_h * 0.6 / max(h, 1), 1.6)
        return x + w / 2, y + h / 2, max(1.12, zoom), (x, y, w, h)


TRANSITION = 0.4  # s
BAR_Y = 120       # progress bar; rows above BAR_Y + 20 stay still during a whip-pan
PANEL_KINDS = ("scroll", "stats", "terminal", "compare", "podium")  # visuals drawn in the browser-window slot


def plan_transitions(kinds, page_of, cta=None):
    """Transition into each scene: None when the visual stays (the camera or the text carries the change),
    a circle wipe into the call to action (`cta`, default the last scene), a panel wipe into the end card,
    otherwise whip-pans alternating with panel wipes."""
    cta = len(kinds) - 1 if cta is None else cta
    out, n = [None] * len(kinds), 0
    for i in range(1, len(kinds)):
        if kinds[i] == kinds[i - 1] and page_of[i] == page_of[i - 1]:
            continue
        if i == cta:
            out[i] = "circle"
        elif kinds[i] == "end":
            out[i] = "panel"
        else:
            out[i] = "whip" if n % 2 == 0 or kinds[i - 1] != "scroll" else "panel"
            n += 1
    return out


def _pad(lst, n, front, back):
    lst = list(lst) if lst else [None] * n
    return [None] * front + lst + [None] * back


def frames(scenes, card_path=None, audio_path=None, watermark=None, page_path=None, page_url=None,
           scene_pages=None, scene_labels=None, scene_focus=None, disclaimer=None, events=None,
           theme=None, intro=None, endcard=None, scene_visuals=None, scene_logos=None):
    """scenes: [(text, seconds)]. Yields raw RGB24 bytes, FPS per second.
    scene_pages: per scene (page_path, url) or None -> that scene shows the page in a browser window;
    consecutive scenes on the same page share one continuous scroll. Without it, a single page_path is
    shown on the middle scenes and the preview card on the first and last (GitHub videos).
    scene_labels: per scene short label drawn above the visual (e.g. "#3/10  Runway").
    scene_visuals: per scene None or a data panel: ("stats", info) | ("terminal", commands) |
    ("compare", (before, after)) | ("podium", items). scene_logos: per scene a logo image path.
    intro / endcard: dicts for the hook frame before the first scene and the end card after the last.
    events: a list that receives (seconds, cue) before the first frame: sounds ("impact", "whoosh",
    "click") and markers for the music ("cta", "end")."""
    from . import visuals
    from .theme import DEFAULT
    theme = theme or DEFAULT
    real = len(scenes)
    if scene_pages is None:
        scene_pages = [(page_path, page_url) if page_path and (0 < i < real - 1 or not card_path) else None
                       for i in range(real)]
    front, back = (1 if intro else 0), (1 if endcard else 0)
    scenes = ([("", visuals.INTRO)] if intro else []) + list(scenes) + ([("", visuals.ENDCARD)] if endcard else [])
    scene_pages = _pad(scene_pages, real, front, back)
    labels = _pad(scene_labels, real, front, back)
    focus = _pad(scene_focus, real, front, back) if scene_focus else None
    vis = _pad(scene_visuals, real, front, back)
    logos = _pad(scene_logos, real, front, back)
    first_real, cta = front, front + real - 1
    total = sum(d for _, d in scenes)
    content_t0, content_t1 = (visuals.INTRO if intro else 0.0), total - (visuals.ENDCARD if endcard else 0.0)
    n = round(total * FPS)
    env = envelope(audio_path, n)
    card = Card(card_path) if card_path else None
    backdrop = fx.Backdrop(theme.bg_top, theme.bg_bot, accent=theme.accent)
    wm = None
    if watermark:
        f = font(34)
        wm = Image.new("RGBA", (int(f.getlength(watermark)) + 4, 50), (0, 0, 0, 0))
        ImageDraw.Draw(wm).text((0, 0), watermark, font=f, fill=(110, 120, 150, 255))

    last = len(scenes) - 1
    fallback = "card" if card else "text"
    kinds = []
    for i in range(len(scenes)):
        if intro and i == 0:
            kinds.append("intro")
        elif endcard and i == last:
            kinds.append("end")
        elif vis[i]:
            kinds.append(vis[i][0])
        else:
            kinds.append("scroll" if scene_pages[i] else fallback)

    bounds, t0 = [], 0.0
    for text, dur in scenes:
        bounds.append((t0, t0 + dur))
        t0 += dur

    def max_h(kind_set):
        hs = [text_height(t) for (t, _), k in zip(scenes, kinds) if k in kind_set and t]
        return max(hs) if hs else 0

    if card:
        y_card = max(260, (H - (card.h + 80 + max_h(("card",)))) // 2 - 40)
    y_panel = 250 if any(labels) else 230
    ph = max(700, min(1150, H - y_panel - 60 - max_h(PANEL_KINDS) - 200))
    panels, vpanels = {}, {}
    for i, k in enumerate(kinds):
        if k == "scroll" and scene_pages[i][0] not in panels:
            try:
                panels[scene_pages[i][0]] = Panel(scene_pages[i][0], scene_pages[i][1] or "", ph)
                panels[scene_pages[i][0]].edge = theme.edge
            except Exception:
                panels[scene_pages[i][0]] = None
        elif k in ("stats", "terminal", "compare", "podium"):
            data = vis[i][1]
            try:
                vpanels[i] = (visuals.StatsPanel(data, theme, ph) if k == "stats" else
                              visuals.TermPanel(data, theme, ph, "Terminal — install") if k == "terminal" else
                              visuals.ComparePanel(data[0], data[1], theme, ph) if k == "compare" else
                              visuals.PodiumPanel(data, theme, ph))
            except Exception:
                vpanels[i] = None
    kinds = [fallback if (k == "scroll" and not panels.get(scene_pages[i][0]))
             or (k in ("stats", "terminal", "compare", "podium") and not vpanels.get(i)) else k
             for i, k in enumerate(kinds)]
    page_of = [scene_pages[i][0] if kinds[i] == "scroll" else (("vis", i) if i in vpanels else None)
               for i in range(len(scenes))]
    # scroll span per run of consecutive scenes on the same page; entrance animation at each new visual
    span_of, run_start, run_first = [None] * len(scenes), [], []
    for i in range(len(scenes)):
        new = i == 0 or kinds[i] != kinds[i - 1] or page_of[i] != page_of[i - 1]
        run_start.append(bounds[i][0] if new else run_start[i - 1])
        run_first.append(i if new else run_first[i - 1])
    i = 0
    while i < len(scenes):
        j = i
        while j + 1 < len(scenes) and kinds[i] == "scroll" and page_of[j + 1] == page_of[i]:
            j += 1
        for k in range(i, j + 1):
            span_of[k] = (bounds[i][0], bounds[j][1])
        i = j + 1
    label_font = font(52)
    starts = [word_starts(t, dur - 0.4) if t else [] for t, dur in scenes]
    trans = plan_transitions(kinds, page_of, cta=cta if real > 1 else None)
    edge = {"circle": lambda a, b, u: fx.circle_wipe(a, b, u, colour=theme.edge),
            "panel": lambda a, b, u: fx.panel_wipe(a, b, u, colour=theme.edge)}

    def move_time(si):  # guided tour: seconds for the camera to glide to the scene's section
        return min(1.4, (bounds[si][1] - bounds[si][0]) * 0.35)

    def visual_lt(si, t):  # seconds since this visual became fully visible
        return t - run_start[si] - (TRANSITION if trans[run_first[si]] else 0.0)

    # impacts (shake + sound) and cues, all known before the first frame
    impacts = [(0.05, 9 if intro else 7)]
    cues = [(0.0, "impact")]
    for si, tr in enumerate(trans):
        if tr:
            a = bounds[si][0]
            cues.append((max(0.0, a - 0.06), "whoosh"))
            impacts.append((a + TRANSITION, 12 if tr == "circle" else 5))
            if tr == "circle":
                cues.append((a + TRANSITION * 0.85, "impact"))
        if focus and focus[si] and kinds[si] == "scroll":
            cues.append((bounds[si][0] + move_time(si), "click"))
        if kinds[si] == "terminal" and run_first[si] == si:
            base = bounds[si][0] + (TRANSITION if trans[si] else 0.0)
            cues += [(base + s, "click") for s in vpanels[si].command_times()]
    if real > 1:
        cues.append((bounds[cta][0], "cta"))
    if endcard:
        cues.append((bounds[last][0], "end"))
    if events is not None:
        events.extend(sorted(cues))

    def compose(t, si, fi):
        a, b = bounds[si]
        lt, text = t - a, scenes[si][0]
        img = backdrop.frame(fi)
        if kinds[si] == "intro":  # the hook frame: also the cover, so nothing else on it
            visuals.draw_intro(img, intro, theme, lt)
            return img
        if wm:
            img.paste(wm, ((W - wm.width) // 2, H - 120), wm)
        idraw = ImageDraw.Draw(img)
        if kinds[si] == "end":
            visuals.draw_endcard(img, endcard, theme, lt)
            return img
        prog = max(0.0, min(1.0, (t - content_t0) / max(0.1, content_t1 - content_t0)))
        bar = MARGIN + int((W - 2 * MARGIN) * prog)
        idraw.rounded_rectangle([MARGIN - 3, BAR_Y - 3, bar + 3, BAR_Y + 15], 9,
                                fill=tuple(c // 3 for c in theme.accent))  # glow
        idraw.rectangle([MARGIN, BAR_Y, bar, BAR_Y + 12], fill=theme.accent)
        whipped = trans[run_first[si]] is not None  # the transition is this visual's entrance
        since = t - run_start[si]
        e = 1.0 if whipped else ease_out(since / 0.6)      # entrance: rise + fade in
        label_x = MARGIN + (visuals.BADGE + 30 if logos[si] else 0)
        if labels[si]:  # e.g. "#3/10  Runway": which item we are on, above the visual
            idraw.text((label_x, 150 + int(30 * (1 - e))), labels[si], font=label_font,
                       fill=theme.edge if e >= 1 else tuple(int(c * e) for c in theme.edge))

        if kinds[si] == "card":
            c = card.frame(t / total, env[fi])
            c = fx.light_sweep(c, (since - 0.6) / 0.8)
            sc = 1.0 if whipped else 0.9 + 0.1 * fx.spring(since)  # punch-in with a soft overshoot
            m = card.mask.point(lambda v, e=e: int(v * e)) if e < 1 else card.mask
            if sc != 1.0:
                size = (int(CARD_W * sc), int(card.h * sc))
                c, m = c.resize(size, Image.BILINEAR), m.resize(size, Image.BILINEAR)
            x, yy = (W - c.width) // 2, y_card + (card.h - c.height) // 2
            if e >= 1:
                idraw.rounded_rectangle([x - 6, yy - 6, x + c.width + 6, yy + c.height + 6], 32,
                                        fill=tuple(int(v * 0.5) for v in theme.accent))
            img.paste(c, (x, yy), m)
            ty = y_card + card.h + 80
        elif kinds[si] in PANEL_KINDS:
            if kinds[si] == "scroll":
                panel = panels[page_of[si]]
                if focus:
                    # guided tour: glide to this scene's section and zoom in, then drift in slowly
                    prev = panel.target(focus[si - 1]) if si > first_real else panel.target(None)
                    cur = panel.target(focus[si])
                    move = move_time(si)
                    k = ease_in_out(lt / move)
                    drift = 1 + 0.05 * max(0.0, min(1.0, (lt - move) / max(0.1, (b - a) - move)))
                    cx, cy = prev[0] + (cur[0] - prev[0]) * k, prev[1] + (cur[1] - prev[1]) * k
                    pimg = panel.frame_at(cx, cy, (prev[2] + (cur[2] - prev[2]) * k) * drift,
                                          spot=cur[3], strength=k)  # spotlight draws on as the camera arrives
                else:
                    s_start, s_end = span_of[si]
                    span = s_end - s_start
                    hold = min(0.8, span * 0.15)
                    prog = ease_in_out((t - s_start - hold) / max(0.1, span - 2 * hold))  # hold at top and bottom
                    pimg = panel.frame(prog, span)
            else:
                panel = vpanels[si]
                pimg = panel.frame(max(0.0, visual_lt(si, t)))
            pimg = fx.light_sweep(pimg, (since - 0.45) / 0.8)
            m = panel.mask.point(lambda v, e=e: int(v * e)) if e < 1 else panel.mask
            img.paste(pimg, (MARGIN, y_panel + int(70 * (1 - e))), m)
            ty = y_panel + panel.h + 60
        else:
            ty = (H - text_height(text)) // 2

        if logos[si]:  # the site's own icon pops in over the visual, then becomes the label badge
            try:
                visuals.draw_logo(img, logos[si], visual_lt(si, t) - 0.05, (W / 2, y_panel + ph / 2),
                                  (MARGIN, 140), theme)
            except Exception:
                logos[si] = None
        if text:
            active = sum(1 for s_ in starts[si] if lt >= s_) - 1 if lt >= starts[si][0] else -1
            fade_out = max(0.0, min(1.0, 1 - ease_in_out((lt - (b - a - 0.25)) / 0.25)))
            draw_kinetic_text(img, text, ty, lt, active, starts[si][active] if active >= 0 else 0.0, fade_out,
                              count=True, theme=theme)
        if disclaimer and content_t0 <= t < content_t0 + 2.4:  # small note for the first ~2 s, not spoken
            a_ = 1 - ease_in_out((t - content_t0 - 1.8) / 0.6)
            if a_ > 0:
                df = font(30)
                dw = df.getlength(disclaimer)
                col = tuple(int(c * a_ + bc * (1 - a_)) for c, bc in zip((210, 215, 230), theme.bg_bot))
                ImageDraw.Draw(img).text(((W - dw) / 2, H - 190), disclaimer, font=df, fill=col)
        return img

    frozen = None  # (scene, image): the outgoing shot of the current transition, held on its last frame
    direction = 1
    for fi in range(n):
        t = fi / FPS
        si = next(i for i, (a, b) in enumerate(bounds) if t < b) if t < total else last
        lt = t - bounds[si][0]
        tr = trans[si]
        if tr and lt < TRANSITION:
            if not frozen or frozen[0] != si:
                frozen = (si, compose(bounds[si][0] - 1 / FPS, si - 1, fi))
                direction = -direction
            cur = compose(t, si, fi)
            u = lt / TRANSITION
            if tr == "whip":
                img = fx.whip(frozen[1], cur, u, direction, top=BAR_Y + 20)
            else:
                img = edge[tr](frozen[1], cur, u)
        else:
            img = compose(t, si, fi)
        dx = dy = 0
        for at, amp in impacts:
            ox, oy = fx.shake(t - at, amp, seed=int(at * 10))
            dx, dy = dx + ox, dy + oy
        if dx or dy:
            img = ImageChops.offset(img, dx, dy)
        yield fx.vignette(img).tobytes()
