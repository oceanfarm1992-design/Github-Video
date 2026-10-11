"""Sound design, generated in code (no samples, no licences): whoosh, impact and click, placed at exact
sample positions from the motion engine's events and written as one mono WAV track.

whoosh - band-passed noise whose centre sweeps up then down (transitions)
impact - low pitch-dropping thump with a short transient (circle-wipe landing, the opening hit)
click  - short bright tick (a spotlight arrives on a section)
"""
import math
import wave

import numpy as np

SR = 44100


def _lowpass(x, cutoff):
    """One-pole low-pass with a per-sample cutoff (Hz)."""
    a = np.exp(-2 * math.pi * np.asarray(cutoff, np.float64) / SR) * np.ones(len(x))
    y, prev = np.empty(len(x)), 0.0
    for i in range(len(x)):
        prev = (1 - a[i]) * x[i] + a[i] * prev
        y[i] = prev
    return y


def whoosh(seed=1):
    dur = 0.42
    n = int(dur * SR)
    t = np.arange(n) / SR
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(n)
    shape = np.sin(math.pi * np.clip(t / dur, 0, 1)) ** 2
    hi = _lowpass(noise, 400 + 3200 * shape)
    band = hi - _lowpass(hi, 180 + 500 * shape)  # crude band-pass that rises and falls
    env = np.where(t < dur * 0.6, (t / (dur * 0.6)) ** 2, np.exp(-(t - dur * 0.6) * 18))
    out = band * env
    return out / (np.abs(out).max() or 1)


def impact():
    dur = 0.75
    t = np.arange(int(dur * SR)) / SR
    freq = 38 + 70 * np.exp(-t * 6)
    body = np.sin(2 * math.pi * np.cumsum(freq) / SR) * np.exp(-t * 5.5)
    rng = np.random.default_rng(3)
    snap = rng.standard_normal(len(t)) * np.exp(-t * 350) * 0.5
    out = np.tanh(1.8 * (body + snap))
    return out / (np.abs(out).max() or 1)


def click():
    t = np.arange(int(0.05 * SR)) / SR
    rng = np.random.default_rng(5)
    out = 0.7 * np.sin(2 * math.pi * 1900 * t) * np.exp(-t * 130) + 0.3 * rng.standard_normal(len(t)) * np.exp(-t * 400)
    return out / (np.abs(out).max() or 1)


SOUNDS = {"whoosh": whoosh, "impact": impact, "click": click}
GAIN = {"whoosh": 0.55, "impact": 0.8, "click": 0.35}


def track(events, total, dest):
    """events: [(seconds, kind)] -> mono 16-bit WAV of `total` seconds at `dest`. Returns dest."""
    buf = np.zeros(int(total * SR) + SR)
    cache = {}
    for at, kind in events:
        if kind not in SOUNDS:
            continue
        snd = cache.setdefault(kind, SOUNDS[kind]())
        i = max(0, int(at * SR))
        end = min(len(buf), i + len(snd))
        if end > i:
            buf[i:end] += GAIN[kind] * snd[:end - i]
    buf = buf[:int(total * SR)]
    peak = np.abs(buf).max()
    if peak > 0.95:
        buf *= 0.95 / peak
    with wave.open(str(dest), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((buf * 32767).astype("<i2").tobytes())
    return dest
