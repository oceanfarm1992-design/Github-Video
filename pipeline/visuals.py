"""Designed visuals for the motion engine, each drawn from real data only.

Hook frame  - full-screen title card for the first second; frame 0 is already complete, so it is also
              the cover every platform shows in the grid (then it settles with a punch)
End card    - comment poll + FOLLOW button + what comes next ("Part 4 tomorrow")
StatsPanel  - star-growth chart from our own hourly star counts (or a growth card: stars and repo age),
              then stat chips: forks, language, licence, last update - all from GitHub's API
TermPanel   - the README's own install command typed into a terminal (no invented output)
ComparePanel- before/after slider, only for real example images listed in the tools catalog
PodiumPanel - weekly ranking: the top 3 rise on a podium
Logo intro  - a site's own icon zooms in, then flies into the label as a badge
"""
import math
import time
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from . import fx

W, H = 1080, 1920
CARD_W = 960
MARGIN = 60
PANEL_BG = (13, 17, 23)
GREY = (150, 160, 180)
WHITE = (255, 255, 255)


def font(size):
    from .motion import font as f  # one font loader for the whole engine
    return f(size)


@lru_cache(maxsize=16)
def mono(size):
    from PIL import ImageFont
    for name in ("DejaVuSansMono-Bold.ttf", "consolab.ttf", "LiberationMono-Bold.ttf", "Courier New Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return font(size)


def ease_out(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def short_num(n):
    """3075 -> '3.1k', 1234567 -> '1.2M' (the way GitHub shows counts)."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    return str(int(n))


def draw_star(d, cx, cy, r, fill):
    """A five-point star drawn as a shape (font glyphs for stars are missing on some systems)."""
    pts = []
    for k in range(10):
        a = math.pi / 2 + k * math.pi / 5
        rr = r if k % 2 == 0 else r * 0.42
        pts.append((cx + rr * math.cos(a), cy - rr * math.sin(a)))
    d.polygon(pts, fill=fill)


def rounded_mask(w, h, r=26):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], r, fill=255)
    return m


def wrap(text, fnt, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        if cur and fnt.getlength(test) > width:
            lines.append(cur)
            cur = w
        else:
            cur = test
    return lines + ([cur] if cur else [])


def centered(d, y, text, fnt, fill, width=W, x0=0):
    d.text((x0 + (width - fnt.getlength(text)) / 2, y), text, font=fnt, fill=fill)


def pill(text, fnt, fill, colour, pad_x=34, height=None, spacing=0):
    """Rounded label image; `spacing` adds letter spacing (tags)."""
    tw = sum(fnt.getlength(ch) + spacing for ch in text) - spacing
    h = height or int(fnt.size * 1.9)
    img = Image.new("RGBA", (int(tw + 2 * pad_x), h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, img.width - 1, h - 1], h // 2, fill=fill)
    x, y = pad_x, (h - fnt.size * 1.18) / 2
    for ch in text:
        d.text((x, y), ch, font=fnt, fill=colour)
        x += fnt.getlength(ch) + spacing
    return img


def paste_scaled(img, layer, center, scale):
    if scale <= 0.01:
        return
    if abs(scale - 1) > 0.002:
        layer = layer.resize((max(1, int(layer.width * scale)), max(1, int(layer.height * scale))), Image.BILINEAR)
    img.paste(layer, (int(center[0] - layer.width / 2), int(center[1] - layer.height / 2)), layer)


@lru_cache(maxsize=8)
def glow(colour, w=1000, h=760, alpha=95):
    g = Image.new("RGBA", (w, h), colour + (0,))
    ImageDraw.Draw(g).ellipse([w * 0.15, h * 0.2, w * 0.85, h * 0.8], fill=colour + (alpha,))
    return g.filter(ImageFilter.GaussianBlur(110))


# ---------------------------------------------------------------- hook frame
INTRO = 1.0  # s


@lru_cache(maxsize=8)
def _title_layer(title, emph):
    size = 136
    while True:
        fnt = font(size)
        lines = wrap(title, fnt, CARD_W)
        if (len(lines) <= 3 and max(fnt.getlength(x) for x in lines) <= CARD_W) or size <= 64:
            break
        size -= 6
    lh = int(size * 1.12)
    layer = Image.new("RGBA", (CARD_W, lh * len(lines) + 20), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for i, line in enumerate(lines):
        x = (CARD_W - fnt.getlength(line)) / 2
        for word in line.split(" "):
            hot = any(ch.isdigit() for ch in word)
            d.text((x + 4, i * lh + 6), word, font=fnt, fill=(0, 0, 0, 120))  # soft drop shadow
            d.text((x, i * lh), word, font=fnt, fill=(emph if hot else WHITE) + (255,))
            x += fnt.getlength(word + " ")
    return layer


def draw_intro(img, intro, theme, lt):
    """intro: {"tag", "title", "sub"}. Complete at lt=0 (the cover); then a punch settles it."""
    cy = 900
    g = glow(theme.accent)
    img.paste(g, ((W - g.width) // 2, cy - g.height // 2), g)
    title = _title_layer(intro["title"], theme.emph)
    s = 1 + 0.09 * (1 - fx.spring(lt * 1.3))
    paste_scaled(img, title, (W / 2, cy), s)
    top, bottom = cy - title.height * s / 2, cy + title.height * s / 2
    tag = pill(intro.get("tag") or theme.tag, font(34), theme.accent + (255,), (10, 10, 14), spacing=5)
    img.paste(tag, ((W - tag.width) // 2, int(top - tag.height - 40)), tag)
    d = ImageDraw.Draw(img)
    bar = int(CARD_W * 0.42 * (0.35 + 0.65 * ease_out(lt / 0.5)))
    d.rounded_rectangle([(W - bar) / 2, bottom + 26, (W + bar) / 2, bottom + 38], 6, fill=theme.accent)
    if intro.get("sub"):
        f = font(52)
        for i, line in enumerate(wrap(intro["sub"], f, CARD_W)[:2]):
            centered(d, bottom + 70 + i * 66, line, f, theme.now)


def cover(intro, theme, backdrop_frame):
    """The settled hook frame as a still image: the video's cover/thumbnail."""
    img = backdrop_frame.copy()
    draw_intro(img, intro, theme, 2.0)
    return fx.vignette(img)


# ------------------------------------------------------------------ end card
ENDCARD = 2.2  # s


def draw_endcard(img, end, theme, lt):
    """end: {"question", "options", "line"}: comment poll, FOLLOW button, what comes next."""
    d = ImageDraw.Draw(img)
    tag = pill(theme.tag, font(30), theme.accent + (60,), theme.accent, spacing=4)
    img.paste(tag, ((W - tag.width) // 2, 300), tag)
    # poll card
    e = fx.spring(lt * 1.2)
    card = Image.new("RGBA", (CARD_W, 470), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    cd.rounded_rectangle([0, 0, CARD_W - 1, 469], 36, fill=(255, 255, 255, 22), outline=theme.accent + (120,), width=3)
    qf = font(56)
    for i, line in enumerate(wrap(end["question"], qf, CARD_W - 80)[:2]):
        centered(cd, 44 + i * 70, line, qf, WHITE, width=CARD_W)
    opts = end.get("options") or []
    many = len(opts) > 3
    cw, ch, gap = (130, 96, 26) if many else (230, 104, 40)
    per_row = 5 if many else len(opts)
    of = font(46 if many else 50)
    for i, o in enumerate(opts):
        row, col = divmod(i, per_row)
        n_row = min(per_row, len(opts) - row * per_row)
        x0 = (CARD_W - (n_row * cw + (n_row - 1) * gap)) / 2 + col * (cw + gap)
        y0 = 200 + row * (ch + 22) - (0 if len(opts) > per_row else -40)
        k = fx.spring(lt - 0.25 - 0.05 * i)
        if k <= 0:
            continue
        chip = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        hd = ImageDraw.Draw(chip)
        hd.rounded_rectangle([0, 0, cw - 1, ch - 1], 24, fill=theme.accent + (235,))
        centered(hd, (ch - of.size * 1.15) / 2, o, of, (12, 12, 16), width=cw)
        paste_scaled(card, chip, (x0 + cw / 2, y0 + ch / 2), min(1.15, k))
    hint = font(32)
    centered(cd, 420, "Comment your answer below", hint, GREY, width=CARD_W)
    card_y = 400 + int(60 * (1 - min(1.0, e)))
    paste_scaled(img, card, (W / 2, card_y + card.height / 2), 0.9 + 0.1 * min(1.0, e))
    # follow button: pops in, then breathes; a light sweep runs across it
    k = fx.spring(lt - 0.55)
    if k > 0:
        btn = pill("+  FOLLOW", font(66), theme.accent + (255,), (10, 10, 14), pad_x=70, height=136)
        btn = fx.light_sweep(btn.convert("RGB"), (lt - 1.0) / 0.7).convert("RGBA")
        btn.putalpha(rounded_mask(btn.width, btn.height, 68))
        breathe = 1 + 0.035 * math.sin(2 * math.pi * 1.6 * max(0.0, lt - 0.9))
        paste_scaled(img, btn, (W / 2, 1110), k * breathe)
    if end.get("line") and lt > 0.8:
        a = ease_out((lt - 0.8) / 0.4)
        f = font(48)
        col = tuple(int(c * a + 30 * (1 - a)) for c in theme.now)
        centered(d, 1225, end["line"], f, col)


# ---------------------------------------------------------- panels (base)
class _Panel:
    def __init__(self, h):
        self.h = h
        self.mask = rounded_mask(CARD_W, h)

    def base(self, theme):
        img = Image.new("RGB", (CARD_W, self.h), PANEL_BG)
        ImageDraw.Draw(img).rounded_rectangle([0, 0, CARD_W - 1, self.h - 1], 26,
                                              outline=tuple(c // 2 for c in theme.accent), width=3)
        return img


LANG_COLOURS = {"Python": (53, 114, 165), "JavaScript": (241, 224, 90), "TypeScript": (49, 120, 198),
                "Go": (0, 173, 216), "Rust": (222, 165, 132), "C++": (243, 75, 125), "C": (85, 85, 85),
                "Java": (176, 114, 25), "Jupyter Notebook": (218, 91, 11), "Shell": (137, 224, 81),
                "Swift": (240, 81, 56), "Kotlin": (169, 123, 255), "Ruby": (112, 21, 22), "HTML": (227, 76, 38)}


def _ago(iso):
    try:
        days = int((time.time() - fx_ts(iso)) // 86400)
    except Exception:
        return None
    return "Updated today" if days <= 0 else f"Updated {days}d ago" if days < 60 else None


def fx_ts(iso):
    import calendar
    return calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))


class StatsPanel(_Panel):
    """Star growth (our real samples) or a growth card, then real stat chips."""

    def __init__(self, info, theme, h):
        super().__init__(h)
        self.info, self.theme = info, theme
        self.hist = info.get("history") or []
        chips = [f"{info['forks']:,} forks" if info.get("forks") is not None else None,
                 info.get("language"), f"{info['license']} license" if info.get("license") not in (None, "NOASSERTION")
                 else None, _ago(info.get("pushed_at"))]
        self.chips = [c for c in chips if c][:4]
        self.chip_pos, x, y, f = [], 40, 0, font(34)  # chip layout (rows wrap); y relative to the first row
        for c in self.chips:
            w = int(f.getlength(c) + 60 + (34 if c == info.get("language") and c in LANG_COLOURS else 0))
            if x + w > CARD_W - 40:
                x, y = 40, y + 86
            self.chip_pos.append((x, y, w))
            x += w + 18
        rows_h = (self.chip_pos[-1][1] + 86) if self.chip_pos else 0
        self.chips_top = h - 100 - rows_h
        self.cx0, self.cx1, self.cy0, self.cy1 = 90, CARD_W - 50, 150, self.chips_top - 80
        if self.hist:
            t0, t1 = self.hist[0][0], self.hist[-1][0]
            top = max(s for _, s in self.hist) * 1.12 or 1
            self.pts = [(self.cx0 + (t - t0) / max(1, t1 - t0) * (self.cx1 - self.cx0),
                         self.cy1 - s / top * (self.cy1 - self.cy0), s) for t, s in self.hist]
            self.top = top
            g = Image.new("L", (1, self.cy1 - self.cy0))
            g.putdata([int(110 * (1 - i / g.height)) for i in range(g.height)])
            self.grad = g.resize((CARD_W, self.cy1 - self.cy0))

    def _header(self, d):
        draw_star(d, 66, 72, 26, fx.GOLD)
        d.text((104, 44), "Star growth" if self.hist else "Stars", font=font(44), fill=WHITE)
        name = self.info.get("name", "")
        f = font(28)
        while f.getlength(name) > 400 and len(name) > 6:
            name = name[:-2]
        d.text((CARD_W - 40 - f.getlength(name), 56), name, font=f, fill=GREY)

    def _chart(self, img, lt):
        d = ImageDraw.Draw(img)
        lf = font(26)
        for k in (0.0, 0.5, 1.0):  # gridlines with real values
            y = self.cy1 - k * (self.cy1 - self.cy0) / 1.12
            d.line([(self.cx0, y), (self.cx1, y)], fill=(40, 46, 56), width=2)
            d.text((24, y - 14), short_num(self.top / 1.12 * k), font=lf, fill=GREY)
        t0 = self.hist[0][0]
        d.text((self.cx0, self.cy1 + 14), time.strftime("%b %d", time.gmtime(t0)), font=lf, fill=GREY)
        d.text((self.cx1 - lf.getlength("today"), self.cy1 + 14), "today", font=lf, fill=GREY)
        p = fx.ease_in_out((lt - 0.3) / 1.7)
        xlim = self.cx0 + p * (self.cx1 - self.cx0)
        pts = [(x, y) for x, y, _ in self.pts if x <= xlim]
        head_val = self.pts[0][2]
        for (x0, y0, s0), (x1, y1, s1) in zip(self.pts, self.pts[1:]):
            if x0 <= xlim < x1:
                k = (xlim - x0) / max(1e-6, x1 - x0)
                pts.append((xlim, y0 + (y1 - y0) * k))
                head_val = s0 + (s1 - s0) * k
                break
            if xlim >= x1:
                head_val = s1
        if len(pts) < 2:
            return
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        poly = Image.new("L", img.size, 0)
        ImageDraw.Draw(poly).polygon(pts + [(pts[-1][0], self.cy1), (pts[0][0], self.cy1)], fill=255)
        fill = Image.new("L", img.size, 0)
        fill.paste(self.grad, (0, self.cy0))
        from PIL import ImageChops
        layer.paste(self.theme.accent + (255,), (0, 0), ImageChops.multiply(poly, fill))
        ld = ImageDraw.Draw(layer)
        ld.line(pts, fill=self.theme.accent + (70,), width=18, joint="curve")
        ld.line(pts, fill=self.theme.accent + (255,), width=6, joint="curve")
        hx, hy = pts[-1]
        ld.ellipse([hx - 18, hy - 18, hx + 18, hy + 18], fill=self.theme.accent + (90,))
        ld.ellipse([hx - 9, hy - 9, hx + 9, hy + 9], fill=WHITE + (255,))
        img.paste(layer, (0, 0), layer)
        label = f"{int(head_val):,}"
        f = font(46)
        lx = min(max(hx - f.getlength(label) / 2, 20), CARD_W - 20 - f.getlength(label))
        d.text((lx, hy - 76), label, font=f, fill=self.theme.now)

    def _growth_card(self, img, lt):
        d = ImageDraw.Draw(img)
        k = ease_out((lt - 0.2) / 1.3)
        val = f"{int(self.info['stars'] * k):,}"
        big = font(150)
        cy = 130 + max(0, (self.chips_top - 130 - 400) / 2)  # number block centred above the chips
        centered(d, cy, val, big, WHITE, width=CARD_W)
        centered(d, cy + 175, "stars on GitHub", font(40), GREY, width=CARD_W)
        created = self.info.get("created_at")
        if created and lt > 1.0:
            days = max(1, int((time.time() - fx_ts(created)) // 86400))
            if days <= 365:
                a = ease_out((lt - 1.0) / 0.4)
                col = tuple(int(c * a + 20 * (1 - a)) for c in self.theme.now)
                centered(d, cy + 240, f"in {days} day{'s' if days != 1 else ''}", font(66), col, width=CARD_W)
                avg = self.info["stars"] / days
                if days > 1:
                    centered(d, cy + 330, f"about {short_num(avg)} new stars a day on average", font(32), GREY,
                             width=CARD_W)

    def _chips(self, img, lt):
        f = font(34)
        for i, (c, (x, dy, w)) in enumerate(zip(self.chips, self.chip_pos)):
            y = self.chips_top + dy
            k = fx.spring(lt - 1.9 - 0.12 * i)
            dot = LANG_COLOURS.get(c) if c == self.info.get("language") else None
            if k > 0:
                chip = Image.new("RGBA", (w, 70), (0, 0, 0, 0))
                cd = ImageDraw.Draw(chip)
                cd.rounded_rectangle([0, 0, w - 1, 69], 35, fill=(32, 38, 48, 255), outline=(60, 68, 82, 255), width=2)
                tx = 30
                if dot:
                    cd.ellipse([26, 24, 48, 46], fill=dot)
                    tx += 34
                cd.text((tx, 14), c, font=f, fill=WHITE)
                paste_scaled(img, chip, (x + w / 2, y + 35), min(1.1, k))

    def frame(self, lt):
        img = self.base(self.theme)
        self._header(ImageDraw.Draw(img))
        (self._chart if self.hist else self._growth_card)(img, lt)
        self._chips(img, lt)
        return img


class TermPanel(_Panel):
    """The README's install commands typed out, char by char, with a blinking cursor."""
    CPS = 26  # characters per second
    GAP = 0.35

    def __init__(self, commands, theme, h, title="Terminal"):
        super().__init__(h)
        self.theme, self.title = theme, title
        longest = max(len(c) for c in commands)
        self.f = mono(44 if longest <= 30 and len(commands) <= 2 else 36 if longest <= 38 else 31)
        cols = int((CARD_W - 90) // self.f.getlength("m")) - 2
        self.lines = []  # (text, is_continuation)
        for c in commands:
            first = True
            while c:
                self.lines.append((c[:cols], not first))
                c, first = c[cols:], False
        self.starts, t = [], 0.5
        for text, cont in self.lines:
            self.starts.append(t)
            t += len(text) / self.CPS + (0.05 if cont else self.GAP)
        self.done = t

    def command_times(self):
        return [s for s, (_, cont) in zip(self.starts, self.lines) if not cont]

    def frame(self, lt):
        img = self.base(self.theme)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, CARD_W - 1, 70], 26, fill=(30, 34, 42))
        d.rectangle([0, 40, CARD_W - 1, 70], fill=(30, 34, 42))
        for i, col in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
            d.ellipse([26 + i * 34, 25, 46 + i * 34, 45], fill=col)
        centered(d, 20, self.title, font(28), GREY, width=CARD_W)
        lh = int(self.f.size * 1.55)
        block = lh * (len(self.lines) + 2.4)
        top = max(110, int((self.h - block) / 2) - 40)  # few lines: centred, not lost at the top
        d.text((40, top), "# install", font=self.f, fill=(110, 118, 135))
        y = top + lh * 1.4
        cursor = None
        for (text, cont), start in zip(self.lines, self.starts):
            if lt < start:
                break
            n = min(len(text), int((lt - start) * self.CPS))
            x = 40
            if not cont:
                d.text((x, y), "$", font=self.f, fill=self.theme.accent)
            x += self.f.getlength("$ ")
            d.text((x, y), text[:n], font=self.f, fill=WHITE)
            cursor = (x + self.f.getlength(text[:n]), y)
            y += lh
        if lt >= self.done:
            d.text((40, y), "$", font=self.f, fill=self.theme.accent)
            cursor = (40 + self.f.getlength("$ "), y)
        if cursor and int(lt * 2.4) % 2 == 0:
            cx, cy = cursor
            d.rectangle([cx + 2, cy + 4, cx + self.f.getlength("m"), cy + self.f.size + 4], fill=self.theme.accent)
        centered(d, self.h - 70, "From the project's README", font(28), (110, 118, 135), width=CARD_W)
        return img


class ComparePanel(_Panel):
    """Before/after slider over two real images (catalog-provided examples only)."""

    def __init__(self, before, after, theme, h):
        super().__init__(h)
        self.theme = theme
        self.a = ImageOps.fit(Image.open(before).convert("RGB"), (CARD_W, h), Image.LANCZOS)
        self.b = ImageOps.fit(Image.open(after).convert("RGB"), (CARD_W, h), Image.LANCZOS)

    def frame(self, lt):
        # 85% -> 15% -> settle at 50%
        if lt < 2.0:
            k = 0.85 - 0.70 * fx.ease_in_out((lt - 0.4) / 1.6)
        else:
            k = 0.15 + 0.35 * fx.ease_in_out((lt - 2.0) / 0.8)
        x = int(CARD_W * k)
        img = self.b.copy()
        img.paste(self.a.crop((0, 0, x, self.h)), (0, 0))
        d = ImageDraw.Draw(img)
        d.line([(x, 0), (x, self.h)], fill=WHITE, width=5)
        d.ellipse([x - 34, self.h / 2 - 34, x + 34, self.h / 2 + 34], fill=WHITE)
        d.text((x - 22, self.h / 2 - 22), "<>", font=font(34), fill=(20, 20, 24))
        for text, left in (("BEFORE", True), ("AFTER", False)):
            tag = pill(text, font(28), (0, 0, 0, 170), WHITE, spacing=3)
            img.paste(tag, (24 if left else CARD_W - 24 - tag.width, 24), tag)
        return img


class PodiumPanel(_Panel):
    """Top 3 of the weekly ranking on a podium: blocks rise, avatars drop in, names and stars."""

    def __init__(self, items, theme, h):
        super().__init__(h)
        self.theme = theme
        self.items = items[:3]
        self.avatars = []
        for it in self.items:
            av = None
            if it.get("avatar"):
                try:
                    av = ImageOps.fit(Image.open(it["avatar"]).convert("RGB"), (150, 150), Image.LANCZOS)
                except Exception:
                    av = None
            self.avatars.append(av)

    def frame(self, lt):
        img = self.base(self.theme)
        d = ImageDraw.Draw(img)
        centered(d, 40, "This week's top 3", font(48), WHITE, width=CARD_W)
        floor = self.h - 60
        slots = [(480, 0.62, 0.75), (190, 0.46, 0.5), (770, 0.34, 0.3)]  # 1st centre, 2nd left, 3rd right
        for rank, ((cx, frac, delay), it, av) in enumerate(zip(slots, self.items, self.avatars), 1):
            k = fx.spring(lt - delay)
            if k <= 0:
                continue
            bh = int((self.h - 420) * frac * min(1.08, k))
            x0, x1 = cx - 125, cx + 125
            top = floor - bh
            col = fx.GOLD if rank == 1 else self.theme.accent
            d.rounded_rectangle([x0, top, x1, floor], 18, fill=tuple(int(c * 0.55) for c in col))
            d.rectangle([x0, top, x1, top + 10], fill=col)
            centered(d, top + 24, str(rank), font(110), WHITE, width=250, x0=x0)
            a = fx.spring(lt - delay - 0.35)
            if a > 0:
                ay = top - 205 - int(60 * (1 - min(1.0, a)))
                if av:
                    m = Image.new("L", av.size, 0)
                    ImageDraw.Draw(m).ellipse([0, 0, 149, 149], fill=255)
                    d.ellipse([cx - 81, ay - 6, cx + 81, ay + 156], fill=col)
                    img.paste(av, (cx - 75, ay), m)
                name = it["name"].split("/")[-1]
                f = font(30)
                while f.getlength(name) > 280 and len(name) > 6:
                    name = name[:-2]
                centered(d, top - 46, name, f, WHITE, width=300, x0=cx - 150)
                if it.get("stars") is not None:
                    label, sf = short_num(it["stars"]), font(32)
                    lx = cx - (sf.getlength(label) + 36) / 2
                    draw_star(d, lx + 14, ay - 32, 15, self.theme.now)
                    d.text((lx + 36, ay - 50), label, font=sf, fill=self.theme.now)
        return img


# ---------------------------------------------------------------- logo intro
@lru_cache(maxsize=32)
def _badge(path, size, ring):
    src = Image.open(path).convert("RGBA")
    src.thumbnail((int(size * 0.7), int(size * 0.7)), Image.LANCZOS)
    b = Image.new("RGBA", (size + 24, size + 24), (0, 0, 0, 0))
    d = ImageDraw.Draw(b)
    d.rounded_rectangle([0, 0, size + 23, size + 23], (size + 24) // 4, fill=ring + (110,))
    d.rounded_rectangle([8, 8, size + 15, size + 15], (size + 8) // 4, fill=(255, 255, 255, 255))
    b.paste(src, ((b.width - src.width) // 2, (b.height - src.height) // 2), src)
    return b


BADGE = 64  # label badge size


def draw_logo(img, path, lt, center, badge_xy, theme):
    """0-0.45 s: the logo pops in big over the visual; 0.45-0.8 s: it flies into the label; then a badge."""
    if lt < 0:
        return
    big = 230
    if lt < 0.45:
        b = _badge(path, big, theme.accent)
        paste_scaled(img, b, center, min(1.1, fx.spring(lt * 1.6)))
        return
    k = fx.ease_in_out((lt - 0.45) / 0.35)
    size = big + (BADGE - big) * k
    bx, by = badge_xy[0] + BADGE / 2 + 12, badge_xy[1] + BADGE / 2 + 12
    x, y = center[0] + (bx - center[0]) * k, center[1] + (by - center[1]) * k
    b = _badge(path, big if k < 1 else BADGE, theme.accent)
    paste_scaled(img, b, (x, y), size / big if k < 1 else 1.0)
