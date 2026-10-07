"""Synthetic background music, generated in code (no samples, no licences, no copyright claims).

Two moods:
  hacker - dark synth pulse: bass line, minor arpeggio with echo, ticking hi-hats, low pad (110 BPM)
  horror - drone with a dissonant minor second, slow swells, heartbeat thumps, eerie high tones
Rendered as a mono 16-bit WAV with the standard library only and cached by (style, length).
"""
import array
import math
import random
import wave
from pathlib import Path

SR = 22050
VERSION = 1  # bump to regenerate cached tracks after changing the sound


def _note(n):
    """MIDI note number -> Hz."""
    return 440.0 * 2 ** ((n - 69) / 12)


def _hacker(seconds, rng):
    n = int(seconds * SR)
    out = array.array("f", bytes(4 * n))
    bpm = 110
    sixteenth = 60 / bpm / 4
    bass_notes = [45, 45, 48, 43]          # A2 A2 C3 G2, one per beat-group of 4 sixteenths x 4
    arp = [57, 60, 64, 67, 69, 64, 60, 64]  # A3 C4 E4 G4 A4 E4 C4 E4 (A minor)
    step = int(sixteenth * SR)
    # bass + arpeggio + hats, note by note
    for i in range(int(seconds / sixteenth) + 1):
        start = i * step
        bar_pos = i % 16
        # bass on every 8th note, root changes each beat
        if i % 2 == 0:
            f = _note(bass_notes[(i // 16) % 4] - 12)
            length = min(n - start, int(step * 1.9))
            for k in range(max(0, length)):
                t = k / SR
                env = math.exp(-t * 7)
                ph = 2 * math.pi * f * t
                s = math.sin(ph) + 0.5 * math.sin(2 * ph) + 0.25 * math.sin(3 * ph)  # soft saw
                out[start + k] += 0.32 * env * s
        # arpeggio: square-ish pluck
        f = _note(arp[i % len(arp)] + (12 if (i // 32) % 2 else 0))
        length = min(n - start, int(step * 0.9))
        for k in range(max(0, length)):
            t = k / SR
            env = math.exp(-t * 18)
            out[start + k] += 0.10 * env * math.tanh(3 * math.sin(2 * math.pi * f * t))
        # hi-hat tick on the off-beat 8ths
        if bar_pos % 4 == 2:
            length = min(n - start, int(0.05 * SR))
            prev = 0.0
            for k in range(max(0, length)):
                w = rng.uniform(-1, 1)
                hp = w - prev  # crude high-pass
                prev = w
                out[start + k] += 0.05 * math.exp(-k / SR * 60) * hp
    # echo on everything (3/16 delay), then a quiet low pad underneath
    d = int(3 * sixteenth * SR)
    for k in range(d, n):
        out[k] += 0.35 * out[k - d]
    for k in range(n):
        t = k / SR
        swell = 0.5 + 0.5 * math.sin(2 * math.pi * t / 8.0)
        out[k] += 0.06 * swell * (math.sin(2 * math.pi * 110 * t) + math.sin(2 * math.pi * 110.6 * t)
                                  + 0.6 * math.sin(2 * math.pi * 164.8 * t))
    return out


def _horror(seconds, rng):
    n = int(seconds * SR)
    out = array.array("f", bytes(4 * n))
    for k in range(n):
        t = k / SR
        lfo = 0.6 + 0.4 * math.sin(2 * math.pi * 0.07 * t)
        drone = (math.sin(2 * math.pi * 55 * t) + 0.8 * math.sin(2 * math.pi * 58.27 * t)  # minor second beat
                 + 0.5 * math.sin(2 * math.pi * 41.2 * t))
        swell = max(0.0, math.sin(2 * math.pi * t / 9.0)) ** 3                               # every ~9 s
        cluster = math.sin(2 * math.pi * 466.2 * t) + math.sin(2 * math.pi * 493.9 * t)      # A#4 + B4
        out[k] = 0.22 * lfo * drone + 0.035 * swell * cluster
    # heartbeat: lub-dub every 1.2 s, pitch dropping thumps
    beat = 1.2
    for b in range(int(seconds / beat) + 1):
        for off, amp in ((0.0, 0.45), (0.22, 0.3)):
            start = int((b * beat + off) * SR)
            for k in range(min(n - start, int(0.25 * SR))):
                t = k / SR
                f = 60 * math.exp(-t * 6) + 35
                out[start + k] += amp * math.exp(-t * 14) * math.sin(2 * math.pi * f * t)
    # sparse eerie pings with a long tail
    t0 = 2.0
    while t0 < seconds - 1:
        start, f = int(t0 * SR), _note(rng.choice([84, 87, 90, 91]))  # high, unresolved notes
        for k in range(min(n - start, int(2.5 * SR))):
            t = k / SR
            out[start + k] += 0.05 * math.exp(-t * 1.6) * math.sin(2 * math.pi * f * t) * \
                (1 + 0.3 * math.sin(2 * math.pi * 5 * t))
        t0 += rng.uniform(3.5, 6.5)
    # simple reverb: two feedback delays
    for d, g in ((int(0.113 * SR), 0.3), (int(0.271 * SR), 0.25)):
        for k in range(d, n):
            out[k] += g * out[k - d]
    return out


def track(style, seconds, out_dir):
    """Path to a WAV of `style` ("hacker" | "horror") lasting `seconds` (cached)."""
    style = style if style in ("hacker", "horror") else "hacker"
    seconds = max(5.0, round(seconds + 0.5, 1))
    path = Path(out_dir) / f"music_{style}_{seconds}_v{VERSION}.wav"
    if path.exists():
        return path
    rng = random.Random(f"{style}-{seconds}")  # deterministic per length
    samples = _hacker(seconds, rng) if style == "hacker" else _horror(seconds, rng)
    peak = max(1e-6, max(abs(min(samples)), abs(max(samples))))
    scale = 0.9 * 32767 / peak
    pcm = array.array("h", (int(s * scale) for s in samples))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path


def style_for(source):
    """Mood for a video: the owner chose the hacker track for every series.
    MUSIC_STYLE overrides ("hacker", "horror", "off")."""
    import os
    forced = os.environ.get("MUSIC_STYLE", "hacker").strip().lower()
    return forced if forced in ("hacker", "horror", "off") else "hacker"
