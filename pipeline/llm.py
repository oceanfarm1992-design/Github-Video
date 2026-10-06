"""Small cached, budget-capped JSON call to the cheap model (shared by stages that need one)."""
import json
import re
import time

from . import config, db
from .generate import PRICE, _budget
from .http import request


def call_json(conn, prompt, max_tokens=900):
    """Returns parsed JSON (list or dict) or None when disabled, over budget, or unparsable.
    Results are cached by prompt hash; spend is recorded against the daily budget."""
    if not config.ANTHROPIC_API_KEY:
        return None
    key = db.sha(config.LLM_MODEL + prompt)
    cached = conn.execute("SELECT response FROM llm_cache WHERE key=?", (key,)).fetchone()
    if cached:
        return json.loads(cached["response"])
    pin, pout = PRICE.get(config.LLM_MODEL, (3.0, 15.0))
    est = (len(prompt) / 4 * pin + max_tokens * pout) / 1e6
    calls, usd = _budget()
    if calls >= config.MAX_LLM_CALLS_PER_DAY or usd + est > config.DAILY_AI_BUDGET_USD:
        return None
    body = json.dumps({"model": config.LLM_MODEL, "max_tokens": max_tokens,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    _, _, resp = request("https://api.anthropic.com/v1/messages", data=body, method="POST", headers={
        "x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
        "content-type": "application/json"})
    r = json.loads(resp)
    u = r.get("usage", {})
    cost = (u.get("input_tokens", 0) * pin + u.get("output_tokens", 0) * pout) / 1e6
    conn.execute("INSERT INTO spend VALUES (?,1,?) ON CONFLICT(day) DO UPDATE SET calls=calls+1, usd=usd+?",
                 (db.today(), cost, cost))
    m = re.search(r"[\[{].*[\]}]", r["content"][0]["text"], re.S)
    if not m:
        return None
    out = json.loads(m.group(0))
    conn.execute("INSERT OR REPLACE INTO llm_cache VALUES (?,?,?,?)", (key, json.dumps(out), cost, time.time()))
    return out
