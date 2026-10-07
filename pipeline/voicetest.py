"""VOICETEST: the same sentences in the cloned voice with different settings, one short MP4 each,
so the owner can listen and pick the cleanest. Never publishes, never changes pipeline state."""
import logging
import os
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

from . import config, motion, render

log = logging.getLogger("voicetest")
TEXT = ("Websites that feel illegal to know. Part one: Photopea. "
        "Photopea Online Photo Editor lets you edit photos, apply effects, filters, add text. "
        "No need to install heavy software on your device. Just open your browser and start editing.")
VARIANTS = [  # name, expressiveness (embedding_scale), beta (0 = copy the style clip), speed
    ("A_current", 1.5, 0.0, 1.1),
    ("B_calmer", 1.0, 0.0, 1.1),
    ("C_blended", 1.0, 0.3, 1.1),
    ("D_blended_natural_speed", 1.0, 0.3, 1.0),
]


def label_image(text, path):
    img = motion.background().copy()
    f = motion.font(64)
    ImageDraw.Draw(img).text(((motion.W - f.getlength(text)) / 2, motion.H // 2 - 40), text, font=f,
                             fill=(255, 214, 102))
    img.save(path)


def run(conn=None):
    if not render.clone_enabled():
        return "voice clone not available (needs VOICE_REF_REPO + VOICE_REPO_PAT + the voiceclone package)"
    from voiceclone import synthesize
    out = Path(config.OUT_DIR) / "video"
    out.mkdir(parents=True, exist_ok=True)
    style = os.environ.get("VOICE_STYLE_REF", "").strip() or None
    made = []
    for name, scale, beta, speed in VARIANTS:
        raw, wav = out / f"{name}.raw.wav", out / f"{name}.wav"
        try:
            kw = {"style_ref_file": style, "styletts2_beta": beta, "styletts2_embedding_scale": scale} if style else {}
            synthesize(TEXT, str(raw), engine="styletts2", voice_ref_repo=os.environ["VOICE_REF_REPO"],
                       voice_ref_cache=str(out / ".voice_reference.mp3"), **kw)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-filter:a", f"atempo={speed}",
                            str(wav)], check=True, timeout=120)
            png = out / f"{name}.png"
            label_image(f"{name[0]}: expr {scale}  beta {beta}  speed {speed}", png)
            mp4 = out / f"voice_{name}.mp4"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(png), "-i", str(wav),
                            "-shortest", "-vf", "format=yuv420p", "-c:v", "libx264", "-tune", "stillimage",
                            "-c:a", "aac", "-b:a", "160k", str(mp4)], check=True, timeout=300)
            made.append(mp4.name)
        except Exception as e:
            log.warning("variant %s failed: %s", name, e)
    return {"samples": made}
