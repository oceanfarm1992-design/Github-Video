"""Google Gemini API (free tier) for text that is built only from public material: long-video scripts, GitHub
Short scripts and new-tool suggestions. Never used for follower comments (personal data): Google may use
free-tier prompts to improve its products.

Key: GEMINI_API_KEY (or the GEMMINI_API secret). Model: GEMINI_MODEL, else the newest "flash" model the key
can use (found once per run from the models list).
"""
import json
import logging
import os
import re
import time

from .http import request

log = logging.getLogger("gemini")
API = "https://generativelanguage.googleapis.com/v1beta"
_model = None


def key():
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GEMMINI_API") or ""


def available():
    return bool(key())


def _version(name):
    nums = re.findall(r"\d+(?:\.\d+)?", name)
    return float(nums[0]) if nums else 0.0


def models():
    """GEMINI_MODEL, else the stable flash models, newest first (a busy one is skipped for the next)."""
    global _model
    if _model:
        return _model
    if os.environ.get("GEMINI_MODEL"):
        _model = [os.environ["GEMINI_MODEL"].strip()]
        return _model
    _, _, body = request(f"{API}/models?pageSize=200", headers={"x-goog-api-key": key()}, attempts=2)
    names = [m["name"].split("/", 1)[1] for m in json.loads(body).get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    flash = [n for n in names if "flash" in n and not re.search(r"lite|preview|exp|image|audio|tts|live|thinking", n)]
    _model = sorted(set(flash), key=_version, reverse=True)[:3] or ["gemini-2.5-flash"]
    log.info("gemini models: %s", _model)
    return _model


def generate_json(prompt, system=None, max_tokens=16000):
    """Parsed JSON answer, or None (no key, blocked, or not JSON). One retry after a rate limit."""
    if not available():
        return None
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": max_tokens,
                                 "temperature": 0.4}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    resp = None
    for name in models():  # newest first; a busy or rate-limited model gets one more try, then the next one
        for attempt in range(2):
            try:
                _, _, resp = request(f"{API}/models/{name}:generateContent", method="POST",
                                     data=json.dumps(body).encode(), attempts=1, timeout=300,
                                     headers={"x-goog-api-key": key(), "Content-Type": "application/json"})
                break
            except RuntimeError as e:
                log.warning("gemini %s failed: %s", name, str(e)[:200])
                if attempt == 0 and re.search(r"\b(429|500|503)\b", str(e)):
                    time.sleep(45)
                    continue
                break
        if resp:
            break
    if not resp:
        return None
    r = json.loads(resp)
    cand = (r.get("candidates") or [{}])[0]
    if cand.get("finishReason") not in (None, "STOP"):
        log.warning("gemini stopped: %s", cand.get("finishReason"))
        return None
    text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []))
    m = re.search(r"[\[{].*[\]}]", text, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except ValueError:
        return None
