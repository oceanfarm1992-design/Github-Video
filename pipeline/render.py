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

from . import browser, config, db, motion, music
from .http import request

log = logging.getLogger("render")
W, H = 1080, 1920
WPS = 2.6  # spoken words/sec, only used when TTS is unavailable
PAD = 0.4  # seconds of breathing room after each narrated scene
MAX_SECONDS = 58.0  # Facebook Reels limit is 60 s
MAX_SECONDS_TOOLS = 88.0  # tools lists: YouTube Shorts + Instagram Reels (90 s); Facebook is skipped > 60 s


DISCLAIMER = os.environ.get("DISCLAIMER_TEXT", "For educational purposes only")


MUSIC_VOLUME = os.environ.get("MUSIC_VOLUME", "0.22")  # music level before ducking (1.0 = full)


def mix_music(voice, track, total, dest):
    """Voice + music: music fades in/out and is pushed down (sidechain) whenever the voice speaks."""
    fade_out = max(0.0, total - 1.5)
    mus = (f"aresample=44100,volume={MUSIC_VOLUME},afade=t=in:d=1.0,"
           f"afade=t=out:st={fade_out:.3f}:d=1.5")
    if voice:
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(voice), "-i", str(track), "-filter_complex",
               f"[1:a]{mus}[m];[0:a]aresample=44100,asplit=2[v][sc];"
               "[m][sc]sidechaincompress=threshold=0.02:ratio=6:attack=20:release=400[md];"
               "[v][md]amix=inputs=2:duration=first:normalize=0[aout]",
               "-map", "[aout]", "-t", f"{total:.3f}", "-c:a", "aac", "-b:a", "160k", str(dest)]
    else:
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(track), "-af", mus,
               "-t", f"{total:.3f}", "-c:a", "aac", "-b:a", "160k", str(dest)]
    subprocess.run(cmd, check=True, timeout=300)
    return dest


def promo_of(topic):
    """Raw data of a Privacy PDF Tools promo topic (tool + tour), else None."""
    try:
        if topic and str(topic["source"]).startswith("promo:"):
            raw = json.loads(topic["raw"] or "{}")
            return raw if raw.get("tour") and raw.get("tool") else None
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    return None


def sections_of(topic):
    """Sections of a one-website video (heading, text, box), else None."""
    try:
        if topic and str(topic["source"]).startswith("sites:"):
            return json.loads(topic["raw"] or "{}").get("sections") or None
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    return None


def tools_of(topic):
    """The verified tools list of an AI-tools topic, else None."""
    try:
        if topic and str(topic["source"]).startswith(("tools:", "sites:")):
            return json.loads(topic["raw"] or "{}").get("tools") or None
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    return None
VOICE = os.environ.get("TTS_VOICE", "en-US-AndrewMultilingualNeural")
try:  # narration speed multiplier (1.0 = natural pace)
    SPEED = min(1.4, max(0.8, float(os.environ.get("VOICE_SPEED", "1.1"))))
except ValueError:
    SPEED = 1.2
RATE = os.environ.get("TTS_RATE", f"{round((SPEED - 1) * 100):+d}%")


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


def clone_enabled():
    """Own-voice cloning (StyleTTS2 via the styletts2-voice-clone package) when configured + installed."""
    if not (os.environ.get("VOICE_REF_REPO") and os.environ.get("VOICE_REPO_PAT")):
        return False
    try:
        import voiceclone  # noqa: F401
        return True
    except ImportError:
        return False


def _env_float(name, default, lo, hi):
    try:
        return min(hi, max(lo, float(os.environ.get(name, default))))
    except ValueError:
        return default


def clone_style():
    """Speaking-style clip in the voice reference repo, how closely to follow it (beta: 0 = copy
    exactly) and expressiveness (StyleTTS2 embedding_scale)."""
    ref = os.environ.get("VOICE_STYLE_REF", "").strip() or None
    return (ref, _env_float("VOICE_STYLE_BETA", 0.3, 0.0, 1.0),
            _env_float("VOICE_EXPRESSIVENESS", 1.0, 0.5, 3.0))


def style_key():
    style_ref, beta, scale = clone_style()
    return f"{style_ref}-{beta}-{scale}" if style_ref else ""


def voice_id():
    if not clone_enabled():
        return VOICE + RATE
    return f"clone-styletts2-{SPEED}" + (f"-{style_key()}" if style_key() else "")


def speech_text(t):
    """StyleTTS2 treats ALL-CAPS as emphasis/spelling; say the CTA keywords naturally."""
    for k, v in (("GITHUB", "GitHub"), ("TOOL", "tool"), ("CODE", "code"), ("DOCS", "docs"),
                 ("DEMO", "demo"), ("SOURCE", "source")):
        t = t.replace(k, v)
    return t


def narrate_clone(texts, out_dir):
    from voiceclone import synthesize
    style_ref, beta, scale = clone_style()
    style = style_key()
    clips = []
    for t in texts:
        p = out_dir / f"{db.sha(f'clone{SPEED}{style}' + t)[:24]}.wav"
        if not p.exists():
            raw = out_dir / f"{db.sha(f'clone{style}' + t)[:24]}.raw.wav"
            if not raw.exists():
                kw = {"style_ref_file": style_ref, "styletts2_beta": beta,
                      "styletts2_embedding_scale": scale} if style_ref else {}
                used = synthesize(speech_text(t), str(raw), engine="styletts2",
                                  voice_ref_repo=os.environ["VOICE_REF_REPO"],
                                  voice_ref_cache=str(out_dir / ".voice_reference.mp3"), **kw)
                log.info("voice engine: %s", used)
            # pitch-preserving speed-up (atempo)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-filter:a",
                            f"atempo={SPEED}", str(p)], check=True, timeout=120)
        clips.append((p, _duration(p)))
    return clips


def narrate(texts, out_dir):
    """One cached clip per scene. Own-voice clone first, then Edge neural TTS, else None (silent)."""
    if clone_enabled():
        try:
            return narrate_clone(texts, out_dir)
        except Exception as e:  # never mix two voices in one video: redo every scene with Edge
            log.warning("voice clone failed (%s); falling back to Edge TTS", e)
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
    h = db.sha(content["script"] + content["title"] + voice_id())[:24]
    mp4 = out / "video" / f"{h}.mp4"
    if mp4.exists():
        return str(mp4)  # never regenerate an unchanged asset

    tools = tools_of(topic)
    # 10-tool lists may run to 88 s (YouTube/Instagram); website parts stay under Facebook's 60 s
    max_seconds = MAX_SECONDS_TOOLS if tools and str(topic["source"]).startswith("tools:") else MAX_SECONDS
    scene_pages = scene_labels = scene_focus = None
    card = page = page_url = None
    sections = sections_of(topic)
    is_sites = bool(topic) and str(topic["source"]).startswith("sites:")
    promo_raw = promo_of(topic)
    if promo_raw:
        # owner's own tool page, guided tour: each beat spotlights its section of the page
        tool, beats = promo_raw["tool"], promo_raw["tour"]["beats"]
        shot = browser.capture(tool["url"], out / "assets", any_site=True, max_css_height=2600)
        if shot and len(texts) == len(beats) + 2:  # hook, beats, outro
            scene_pages = [(shot, tool["url"])] * len(texts)
            scene_focus = [None] + [b.get("box") for b in beats] + [None]
            scene_labels = [f"{promo_raw.get('brand', '')}  ·  {tool['name']}"] * len(texts)
    elif tools and sections and len(tools) == 1:
        # one website, guided tour: every scene on its page; the camera glides and zooms to each section
        site = tools[0]
        shot = browser.capture(site["url"], out / "assets", any_site=True, max_css_height=2600)
        if shot and len(texts) == len(sections) + 3:  # hook, description, one per section, outro
            scene_pages = [(shot, site["url"])] * len(texts)
            scene_focus = [None, None] + [s["box"] for s in sections] + [None]
            part = json.loads(topic["raw"] or "{}").get("part", 1)
            scene_labels = [f"Part {part}  ·  {site['name']}"] * len(texts)
    elif tools:  # one scene per tool: its homepage live in a browser window, labelled "#i/N  Name"
        n = len(tools)
        scene_pages = [None] + [(browser.capture(t["url"], out / "assets", any_site=True, max_css_height=2600),
                                 t["url"]) for t in tools] + [None]
        scene_pages = [p if p and p[0] else None for p in scene_pages]
        scene_labels = [None] + [f"#{i}/{n}  {t['name']}" for i, t in enumerate(tools, 1)] + [None]
        if len(scene_pages) != len(texts):  # script and tool list disagree: show text-only scenes
            scene_pages = scene_labels = None
    else:
        card = fetch_image(image_url(topic), out / "assets") if topic else None
        if topic and os.environ.get("PAGE_SCROLL", "1") != "0":
            page_url = next((u for u in (topic["github_url"], topic["url"]) if browser.supported(u)), None)
        page = browser.capture(page_url, out / "assets") if page_url else None
    clips = narrate(texts, out / "audio")
    if clips:
        scenes = [(t, dur + PAD) for t, (_, dur) in zip(texts, clips)]
    else:
        scenes = [(t, max(3.0, len(t.split()) / WPS + 0.6)) for t in texts]
    # stay under the platform limit: drop the last middle beat(s) instead of failing the whole video
    while sum(d for _, d in scenes) > max_seconds and len(scenes) > 3:
        del scenes[-2]
        if clips:
            del clips[-2]
        for lst in (scene_pages, scene_labels, scene_focus):
            if lst:
                del lst[-2]
    total = sum(d for _, d in scenes)
    if total < 30:  # pad the last scene (silence) to the 30s floor
        scenes[-1] = (scenes[-1][0], scenes[-1][1] + 30 - total)
        total = 30.0
    if total > max_seconds:
        raise RuntimeError(f"script too long for a Short: {total:.0f}s")

    srt(scenes, out / "video" / f"{h}.srt")

    # 1) narration track, each clip padded to its scene length so audio and slides stay in lockstep
    audio = None
    if clips:
        audio = out / "audio" / f"{h}.m4a"
        cmd = ["ffmpeg", "-y", "-loglevel", "error"]
        for p, _ in clips:
            cmd += ["-i", str(p)]
        # 15 ms fade in/out per clip: no clicks or pops where one sentence ends and the next starts
        parts = "".join(
            f"[{i}:a]aresample=44100,afade=t=in:d=0.015,afade=t=out:st={max(0.0, clips[i][1] - 0.02):.3f}:d=0.02,"
            f"apad=whole_dur={scenes[i][1]:.3f}[a{i}];" for i in range(len(clips)))
        fc = parts + "".join(f"[a{i}]" for i in range(len(clips))) + f"concat=n={len(clips)}:v=0:a=1,apad[aout]"
        cmd += ["-filter_complex", fc, "-map", "[aout]", "-t", f"{total:.3f}", "-c:a", "aac", "-b:a", "128k", str(audio)]
        subprocess.run(cmd, check=True, timeout=300)

    # 1b) synthetic background music (hacker / horror), ducked under the voice
    style = music.style_for(topic["source"] if topic else "")
    if style != "off":
        try:
            audio = mix_music(audio, music.track(style, total, out / "audio"), total, out / "audio" / f"{h}.mix.m4a")
        except Exception as e:  # music is a nice-to-have; never lose the video over it
            log.warning("background music skipped: %s", e)

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
        for raw in motion.frames(scenes, card, audio, os.environ.get("WATERMARK_TEXT"), page, page_url,
                                 scene_pages=scene_pages, scene_labels=scene_labels, scene_focus=scene_focus,
                                 disclaimer=DISCLAIMER if is_sites else None):
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
