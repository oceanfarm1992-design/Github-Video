"""RENDER: Pillow frames + FFmpeg. 1080x1920, H.264/AAC. Assets cached by content hash.

Visuals: the project's official preview image (GitHub social-preview card, or the page's og:image).
Voice: one TTS clip per scene so each slide lasts exactly as long as its narration.
"""
import asyncio
import io
import json
import logging
import os
import re
import shutil
import subprocess
import textwrap
import urllib.parse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from . import config, db
from .http import request

log = logging.getLogger("render")
W, H = 1080, 1920
WPS = 2.6  # spoken words/sec, only used when TTS is unavailable
PAD = 0.4  # seconds of breathing room after each narrated scene
VOICE = os.environ.get("TTS_VOICE", "en-US-AndrewMultilingualNeural")
RATE = os.environ.get("TTS_RATE", "+4%")


def _font(size):
    for name in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf", "LiberationSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


# ------------------------------------------------------------------ images
def image_url(topic):
    """Official, shareable preview image for the topic, or None."""
    if topic["source"] == "github" or "github.com/" in (topic["github_url"] or ""):
        m = re.search(r"github\.com/([^/]+/[^/?#]+)", topic["github_url"] or topic["url"])
        if m:
            return f"https://opengraph.githubassets.com/1/{m.group(1)}"
    try:
        _, _, body = request(topic["url"], headers={"Accept": "text/html"}, attempts=2)
    except RuntimeError:
        return None
    html = body[:300_000].decode("utf-8", "replace")
    for pat in (r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
                r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)'):
        m = re.search(pat, html, re.I)
        if m:
            return urllib.parse.urljoin(topic["url"], m.group(1).replace("&amp;", "&"))
    return None


def fetch_image(url, out_dir):
    """Download + validate with Pillow; cached by URL hash. Returns a Path or None."""
    if not url or not url.startswith("https://"):
        return None
    p = out_dir / f"img_{db.sha(url)[:24]}.png"
    if p.exists():
        return p
    try:
        _, _, body = request(url, attempts=2)
        if len(body) > 8_000_000:
            return None
        img = Image.open(io.BytesIO(body))
        img.load()
        if img.width < 400 or img.height < 200:
            return None
        img.convert("RGB").save(p)
        return p
    except Exception as e:
        log.info("image unavailable (%s): %s", url, e)
        return None


def _card(path, width=960):
    img = Image.open(path).convert("RGB")
    h = min(int(img.height * width / img.width), 760)
    img = ImageOps.fit(img, (width, h), Image.LANCZOS)
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.width, img.height], 28, fill=255)
    out = Image.new("RGBA", img.size)
    out.paste(img, (0, 0), mask)
    return out


def frame(text, idx, total, title, path, card=None):
    img = Image.new("RGB", (W, H), (14, 16, 28))
    d = ImageDraw.Draw(img)
    for y in range(H):  # cheap vertical gradient
        c = int(14 + 30 * y / H)
        d.line([(0, y), (W, y)], fill=(c, c + 4, c + 22))
    d.rectangle([60, 120, 60 + int((W - 120) * (idx + 1) / total), 132], fill=(90, 160, 255))
    d.text((60, 180), title[:40], font=_font(46), fill=(150, 175, 220))
    size = 66 if len(text) < 90 else 54
    lines = textwrap.wrap(text, width=int(1000 / (size * 0.55)))
    text_h = len(lines) * int(size * 1.3)
    c = _card(card) if card is not None else None
    block = (c.height + 80 if c else 0) + text_h
    y = max(260, (H - block) // 2 - 40)  # vertically centre the card + text block
    if c is not None:
        d.rounded_rectangle([60 - 6, y - 6, 60 + c.width + 6, y + c.height + 6], 32, fill=(60, 80, 130))
        img.paste(c, (60, y), c)
        y += c.height + 80
    for ln in lines:
        d.text((60, y), ln, font=_font(size), fill=(255, 255, 255))
        y += int(size * 1.3)
    img.save(path)


# ------------------------------------------------------------------- audio
async def _tts(text, out):
    import edge_tts
    await edge_tts.Communicate(text, VOICE, rate=RATE).save(out)


def _duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, timeout=60, check=True)
    return float(r.stdout.strip())


def narrate(texts, out_dir):
    """One cached mp3 per scene. Returns list of (path, seconds) or None if TTS is unavailable."""
    clips = []
    try:
        for t in texts:
            p = out_dir / f"{db.sha(VOICE + RATE + t)[:24]}.mp3"
            if not p.exists():
                asyncio.run(_tts(t, str(p)))
            clips.append((p, _duration(p)))
        return clips
    except Exception as e:
        log.warning("TTS unavailable (%s); using silent audio", e)
        return None


def scenes_for(script):
    s = json.loads(script)
    return [s["hook"]] + s["beats"] + [s["outro"]]


def srt(scenes, path):
    def ts(t):
        return f"{int(t // 3600):02}:{int(t % 3600 // 60):02}:{int(t % 60):02},{int(t % 1 * 1000):03}"
    t, out = 0.0, []
    for i, (text, dur) in enumerate(scenes, 1):
        out.append(f"{i}\n{ts(t)} --> {ts(t + dur)}\n{text}\n")
        t += dur
    Path(path).write_text("\n".join(out), encoding="utf-8")


def render(content, topic=None):
    out = Path(config.OUT_DIR)
    for sub in ("video", "audio", "assets"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    texts = scenes_for(content["script"])
    h = db.sha(content["script"] + content["title"] + VOICE)[:24]
    mp4 = out / "video" / f"{h}.mp4"
    if mp4.exists():
        return str(mp4)  # never regenerate an unchanged asset

    card = fetch_image(image_url(topic), out / "assets") if topic else None
    clips = narrate(texts, out / "audio")
    if clips:
        scenes = [(t, dur + PAD) for t, (_, dur) in zip(texts, clips)]
    else:
        scenes = [(t, max(3.0, len(t.split()) / WPS + 0.6)) for t in texts]
    total = sum(d for _, d in scenes)
    if total < 30:  # pad the last scene (silence) to the 30s floor
        scenes[-1] = (scenes[-1][0], scenes[-1][1] + 30 - total)
        total = 30.0
    if total > 58:
        raise RuntimeError(f"script too long for a Short: {total:.0f}s")

    frames = []
    for i, (text, _) in enumerate(scenes):
        key = db.sha(f"{text}|{content['title']}|{card}|{i}|{len(scenes)}")[:24]
        p = out / "assets" / f"f_{key}.png"
        if not p.exists():
            frame(text, i, len(scenes), content["title"], p, card)
        frames.append(p)
    srt(scenes, out / "video" / f"{h}.srt")
    lst = out / "video" / f"{h}.txt"
    lines = []
    for p, (_, dur) in zip(frames, scenes):
        lines += [f"file '{p.resolve().as_posix()}'", f"duration {dur:.3f}"]
    lines.append(f"file '{frames[-1].resolve().as_posix()}'")
    lst.write_text("\n".join(lines))

    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst)]
    if clips:
        for p, _ in clips:
            cmd += ["-i", str(p)]
        # pad each clip to its scene length so audio and slides stay in lockstep, then join
        parts = "".join(f"[{i + 1}:a]aresample=44100,apad=whole_dur={scenes[i][1]:.3f}[a{i}];" for i in range(len(clips)))
        fc = parts + "".join(f"[a{i}]" for i in range(len(clips))) + f"concat=n={len(clips)}:v=0:a=1,apad[aout]"
        cmd += ["-filter_complex", fc, "-map", "0:v", "-map", "[aout]"]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    cmd += ["-t", f"{total:.3f}", "-vf", f"fps=30,scale={W}:{H},format=yuv420p", "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", str(mp4)]
    subprocess.run(cmd, check=True, timeout=600)
    lst.unlink(missing_ok=True)
    return str(mp4)


def run(conn):
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found")
    n = 0
    rows = conn.execute(
        """SELECT c.*, t.id AS tid FROM contents c JOIN topics t ON t.id=c.topic_id
        WHERE t.status='RENDERING' AND c.video_path IS NULL""").fetchall()
    for c in rows:
        try:
            topic = conn.execute("SELECT * FROM topics WHERE id=?", (c["tid"],)).fetchone()
            path = render(dict(c), topic)
            conn.execute("UPDATE contents SET video_path=?, template='card-v2' WHERE id=?", (path, c["id"]))
            conn.execute("UPDATE topics SET renders=renders+1 WHERE id=?", (c["tid"],))
            db.set_status(conn, c["tid"], "QC")
            n += 1
        except Exception as e:
            log.warning("render %s failed: %s", c["title"], e)
            conn.execute("DELETE FROM contents WHERE id=?", (c["id"],))
            conn.execute("DELETE FROM cta_map WHERE video_id=?", (c["id"],))
            db.fail(conn, c["tid"], e, resume_status="QUEUED")
        conn.commit()
    return n
