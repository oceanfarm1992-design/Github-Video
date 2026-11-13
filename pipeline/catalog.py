"""Growing tool catalogs: every AI-tools video shows tools that series never showed before.

The hand-written catalogs (tools.CATALOG, channel_series.KIDS / EVERYDAY) are the seed. When a series runs low
on unused tools, Claude suggests new tools (names + official homepages) for its existing topics and one new
topic; each suggestion is checked live before it is stored (the site answers, has its own description, does not
announce a shutdown, and the name matches the site). What a video says about a tool still comes only from the
tool's own homepage at build time. Stored in the SQLite state (table catalog_tools).
"""
import json
import logging
import re
import time
import urllib.parse

from . import config, db

log = logging.getLogger("catalog")
GROW_EVERY_DAYS = 6       # at most one growth call per series per week
MIN_READY = 3             # topics that can still fill a video with unused tools
MODEL = "claude-opus-5-5"
PRICE = (4.0, 20.0)       # USD per 1M tokens (input, output)

AUDIENCE = {
    "tools": "people curious about AI (creators, students, professionals); established AI products and websites",
    "kids": ("parents and teachers looking for safe, reputable learning and creativity tools that children use; "
             "educational products from established companies or organisations only; no chat or social apps"),
    "everyday": "everyday people who are not into tech; simple, established apps and websites for daily life",
}

SCHEMA = {
    "type": "object",
    "properties": {"topics": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "key": {"type": "string"}, "phrase": {"type": "string"}, "hashtag": {"type": "string"},
            "tools": {"type": "array", "items": {
                "type": "object", "properties": {"name": {"type": "string"}, "url": {"type": "string"}},
                "required": ["name", "url"], "additionalProperties": False}}},
        "required": ["key", "phrase", "hashtag", "tools"], "additionalProperties": False}}},
    "required": ["topics"], "additionalProperties": False,
}


def _ensure(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS catalog_tools (series TEXT, category TEXT, phrase TEXT, hashtag TEXT,
                    name TEXT, url TEXT, added_at REAL, PRIMARY KEY (series, url))""")


def base(series):
    from . import channel_series, tools
    return tools.CATALOG if series == "tools" else channel_series.CATALOGS[series]


def norm(url):
    p = urllib.parse.urlparse(url.strip().lower())
    return (p.netloc.removeprefix("www.") + p.path.rstrip("/")) if p.netloc else url.strip().lower()


def merged(conn, series):
    """The seed catalog plus every verified tool added since: {category: (phrase, hashtag, [(name, url)])}."""
    _ensure(conn)
    out = {k: (p, h, list(e)) for k, (p, h, e) in base(series).items()}
    known = {norm(u) for _, _, e in out.values() for _, u in e}
    for r in conn.execute("SELECT category, phrase, hashtag, name, url FROM catalog_tools WHERE series=? "
                          "ORDER BY added_at", (series,)):
        if norm(r["url"]) in known:
            continue
        known.add(norm(r["url"]))
        if r["category"] not in out:
            out[r["category"]] = (r["phrase"], r["hashtag"], [])
        out[r["category"]][2].append((r["name"], r["url"]))
    return out


def used_urls(conn, series):
    """Tools this Short series already showed (or is about to): never shown again."""
    out = set()
    for r in conn.execute("SELECT raw FROM topics WHERE source LIKE ? AND status NOT IN ('FAILED','SKIPPED')",
                          (series + ":%",)):
        out |= {norm(t["url"]) for t in json.loads(r["raw"] or "{}").get("tools", []) if t.get("url")}
    return out


def unused(entries, used):
    return [(n, u) for n, u in entries if norm(u) not in used]


def ready_topics(cat, used, need):
    return [k for k, (_, _, e) in cat.items() if len(unused(e, used)) >= need]


# ------------------------------------------------------------------ verification
TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def identity_ok(name, url, title, desc):
    """The site is really this tool: a word of the name is in the domain, page title or description."""
    words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) >= 3] or [name.lower()]
    hay = " ".join([urllib.parse.urlparse(url).netloc.lower(), (title or "").lower(), (desc or "").lower()])
    return any(w in hay for w in words)


def verify(name, url):
    """True when the suggestion is a live, real homepage of this tool."""
    from .http import page_description, request, url_ok
    from .tools import DEAD_RE
    if not url.startswith("https://") or not url_ok(url):
        return False
    desc = page_description(url)
    if not desc or DEAD_RE.search(desc):
        return False
    try:
        _, _, body = request(url, headers={"Accept": "text/html"}, attempts=1)
        m = TAG_RE.search(body[:200_000].decode("utf-8", "replace"))
        title = m.group(1) if m else ""
    except RuntimeError:
        title = ""
    return identity_ok(name, url, title, desc)


# ------------------------------------------------------------------ growth
def _ask(series, cat, used, need):
    import anthropic
    lows = sorted(cat, key=lambda k: len(unused(cat[k][2], used)))[:3]
    names = sorted({n for _, _, e in cat.values() for n, _ in e})
    prompt = (
        f"Audience: {AUDIENCE[series]}.\n"
        f"Existing topics (key: phrase): " + "; ".join(f"{k}: {cat[k][0]}" for k in cat) + "\n"
        f"Tools already listed (do not suggest these): {', '.join(names)}\n\n"
        f"Suggest real, currently available AI tools or websites with their official homepage (https, the "
        f"product's own domain, no app-store, review or affiliate links):\n"
        f"- for each of these existing topics, {need + 3} new tools that fit it: {', '.join(lows)} (keep each "
        f"topic's key, phrase and hashtag exactly);\n"
        f"- one new topic (short kebab-case key, a phrase that completes '10 AI tools for ...', a hashtag) with "
        f"{need + 4} tools.\nOnly tools you are confident exist today.")
    from . import gemini
    if gemini.available():  # free tier; every suggestion is checked live before it is used
        out = gemini.generate_json(prompt + '\n\nAnswer with JSON only: {"topics": [{"key": str, "phrase": str, '
                                   '"hashtag": str, "tools": [{"name": str, "url": str}]}]}')
        return (out or {}).get("topics", []) if isinstance(out, dict) else [], 0.0
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    # fallbacks="default": a safety decline is re-run server-side on Anthropic's recommended fallback model
    msg = client.beta.messages.create(
        model=MODEL, max_tokens=16000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}})
    cost = (msg.usage.input_tokens * PRICE[0] + msg.usage.output_tokens * PRICE[1]) / 1e6
    if msg.stop_reason in ("refusal", "max_tokens"):
        log.warning("catalog suggestion for %s stopped: %s", series, msg.stop_reason)
        return [], cost
    text = next((b.text for b in msg.content if b.type == "text"), "{}")
    return json.loads(text).get("topics", []), cost


def grow(conn, series, need):
    """Adds verified tools when fewer than MIN_READY topics can fill a video. Returns tools added."""
    _ensure(conn)
    cat, used = merged(conn, series), used_urls(conn, series)
    if len(ready_topics(cat, used, need)) >= MIN_READY:
        return 0
    import os
    from . import gemini
    paid_ok = os.environ.get("CATALOG_GROWTH", "0") == "1" and config.ANTHROPIC_API_KEY
    if not (gemini.available() or paid_ok):  # Gemini free tier, or Claude when turned on (paid)
        log.info("%s: fewer than %d topics with unused tools; add tools or set CATALOG_GROWTH=1", series, MIN_READY)
        return 0
    last = float(db.kv_get(conn, f"catalog_grown:{series}") or 0)  # last answered request: a failed one retries next run
    if time.time() - last < GROW_EVERY_DAYS * 86400:
        return 0
    try:
        topics, cost = _ask(series, cat, used, need)
    except Exception as e:
        log.warning("catalog suggestion for %s failed: %s", series, e)
        return 0
    db.kv_set(conn, f"catalog_grown:{series}", str(time.time()))
    conn.execute("INSERT INTO spend VALUES (?,1,?) ON CONFLICT(day) DO UPDATE SET calls=calls+1, usd=usd+?",
                 (db.today(), cost, cost))
    known = {norm(u) for _, _, e in cat.values() for _, u in e}
    added = 0
    for t in topics:
        key = re.sub(r"[^a-z0-9-]", "", t["key"].lower())[:40]
        if not key:
            continue
        phrase, tag = (cat[key][0], cat[key][1]) if key in cat else (t["phrase"].strip()[:60], t["hashtag"][:30])
        for tool in t["tools"]:
            name, url = tool["name"].strip()[:60], tool["url"].strip()
            if not name or norm(url) in known:
                continue
            try:
                ok = verify(name, url)
            except Exception:
                ok = False
            if not ok:
                log.info("suggested %s (%s) not verified; skipped", name, url)
                continue
            known.add(norm(url))
            conn.execute("INSERT OR IGNORE INTO catalog_tools VALUES (?,?,?,?,?,?,?)",
                         (series, key, phrase, tag, name, url, time.time()))
            added += 1
    log.info("catalog %s: %d verified tools added ($%.3f)", series, added, cost)
    return added


def run(conn):
    """Keeps every AI-tools series stocked with tools it never showed."""
    from . import channels
    out = {"tools": grow(conn, "tools", config.TOOLS_PER_VIDEO)}
    for key in channels.enabled():
        out[key] = grow(conn, key, config.CHANNEL_TOOLS_PER_VIDEO)
    return out
