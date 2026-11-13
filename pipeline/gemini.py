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


def model():
    """GEMINI_MODEL, else the newest stable flash model (not lite, preview, image or audio)."""
    global _model
    if _model:
        return _model
    if os.environ.get("GEMINI_MODEL"):
        _model = os.environ["GEMINI_MODEL"].strip()
        return _model
    _, _, body = request(f"{API}/models?pageSize=200", headers={"x-goog-api-key": key()}, attempts=2)
    names = [m["name"].split("/", 1)[1] for m in json.loads(body).get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    flash = [n for n in names if "flash" in n and not re.search(r"lite|preview|exp|image|audio|tts|live|thinking", n)]
    _model = max(flash, key=_version) if flash else "gemini-2.5-flash"
    log.info("gemini model: %s", _model)
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
    for attempt in range(2):
        try:
            _, _, resp = request(f"{API}/models/{model()}:generateContent", method="POST", data=json.dumps(body).encode(),
                                 headers={"x-goog-api-key": key(), "Content-Type": "application/json"},
                                 attempts=1, timeout=300)
            break
        except RuntimeError as e:
            if "429" in str(e) and attempt == 0:  # free-tier rate limit: wait and try once more
                time.sleep(60)
                continue
            log.warning("gemini request failed: %s", str(e)[:300])
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
