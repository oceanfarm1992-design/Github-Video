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
import urllib.parse
from pathlib import Path

from PIL import Image

from . import config, db, motion
from .http import request

log = logging.getLogger("render")
W, H = 1080, 1920
WPS = 2.6  # spoken words/sec, only used when TTS is unavailable
PAD = 0.4  # seconds of breathing room after each narrated scene
VOICE = os.environ.get("TTS_VOICE", "en-US-AndrewMultilingualNeural")
RATE = os.environ.get("TTS_RATE", "+4%")


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

    srt(scenes, out / "video" / f"{h}.srt")

    # 1) narration track, each clip padded to its scene length so audio and slides stay in lockstep
    audio = None
    if clips:
        audio = out / "audio" / f"{h}.m4a"
        cmd = ["ffmpeg", "-y", "-loglevel", "error"]
        for p, _ in clips:
            cmd += ["-i", str(p)]
        parts = "".join(f"[{i}:a]aresample=44100,apad=whole_dur={scenes[i][1]:.3f}[a{i}];" for i in range(len(clips)))
        fc = parts + "".join(f"[a{i}]" for i in range(len(clips))) + f"concat=n={len(clips)}:v=0:a=1,apad[aout]"
        cmd += ["-filter_complex", fc, "-map", "[aout]", "-t", f"{total:.3f}", "-c:a", "aac", "-b:a", "128k", str(audio)]
        subprocess.run(cmd, check=True, timeout=300)

    # 2) animated frames piped straight into the encoder (fade in/out to black, audio fades too)
    fade = f"fade=t=in:st=0:d=0.4,fade=t=out:st={total - 0.5:.3f}:d=0.5,format=yuv420p"
    enc = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(motion.FPS), "-i", "-"]
    enc += ["-i", str(audio)] if audio else ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    enc += ["-t", f"{total:.3f}", "-vf", fade, "-af", f"afade=t=out:st={total - 0.5:.3f}:d=0.5",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", str(mp4)]
    proc = subprocess.Popen(enc, stdin=subprocess.PIPE)
    try:
        for raw in motion.frames(scenes, card, audio, os.environ.get("WATERMARK_TEXT")):
            proc.stdin.write(raw)
        proc.stdin.close()
        if proc.wait(timeout=600) != 0:
            raise RuntimeError("ffmpeg encode failed")
    except BrokenPipeError:
        raise RuntimeError("ffmpeg closed the pipe early")
    finally:
        if proc.poll() is None:
            proc.kill()
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
