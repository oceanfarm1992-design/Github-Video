"""WEEKLY RECAP: the week's videos in about 30 seconds.

On RECAP_WEEKDAY the week's published videos (still hosted in the public `videos` release, kept 14 days)
are cut into a montage: the recap's hook frame, the opening seconds of each video (its own hook frame
and first scene) with a "3/8 · title" banner, whip/panel transitions, the series music, and an end card
with a comment poll. The DM keyword sends every link from the week.
"""
import json
import logging
import os
import subprocess
import time
from pathlib import Path

from PIL import Image, ImageDraw

from . import config, db, fx, generate, motion, music, sfx, visuals
from .http import request
from .theme import THEMES, kind as series_kind

log = logging.getLogger("recap")
MAX_CLIPS, MIN_CLIPS = 8, 6
TARGET = 28.0  # s of video in total


def _weekday_ok():
    return 0 <= config.RECAP_WEEKDAY <= 6 and time.gmtime().tm_wday == config.RECAP_WEEKDAY


def collect(conn, days=7):
    """This week's published videos (oldest first), at most MAX_CLIPS spread over the week."""
    rows = conn.execute(
        """SELECT c.id, c.title, c.video_path, t.source, t.url FROM contents c JOIN topics t ON t.id=c.topic_id
        WHERE t.status='PUBLISHED' AND c.video_path IS NOT NULL AND c.created_at>=? AND t.source NOT LIKE 'recap:%'
        ORDER BY c.created_at""", (time.time() - days * 86400,)).fetchall()
    rows = [dict(r) for r in rows]
    if len(rows) > MAX_CLIPS:
        step = len(rows) / MAX_CLIPS
        rows = [rows[int(i * step)] for i in range(MAX_CLIPS)]
    return [{"title": r["title"], "asset": os.path.basename(r["video_path"]), "kind": series_kind(r["source"]),
             "url": r["url"]} for r in rows]


def build(conn, items=None):
    """Queues this week's recap (topic + content ready to render); returns the topic id or None."""
    week = time.strftime("%G-W%V", time.gmtime())
    tid = db.sha(f"recap:{week}")[:16]
    if conn.execute("SELECT 1 FROM topics WHERE id=?", (tid,)).fetchone():
        return None
    items = items if items is not None else collect(conn)
    if len(items) < MIN_CLIPS:
        log.info("only %d published videos this week; no recap", len(items))
        return None
    n = len(items)
    links = "Here are this week's links:\n" + "\n".join(f"{i}. {it['title']} - {it['url']}"
                                                         for i, it in enumerate(items, 1))
    claims = [{"text": it["title"], "source": it["url"]} for it in items]
    title = "This week in 30 seconds"
    conn.execute(
        """INSERT INTO topics (id,title,source,url,published_at,discovered_at,content_hash,raw,summary,claims,
        source_urls,score,confidence,status,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (tid, title, f"recap:{week}", items[0]["url"], db.now_iso(), db.now_iso(), db.sha(f"recap:{week}"),
         db.js({"items": items, "week": week}), title, db.js(claims), db.js([it["url"] for it in items]),
         100, 100, "RENDERING", time.time()))
    hook = f"Everything from this week in 30 seconds: {n} videos."
    outro = generate.series_outro("LINK", links)
    caption = f"{hook}\n\nWhich one was your favorite?\n\nSource: {items[0]['url']}"
    cur = conn.execute(
        """INSERT INTO contents (topic_id,hook,script,title,caption,hashtags,cta_keyword,sources,duration_target,
        status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (tid, hook, db.js({"hook": hook, "beats": [it["title"] for it in items], "outro": outro}), title, caption,
         db.js(["#AITools", "#OpenSource", "#GitHub", "#TechTips", "#WeeklyRecap"]), "LINK",
         db.js([it["url"] for it in items]), 30, "ready", time.time()))
    conn.execute("INSERT INTO cta_map VALUES (?,?,?,?)", (cur.lastrowid, "LINK", tid, links))
    return tid


def run(conn):
    return 1 if _weekday_ok() and build(conn) else 0


# ------------------------------------------------------------------ render
def _download(asset, out_dir):
    dest = Path(out_dir) / f"recap_{asset}"
    if dest.exists():
        return dest
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        raise RuntimeError("GITHUB_REPOSITORY not set (recaps are built in GitHub Actions)")
    _, _, body = request(f"https://github.com/{repo}/releases/download/videos/{asset}", attempts=2, timeout=120)
    dest.write_bytes(body)
    return dest


def _clip_frames(path, seconds):
    """Raw RGB frames of the first `seconds` of a video, at the engine's size and frame rate."""
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(path), "-t", f"{seconds:.3f}", "-an",
                          "-vf", f"fps={motion.FPS},scale={motion.W}:{motion.H}", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    size, want, count, last = motion.W * motion.H * 3, round(seconds * motion.FPS), 0, None
    try:
        while count < want:
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            last = Image.frombytes("RGB", (motion.W, motion.H), buf)
            count += 1
            yield last
        while last is not None and count < want:  # short clip: hold its last frame
            count += 1
            yield last
    finally:
        p.kill()


def _banner(img, i, n, title, theme):
    """Top banner: "3/8" pill + the video's title over a soft shade."""
    shade = Image.new("L", (motion.W, 330), 0)
    sd = ImageDraw.Draw(shade)
    for y in range(330):
        sd.line([(0, y), (motion.W, y)], fill=int(200 * (1 - y / 330)))
    img.paste((0, 0, 0), (0, 0, motion.W, 330), shade)
    tag = visuals.pill(f"{i}/{n}", motion.font(40), theme.accent + (255,), (10, 10, 14))
    img.paste(tag, (60, 110), tag)
    f = motion.font(46)
    lines = visuals.wrap(title, f, motion.W - 120 - tag.width - 24)[:2]
    d = ImageDraw.Draw(img)
    for k, line in enumerate(lines):
        d.text((60 + tag.width + 24, 108 + k * 56), line, font=f, fill=(255, 255, 255))


def frames(clips, theme, intro, endcard, clip_len):
    """Hook frame -> clips with banners and transitions -> end card. Returns (frame generator, cues)."""
    backdrop = fx.Backdrop(theme.bg_top, theme.bg_bot, accent=theme.accent)
    n = len(clips)
    t0 = visuals.INTRO
    cues = [(0.0, "impact")] + [(t0 + i * clip_len - 0.06, "whoosh") for i in range(n)]
    end_at = t0 + n * clip_len
    cues += [(end_at - 0.06, "whoosh"), (end_at, "end")]
    T = motion.TRANSITION

    def gen():
        fi = 0
        for k in range(round(visuals.INTRO * motion.FPS)):
            img = backdrop.frame(fi)
            visuals.draw_intro(img, intro, theme, k / motion.FPS)
            fi += 1
            yield fx.vignette(img).tobytes()
        prev = img
        for i, (path, title) in enumerate(clips, 1):
            frame = None
            for k, frame in enumerate(_clip_frames(path, clip_len)):
                frame = frame.copy()
                _banner(frame, i, n, title, theme)
                lt = k / motion.FPS
                out = frame
                if lt < T:  # whip-pans alternating with panel wipes
                    out = fx.whip(prev, frame, lt / T) if i % 2 else fx.panel_wipe(prev, frame, lt / T, colour=theme.edge)
                fi += 1
                yield fx.vignette(out).tobytes()
            prev = frame or prev
        for k in range(round(visuals.ENDCARD * motion.FPS)):
            img = backdrop.frame(fi)
            visuals.draw_endcard(img, endcard, theme, k / motion.FPS)
            lt = k / motion.FPS
            out = fx.panel_wipe(prev, img, lt / T, colour=theme.edge) if lt < T else img
            fi += 1
            yield fx.vignette(out).tobytes()
    return gen(), cues


def render(content, topic, mp4):
    from . import render as r  # audio helpers (music mix, sound design, loudness)
    out = Path(config.OUT_DIR)
    raw = json.loads(topic["raw"] or "{}")
    items = raw.get("items", [])
    clips = []
    for it in items:
        try:
            clips.append((_download(it["asset"], out / "assets"), it["title"]))
        except Exception as e:
            log.warning("recap clip %s unavailable: %s", it["asset"], e)
    if len(clips) < MIN_CLIPS:
        raise RuntimeError(f"only {len(clips)} recap clips available")
    theme = THEMES["recap"]
    n = len(clips)
    clip_len = max(2.4, min(4.0, (TARGET - visuals.INTRO - visuals.ENDCARD) / n))
    total = visuals.INTRO + n * clip_len + visuals.ENDCARD
    intro = {"title": "This week in 30 seconds", "sub": f"{n} videos you might have missed", "tag": theme.tag}
    endcard = {"question": "Which one was your favorite?", "options": [str(i) for i in range(1, n + 1)],
               "line": "New videos every day"}
    r.srt([(title, clip_len) for _, title in clips], mp4.with_suffix(".srt"), offset=visuals.INTRO)
    gen, cues = frames(clips, theme, intro, endcard, clip_len)
    audio = None
    try:
        audio = r.mix_music(None, music.track("hacker", total, out / "audio", "recap"), total,
                            out / "audio" / f"{mp4.stem}.mix.m4a", cues)
        audio = r.mix_sfx(audio, sfx.track(cues, total, out / "audio" / f"{mp4.stem}.sfx.wav"), total,
                          out / "audio" / f"{mp4.stem}.sfxmix.m4a")
    except Exception as e:
        log.warning("recap audio: %s", e)
    try:
        visuals.cover(intro, theme, fx.Backdrop(theme.bg_top, theme.bg_bot, accent=theme.accent).frame(0)) \
            .save(mp4.with_suffix(".jpg"), quality=90)
    except Exception as e:
        log.warning("recap cover skipped: %s", e)
    afilter = (f"{r.loudnorm(audio)},alimiter=limit=0.84:level=false," if audio else "") + \
        f"aresample=44100,afade=t=out:st={total - 0.5:.3f}:d=0.5"
    enc = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s",
           f"{motion.W}x{motion.H}", "-r", str(motion.FPS), "-i", "-"]
    enc += ["-i", str(audio)] if audio else ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    enc += ["-t", f"{total:.3f}", "-vf", f"fade=t=out:st={total - 0.5:.3f}:d=0.5,format=yuv420p", "-af", afilter,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", str(mp4)]
    proc = subprocess.Popen(enc, stdin=subprocess.PIPE)
    try:
        for frame in gen:
            proc.stdin.write(frame)
        proc.stdin.close()
        if proc.wait(timeout=600) != 0:
            raise RuntimeError("ffmpeg encode failed")
    finally:
        if proc.poll() is None:
            proc.kill()
    return str(mp4)
