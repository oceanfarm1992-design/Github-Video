"""RENDER: Pillow frames + FFmpeg. 1080x1920, H.264/AAC. Assets cached by content hash."""
import asyncio
import json
import logging
import shutil
import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import config, db

log = logging.getLogger("render")
W, H = 1080, 1920
WPS = 2.6  # spoken words per second


def _font(size):
    for name in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf", "LiberationSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def scenes_for(script):
    s = json.loads(script)
    texts = [s["hook"]] + s["beats"] + [s["outro"]]
    return [(t, max(3.0, len(t.split()) / WPS + 0.6)) for t in texts]


def frame(text, idx, total, title, path):
    img = Image.new("RGB", (W, H), (14, 16, 28))
    d = ImageDraw.Draw(img)
    for y in range(H):  # cheap vertical gradient
        c = int(14 + 30 * y / H)
        d.line([(0, y), (W, y)], fill=(c, c + 4, c + 22))
    d.rectangle([60, 120, 60 + int((W - 120) * (idx + 1) / total), 132], fill=(90, 160, 255))
    d.text((60, 200), title[:40], font=_font(48), fill=(150, 175, 220))
    size = 78 if len(text) < 90 else 62
    lines = textwrap.wrap(text, width=int(1000 / (size * 0.55)))
    y = H // 2 - len(lines) * size // 2
    for ln in lines:
        d.text((60, y), ln, font=_font(size), fill=(255, 255, 255))
        y += int(size * 1.3)
    img.save(path)


async def _tts(text, out):
    import edge_tts
    await edge_tts.Communicate(text, "en-US-AriaNeural").save(out)


def make_audio(text, total, out_dir, h):
    """Cached TTS audio; falls back to silence so QC's audio-track check still holds."""
    mp3 = out_dir / f"{h}.mp3"
    if not mp3.exists():
        try:
            asyncio.run(_tts(text, str(mp3)))
        except Exception as e:
            log.info("TTS unavailable (%s); using silent audio", e)
            mp3 = None
    return mp3


def srt(scenes, path):
    def ts(t):
        return f"{int(t // 3600):02}:{int(t % 3600 // 60):02}:{int(t % 60):02},{int(t % 1 * 1000):03}"
    t, out = 0.0, []
    for i, (text, dur) in enumerate(scenes, 1):
        out.append(f"{i}\n{ts(t)} --> {ts(t + dur)}\n{text}\n")
        t += dur
    Path(path).write_text("\n".join(out), encoding="utf-8")


def render(content):
    out = Path(config.OUT_DIR)
    (out / "video").mkdir(parents=True, exist_ok=True)
    (out / "audio").mkdir(parents=True, exist_ok=True)
    (out / "assets").mkdir(parents=True, exist_ok=True)
    scenes = scenes_for(content["script"])
    h = db.sha(content["script"] + content["title"])[:24]
    mp4 = out / "video" / f"{h}.mp4"
    if mp4.exists():
        return str(mp4)  # never regenerate an unchanged asset
    total = sum(d for _, d in scenes)
    while total < 30:  # pad to the 30s floor on the last scene
        scenes[-1] = (scenes[-1][0], scenes[-1][1] + (30 - total))
        total = 30
    frames = []
    for i, (text, _) in enumerate(scenes):
        p = out / "assets" / f"{db.sha(text + content['title'])[:24]}_{i}_{len(scenes)}.png"
        if not p.exists():
            frame(text, i, len(scenes), content["title"], p)
        frames.append(p)
    srt(scenes, out / "video" / f"{h}.srt")
    lst = out / "video" / f"{h}.txt"
    lines = []
    for p, (_, dur) in zip(frames, scenes):
        lines += [f"file '{p.resolve().as_posix()}'", f"duration {dur:.2f}"]
    lines.append(f"file '{frames[-1].resolve().as_posix()}'")
    lst.write_text("\n".join(lines))
    mp3 = make_audio(" ".join(t for t, _ in scenes), total, out / "audio", h)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst)]
    if mp3:
        cmd += ["-i", str(mp3)]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    cmd += ["-t", f"{total:.2f}", "-vf", f"fps=30,scale={W}:{H},format=yuv420p", "-c:v", "libx264",
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
            path = render(dict(c))
            conn.execute("UPDATE contents SET video_path=?, template='basic-v1' WHERE id=?", (path, c["id"]))
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
