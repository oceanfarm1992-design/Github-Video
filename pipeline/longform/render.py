"""16:9 renderer for long videos (YouTube + Facebook): 1920x1080 at 24 fps.

Timeline: intro card -> per chapter (chapter card, then one segment per narrated paragraph) -> end card.
Each paragraph shows its cited visual: the page in a browser window with the camera gliding to the section
being explained (spotlight + self-drawing outline), the README code revealed line by line, or the stats
panel. Captions show the sentence being spoken. Frames are redrawn only when something changes (camera
moves, a new caption, the progress bar each second), so a 14-minute video renders in minutes.
"""
import logging
import math
import os
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

from .. import fx, music, render as short_render, sfx, visuals
from ..theme import THEMES

log = logging.getLogger("longform.render")
W, H, FPS = 1920, 1080, 24
PX0, PY0, PW, PH = 100, 118, 1720, 780  # browser-window slot
BAR = 56
INTRO, CARD, END, PAD = 4.0, 2.2, 10.0, 0.35
GLIDE, FADE = 1.0, 0.4
CAPTION_Y = 922
STATS_ZOOM = 1.0  # the stats card is laid out for 780 px; scaling it down then up would cramp it


def font(size):
    return visuals.font(size)


def ease(t):
    return fx.ease_in_out(t)


# ------------------------------------------------------------------ panels
class DeskPanel:
    """A desktop page capture in a browser window; the camera zooms to a section and spotlights it."""

    def __init__(self, path, url, theme):
        self.page = fx.grade(Image.open(path).convert("RGB"))
        self.theme = theme
        self.vw, self.vh = PW, PH - BAR
        self.scale = self.page.width / 1280  # capture px per CSS px
        chrome = Image.new("RGB", (PW, PH), (13, 17, 23))
        d = ImageDraw.Draw(chrome)
        d.rectangle([0, 0, PW, BAR], fill=(36, 41, 47))
        for i, col in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
            d.ellipse([22 + i * 30, 19, 40 + i * 30, 37], fill=col)
        d.rounded_rectangle([130, 11, PW - 22, BAR - 11], 17, fill=(22, 27, 34))
        label = url.replace("https://", "")[:110]
        d.text((152, 16), label, font=font(24), fill=(201, 209, 217))
        self.chrome = chrome
        self.mask = visuals.rounded_mask(PW, PH, 22)

    def target(self, box):
        """(cx, cy, zoom, spot) in page px for a CSS box; None = top of the page."""
        full = self.page.width / self.vw  # page px per view px at zoom 1
        if not box:
            return self.page.width / 2, self.vh * full / 2, 1.0, None
        x, y, w, h = (v * self.scale for v in box)
        zoom = min(self.page.width * 0.92 / max(w, 1), self.vh * full * 0.72 / max(h, 1), 2.2)
        return x + w / 2, y + h / 2, max(1.0, zoom), (x, y, w, h)

    def frame(self, cx, cy, zoom, spot=None, k=1.0):
        full = self.page.width / self.vw
        ww, hh = self.page.width / zoom, self.vh * full / zoom
        cx = min(max(cx, ww / 2), self.page.width - ww / 2)
        cy = min(max(cy, hh / 2), max(hh / 2, self.page.height - hh / 2))
        x0, y0 = cx - ww / 2, cy - hh / 2
        view = self.page.resize((self.vw, self.vh), Image.BILINEAR, box=(x0, y0, x0 + ww, y0 + hh))
        if spot and k > 0.01:
            f = self.vw / ww
            sx, sy, sw, sh = spot
            r = [(sx - x0) * f - 12, (sy - y0) * f - 12, (sx + sw - x0) * f + 12, (sy + sh - y0) * f + 12]
            shade = Image.new("L", view.size, int(130 * k))
            ImageDraw.Draw(shade).rounded_rectangle(r, 16, fill=0)
            view = Image.composite(Image.new("RGB", view.size, (0, 0, 0)), view, shade)
            view = fx.draw_on_outline(view, r, k, colour=self.theme.edge)
        img = self.chrome.copy()
        img.paste(view, (0, BAR))
        return img


class CodePanel:
    """A README code block in an editor window, revealed line by line; the newest line is highlighted."""

    def __init__(self, code, theme):
        self.theme, self.lines = theme, code["lines"]
        longest = max((len(x) for x in self.lines), default=10)
        self.f = visuals.mono(34 if longest <= 72 else 28 if longest <= 88 else 24)
        self.lang = code.get("lang") or "text"
        self.mask = visuals.rounded_mask(PW, PH, 22)

    def frame(self, shown):
        img = Image.new("RGB", (PW, PH), (13, 17, 23))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, PW, BAR], fill=(30, 34, 42))
        for i, col in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
            d.ellipse([22 + i * 30, 19, 40 + i * 30, 37], fill=col)
        visuals.centered(d, 15, f"README  ·  {self.lang}", font(24), (150, 160, 180), width=PW)
        lh = int(self.f.size * 1.5)
        y = BAR + 34
        cw = self.f.getlength("m")
        max_chars = int((PW - 140) / cw)
        for i, line in enumerate(self.lines[:shown]):
            if y > PH - 70:
                break
            if i == shown - 1:
                d.rectangle([0, y - 6, PW, y + lh - 10], fill=(28, 36, 48))
            d.text((24, y), f"{i + 1:>3}", font=self.f, fill=(90, 98, 115))
            d.text((24 + cw * 5, y), line[:max_chars], font=self.f, fill=(230, 235, 245))
            y += lh
        visuals.centered(d, PH - 50, "From the project's README", font(24), (110, 118, 135), width=PW)
        return img


# ---------------------------------------------------------------- captions
def caption_chunks(text, limit=16):
    """Sentences (long ones split into <= `limit`-word chunks) for on-screen captions."""
    out = []
    for s in re.split(r"(?<=[.!?])\s+", text.strip()):
        ws = s.split()
        n = max(1, math.ceil(len(ws) / limit))  # balanced chunks, no one-word leftovers
        size = math.ceil(len(ws) / n)
        out += [" ".join(ws[i:i + size]) for i in range(0, len(ws), size)]
    return [c for c in out if c]


def draw_caption(img, text):
    f = font(44)
    lines = visuals.wrap(text, f, 1560)[:2]
    if not lines:
        return
    w = max(f.getlength(x) for x in lines) + 60
    h = len(lines) * 56 + 30
    x0, y0 = (W - w) / 2, CAPTION_Y + (110 - h) / 2
    layer = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle([0, 0, w - 1, h - 1], 20, fill=(0, 0, 0, 170))
    img.paste(layer, (int(x0), int(y0)), layer)
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        visuals.centered(d, y0 + 14 + i * 56, line, f, (255, 255, 255), width=W)


# ------------------------------------------------------------------- cards
def draw_card(img, theme, tag, title, sub, lt):
    g = visuals.glow(theme.accent, 1500, 700)
    img.paste(g, ((W - g.width) // 2, 540 - g.height // 2), g)
    s = 1 + 0.06 * (1 - fx.spring(lt * 1.3))
    f = font(96)
    lines = visuals.wrap(title, f, 1600)[:3]
    while len(lines) > 2 and f.size > 60:
        f = font(f.size - 8)
        lines = visuals.wrap(title, f, 1600)[:3]
    layer = Image.new("RGBA", (1700, int(len(lines) * f.size * 1.15) + 20), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    for i, line in enumerate(lines):
        visuals.centered(ld, i * f.size * 1.15, line, f, (255, 255, 255), width=1700)
    visuals.paste_scaled(img, layer, (W / 2, 520), s)
    tg = visuals.pill(tag, font(30), theme.accent + (255,), (10, 10, 14), spacing=4)
    img.paste(tg, ((W - tg.width) // 2, int(520 - layer.height * s / 2 - 90)), tg)
    d = ImageDraw.Draw(img)
    y = 520 + layer.height * s / 2 + 24
    bar = int(600 * (0.3 + 0.7 * visuals.ease_out(lt / 0.6)))
    d.rounded_rectangle([(W - bar) / 2, y, (W + bar) / 2, y + 10], 5, fill=theme.accent)
    if sub:
        visuals.centered(d, y + 40, sub, font(46), theme.now, width=W)


def draw_end(img, theme, lt, n_links):
    d = ImageDraw.Draw(img)
    visuals.centered(d, 250, "Thanks for watching", font(84), (255, 255, 255), width=W)
    k = fx.spring(lt - 0.3)
    if k > 0:
        btn = visuals.pill("SUBSCRIBE", font(64), theme.accent + (255,), (10, 10, 14), pad_x=80, height=130)
        visuals.paste_scaled(img, btn, (W / 2, 470), min(1.1, k) * (1 + 0.03 * math.sin(lt * 6)))
    visuals.centered(d, 600, "Which part was most useful? Tell me in the comments.", font(46), theme.now, width=W)
    what = "All links are in the description" if n_links > 1 else "The link is in the description"
    visuals.centered(d, 680, what, font(40), (170, 178, 196), width=W)


def draw_chrome(img, theme, chip, t, total, ticks):
    """Chapter chip (top left), progress bar with chapter ticks (bottom)."""
    tg = visuals.pill(chip, font(30), theme.accent + (230,), (10, 10, 14), pad_x=26)
    img.paste(tg, (PX0, 40), tg)
    wm = os.environ.get("WATERMARK_TEXT") or ""
    if wm:
        d = ImageDraw.Draw(img)
        d.text((W - PX0 - font(28).getlength(wm), 50), wm, font=font(28), fill=(120, 130, 150))
    d = ImageDraw.Draw(img)
    d.rectangle([0, H - 8, W, H], fill=(30, 34, 44))
    d.rectangle([0, H - 8, int(W * t / total), H], fill=theme.accent)
    for x in ticks:
        d.rectangle([int(W * x / total) - 1, H - 8, int(W * x / total) + 1, H], fill=(10, 10, 14))


# ---------------------------------------------------------------- timeline
def timeline(script, clips):
    """[(kind, start, dur, data)] segments; clips: per paragraph (path, seconds)."""
    segs, t, k = [("intro", 0.0, INTRO, None)], INTRO, 0
    for ci, ch in enumerate(script["chapters"]):
        segs.append(("card", t, CARD, (ci, ch["title"])))
        t += CARD
        for p in ch["paragraphs"]:
            dur = clips[k][1] + PAD
            segs.append(("para", t, dur, (ci, p, k)))
            t += dur
            k += 1
    segs.append(("end", t, END, None))
    return segs, t + END


def narration(segs, clips, total, out_dir, stem):
    """One WAV: silence under the cards, each paragraph clip at its segment start."""
    parts, n = [], 0
    for kind, start, dur, data in segs:
        piece = Path(out_dir) / f"{stem}_n{n:03d}.wav"
        n += 1
        if kind == "para":
            src = clips[data[2]][0]
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-af",
                   f"aresample=44100,afade=t=in:d=0.015,apad=whole_dur={dur:.3f}", "-t", f"{dur:.3f}",
                   "-ac", "1", "-c:a", "pcm_s16le", str(piece)]
        else:
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                   "-t", f"{dur:.3f}", "-c:a", "pcm_s16le", str(piece)]
        subprocess.run(cmd, check=True, timeout=120)
        parts.append(piece)
    lst = Path(out_dir) / f"{stem}_list.txt"
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8")
    out = Path(out_dir) / f"{stem}_voice.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-t", f"{total:.3f}", "-c:a", "pcm_s16le", str(out)], check=True, timeout=600)
    for p in parts:
        p.unlink(missing_ok=True)
    return out


# ------------------------------------------------------------------ frames
class Frames:
    def __init__(self, m, script, segs, total, theme):
        self.m, self.script, self.segs, self.total, self.theme = m, script, segs, total, theme
        self.backdrop = fx.Backdrop(theme.bg_top, theme.bg_bot, accent=theme.accent, size=(W, H)).frame(0)
        self.pages = {}
        self.vis = {v["id"]: v for v in m["visuals"]}
        self.ticks = [s[1] for s in segs if s[0] == "card"]
        self.n_ch = len(script["chapters"])
        self.stats = None

    def page(self, i):
        if i not in self.pages:
            path, url, _ = self.m["pages"][i]
            self.pages[i] = DeskPanel(path, url, self.theme)
        return self.pages[i]

    def visual_state(self, v, lt, dur, prev_v):
        """(key, draw) for the paragraph's visual at local time lt."""
        if v["kind"] in ("page", "overview"):
            panel = self.page(v["page"])
            cur = panel.target(v.get("box") if v["kind"] == "page" else None)
            same_page = prev_v and prev_v["kind"] in ("page", "overview") and prev_v["page"] == v["page"]
            prev = panel.target(prev_v.get("box") if prev_v["kind"] == "page" else None) if same_page else cur
            k = ease(lt / GLIDE) if same_page else 1.0
            fade = 1.0 if same_page or not prev_v else min(1.0, lt / FADE)
            key = ("p", v["id"], round(k, 3) if k < 1 else 1, round(fade, 2) if fade < 1 else 1)

            def draw():
                cx, cy = prev[0] + (cur[0] - prev[0]) * k, prev[1] + (cur[1] - prev[1]) * k
                z = prev[2] + (cur[2] - prev[2]) * k
                spot_k = ease((lt - (GLIDE if same_page else 0.2)) / 0.6) if cur[3] else 0
                return panel.frame(cx, cy, z, cur[3], spot_k), fade
            return key, draw
        if v["kind"] == "code":
            panel = CodePanel(v["code"], self.theme)
            n = len(v["code"]["lines"])
            shown = max(1, min(n, 1 + int(n * lt / max(1.0, dur * 0.7))))
            fade = min(1.0, lt / FADE)
            return ("c", v["id"], shown, round(fade, 2) if fade < 1 else 1), lambda: (panel.frame(shown), fade)
        if v["kind"] == "stats":
            if self.stats is None:
                self.stats = visuals.StatsPanel(self.m["stats"], self.theme, int(PH / STATS_ZOOM))
            al = min(lt, 3.4)
            fade = min(1.0, lt / FADE)

            def draw_stats():  # the 960-px panel, scaled up to fill the 16:9 frame
                img = self.stats.frame(al)
                return img.resize((int(img.width * STATS_ZOOM), int(img.height * STATS_ZOOM)), Image.LANCZOS), fade
            return ("s", round(al * FPS) if lt < 3.4 else -1, round(fade, 2) if fade < 1 else 1), draw_stats
        return ("none",), lambda: (None, 1.0)

    def compose(self, panel_img, fade, chip, caption, t):
        img = self.backdrop.copy()
        if panel_img is not None:
            if panel_img.width == PW:
                x, y, mask = PX0, PY0, visuals.rounded_mask(PW, PH, 22)
            else:  # stats panel (960 wide): centred
                x, y, mask = (W - panel_img.width) // 2, PY0, visuals.rounded_mask(panel_img.width, panel_img.height)
            if fade < 1:
                mask = mask.point(lambda a, f=fade: int(a * f))
            img.paste(panel_img, (x, y), mask)
        draw_chrome(img, self.theme, chip, t, self.total, self.ticks)
        if caption:
            draw_caption(img, caption)
        return fx.vignette(img)

    def __iter__(self):
        last_key, last = None, None
        prev_v = None
        n = round(self.total * FPS)
        si = 0
        for fi in range(n):
            t = fi / FPS
            while si + 1 < len(self.segs) and t >= self.segs[si + 1][1]:
                if self.segs[si][0] == "para":
                    prev_v = self.vis[self.segs[si][3][1]["visual"]]
                si += 1
            kind, start, dur, data = self.segs[si]
            lt = t - start
            sec = int(t)
            if kind == "intro":
                a = min(lt, 0.9)
                key = ("intro", round(a * FPS) if lt < 0.9 else -1)
                if key != last_key:
                    img = self.backdrop.copy()
                    draw_card(img, self.theme, self.theme.tag, self.script["title"], self.m["subject"], a)
                    last = fx.vignette(img).tobytes()
            elif kind == "card":
                ci, title = data
                a = min(lt, 0.8)
                key = ("card", ci, round(a * FPS) if lt < 0.8 else -1, sec)
                if key != last_key:
                    img = self.backdrop.copy()
                    draw_card(img, self.theme, f"CHAPTER {ci + 1} OF {self.n_ch}", title, None, a)
                    draw_chrome(img, self.theme, self.theme.tag, t, self.total, self.ticks)
                    last = fx.vignette(img).tobytes()
                prev_v = None if ci == 0 else prev_v
            elif kind == "para":
                ci, p, k = data
                v = self.vis[p["visual"]]
                chunks = caption_chunks(p["text"])
                lens = [len(c) for c in chunks]
                speak = max(0.1, dur - PAD)
                acc, idx = 0.0, 0
                for i, ln in enumerate(lens):
                    acc += speak * ln / max(1, sum(lens))
                    if lt < acc:
                        idx = i
                        break
                    idx = i
                vkey, draw = self.visual_state(v, lt, dur, prev_v)
                key = ("para", k, vkey, idx, sec)
                if key != last_key:
                    panel_img, fade = draw()
                    chip = f"{ci + 1}/{self.n_ch}  {self.script['chapters'][ci]['title']}"
                    last = self.compose(panel_img, fade, chip, chunks[idx] if chunks else "", t).tobytes()
            else:  # end card
                a = min(lt, 1.6)
                key = ("end", round(a * FPS) if lt < 1.6 else round(lt * 4), sec)
                if key != last_key:
                    img = self.backdrop.copy()
                    draw_end(img, self.theme, lt, len(self.m["links"]))
                    last = fx.vignette(img).tobytes()
            last_key = key
            yield last


# ------------------------------------------------------------------ render
def render(m, script, out_dir):
    """Renders the long video; returns (mp4_path, chapters [(seconds, title)], thumbnail_path)."""
    out_dir = Path(out_dir)
    theme = THEMES.get(m["format"], THEMES["github"])
    texts = [p["text"] for ch in script["chapters"] for p in ch["paragraphs"]]
    clips = short_render.narrate(texts, out_dir)
    if not clips:
        raise RuntimeError("narration failed; no long video")
    segs, total = timeline(script, clips)
    stem = f"long_{m['format']}_{abs(hash(m['key'])) % 10**8}"
    voice = narration(segs, clips, total, out_dir, stem)
    cues = [(0.0, "impact")] + [(s[1] - 0.06, "whoosh") for s in segs if s[0] == "card"]
    cues += [(segs[-1][1], "end")]
    audio = voice
    try:
        source = "github" if m["format"] == "github" else f"{m['format']}:long"
        track = music.track("hacker", total, out_dir, music.flavor_for(source))
        audio = short_render.mix_music(voice, track, total, out_dir / f"{stem}_mix.m4a", cues)
        audio = short_render.mix_sfx(audio, sfx.track(cues, total, out_dir / f"{stem}_sfx.wav"), total,
                                     out_dir / f"{stem}_sfxmix.m4a")
    except Exception as e:
        log.warning("long video music/sfx skipped: %s", e)
    # thumbnail: the intro card, 1280x720
    thumb = out_dir / f"{stem}.jpg"
    img = fx.Backdrop(theme.bg_top, theme.bg_bot, accent=theme.accent, size=(W, H)).frame(0)
    draw_card(img, theme, theme.tag, script["title"], m["subject"], 2.0)
    fx.vignette(img).resize((1280, 720), Image.LANCZOS).save(thumb, quality=92)
    mp4 = out_dir / f"{stem}.mp4"
    af = f"{short_render.loudnorm(audio)},alimiter=limit=0.84:level=false,aresample=44100"
    enc = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-i", str(audio), "-t", f"{total:.3f}",
           "-vf", f"fade=t=out:st={total - 1:.3f}:d=1,format=yuv420p", "-af", af,
           # "fast", not "slow": a 15-minute video must stay well inside the run's time limit
           "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-profile:v", "high", "-g", str(FPS * 4),
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(mp4)]
    proc = subprocess.Popen(enc, stdin=subprocess.PIPE)
    try:
        for frame in Frames(m, script, segs, total, theme):
            proc.stdin.write(frame)
        proc.stdin.close()
        if proc.wait(timeout=3600) != 0:
            raise RuntimeError("ffmpeg encode failed")
    finally:
        if proc.poll() is None:
            proc.kill()
    cards = [(s[1], s[3][1]) for s in segs if s[0] == "card"]
    chapters = [(0.0, cards[0][1])] + cards[1:]  # YouTube chapters must start at 0:00
    return mp4, chapters, thumb
