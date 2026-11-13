"""Groq API (free tier, OpenAI-compatible): the second free model after Gemini, for text built only from public
material (never follower comments). Key: GROQ_API (or GROQ_API_KEY). Model: GROQ_MODEL, else the best
available from PREFERRED (found once per run from the models list)."""
import json
import logging
import os
import re
import time

from .http import request

log = logging.getLogger("groq")
API = "https://api.groq.com/openai/v1"
PREFERRED = ("openai/gpt-oss-120b", "llama-3.3-70b-versatile", "moonshotai/kimi-k2-instruct", "qwen/qwen3-32b",
             "openai/gpt-oss-20b", "llama-3.1-8b-instant")
_models = None


def key():
    return os.environ.get("GROQ_API_KEY") or os.environ.get("GROQ_API") or ""


def available():
    return bool(key())


def models():
    """GROQ_MODEL, else up to two preferred chat models this key can use."""
    global _models
    if _models is None:
        if os.environ.get("GROQ_MODEL"):
            _models = [os.environ["GROQ_MODEL"].strip()]
        else:
            _, _, body = request(f"{API}/models", headers={"Authorization": f"Bearer {key()}"}, attempts=2)
            have = {m["id"] for m in json.loads(body).get("data", []) if m.get("active", True)}
            _models = [m for m in PREFERRED if m in have][:2] or sorted(have)[:1]
        log.info("groq models: %s", _models)
    return _models


def generate_json(prompt, system=None, max_tokens=8000):
    """Parsed JSON answer, or None. A rate-limited or busy model gets one more try, then the next one."""
    if not available():
        return None
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    for name in models():
        body = {"model": name, "messages": msgs, "temperature": 0.4, "max_tokens": max_tokens,
                "response_format": {"type": "json_object"}}
        for attempt in range(2):
            try:
                _, _, resp = request(f"{API}/chat/completions", method="POST", data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {key()}", "Content-Type": "application/json"},
                                     attempts=1, timeout=300)
            except RuntimeError as e:
                log.warning("groq %s failed: %s", name, str(e)[:200])
                if attempt == 0 and re.search(r"\b(429|500|503)\b", str(e)):
                    time.sleep(30)
                    continue
                break
            choice = (json.loads(resp).get("choices") or [{}])[0]
            text = (choice.get("message") or {}).get("content") or ""
            m = re.search(r"[\[{].*[\]}]", text, re.S)
            try:
                return json.loads(m.group(0)) if m else None
            except ValueError:
                return None
    return None
