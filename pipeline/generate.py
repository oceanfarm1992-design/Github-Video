"""GENERATE: deterministic template first; optional cheap-LLM rewrite under a hard budget."""
import calendar
import json
import logging
import os
import re
import time

from . import config, db
from .http import request

log = logging.getLogger("generate")

# USD per 1M tokens (input, output) for the default small model.
PRICE = {"claude-haiku-4-5-20251001": (1.0, 5.0)}
HASHTAGS = ["#AI", "#OpenSource", "#GitHub", "#MachineLearning"]


def pick_cta(row):
    if row["github_url"] or row["source"] == "github":
        return "GITHUB", row["github_url"] or row["url"]
    if row["source"] == "huggingface":
        return "DEMO", row["url"]
    if row["source"] == "arxiv":
        return "DOCS", row["url"]
    return "SOURCE", row["url"]


def numbers(text):
    """Numbers as written (1.7k, 501B, 40%, 2,000 -> 2000), for the no-invented-statistics check."""
    found = re.findall(r"\d+(?:[.,]\d+)*\s?(?:[kmb](?![a-z])|%)?", (text or "").lower())
    return {re.sub(r"[,\s]", "", n).rstrip(".") for n in found}


def clean_claim(text, limit=140):
    """Make a claim safe to show and speak: drop CJK/emoji/symbols the font and voice can't handle."""
    text = re.sub(r"^Description:\s*", "", text)
    kept = "".join(ch for ch in text if ch.isascii() or ch in "—–’‘“”…")
    kept = re.sub(r"\s+", " ", kept).strip(" -|:,.")
    if len(kept) < 0.6 * len(text.strip()) or len(kept) < 12:
        return None  # mostly non-Latin text: not usable
    if len(kept) > limit:
        kept = kept[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "..."
    return kept if kept[-1] in ".!?" else kept + "."


TOPIC_PHRASES = {
    "llm": "building with large language models", "ai-agents": "AI agents", "agents": "AI agents",
    "machine-learning": "machine learning", "rag": "RAG apps", "mcp": "connecting AI to your tools",
    "deep-learning": "deep learning", "chatbot": "chatbots", "diffusion": "AI image generation",
}


def template_script(row, claims):
    texts = [t for t in (clean_claim(c["text"]) for c in claims) if t]
    title = row["title"]
    short = title.split("/")[-1] if row["source"] == "github" else title
    raw = json.loads(row["raw"] or "{}")
    if row["source"] == "github":
        phrase = next((TOPIC_PHRASES[t] for t in raw.get("topics", []) if t in TOPIC_PHRASES), None)
        if raw.get("license") and phrase:  # "free, open-source" only when the license is a sourced fact
            hook = f"Want a free, open-source tool for {phrase}?"
        elif raw.get("license"):
            hook = "Want a free, open-source AI project to try today?"
        else:
            hook = "Looking for a new AI project on GitHub?"
        intro = f"Meet {short}."
    else:
        hook = "Did you catch this AI update?"
        intro = f"{short[:90]}."
    return hook, [intro] + texts[:3]


def _budget():
    with db.db() as conn:
        r = conn.execute("SELECT calls,usd FROM spend WHERE day=?", (db.today(),)).fetchone()
    return (r["calls"], r["usd"]) if r else (0, 0.0)


def llm_rewrite(conn, row, claims):
    """Returns (hook, beats) or None. Skips (never exceeds) when budget/cap/key is missing."""
    if not config.ANTHROPIC_API_KEY:
        return None
    facts = [t for t in (clean_claim(c["text"]) for c in claims) if t]
    prompt = (
        "Write a 35-second vertical-video script as JSON {\"hook\": str, \"beats\": [str, str, str]}. "
        "The hook MUST be a question addressed to the viewer (start with 'Do you want', 'Want' or 'Looking for'), "
        "under 14 words, about the need this project meets, e.g. 'Do you want to build AI videos on your own PC?'. "
        "Only mention free, local or open-source if the facts say so. The first beat must introduce the project by "
        "name ('Meet NAME, ...'). Use ONLY the facts below: never describe purpose, features, quality or "
        "capabilities that are not stated in them, and never repeat the same fact. If the facts are thin, "
        "return fewer beats (minimum 2). Do not add statistics or links. "
        "Each beat under 22 words.\nTitle: " + row["title"] + "\nFacts:\n- " + "\n- ".join(facts))
    key = db.sha(config.LLM_MODEL + prompt)
    cached = conn.execute("SELECT response FROM llm_cache WHERE key=?", (key,)).fetchone()
    if cached:
        return json.loads(cached["response"])
    pin, pout = PRICE.get(config.LLM_MODEL, (3.0, 15.0))
    est = (len(prompt) / 4 * pin + 400 * pout) / 1e6
    calls, usd = _budget()
    if calls >= config.MAX_LLM_CALLS_PER_DAY or usd + est > config.DAILY_AI_BUDGET_USD:
        log.info("LLM budget reached; using template for %s", row["title"])
        return None
    body = json.dumps({"model": config.LLM_MODEL, "max_tokens": 400,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    _, _, resp = request("https://api.anthropic.com/v1/messages", data=body, method="POST", headers={
        "x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
        "content-type": "application/json"})
    r = json.loads(resp)
    u = r.get("usage", {})
    cost = (u.get("input_tokens", 0) * pin + u.get("output_tokens", 0) * pout) / 1e6
    conn.execute("INSERT INTO spend VALUES (?,1,?) ON CONFLICT(day) DO UPDATE SET calls=calls+1, usd=usd+?",
                 (db.today(), cost, cost))
    text = r["content"][0]["text"]
    m = re.search(r"\{.*\}", text, re.S)
    out = json.loads(m.group(0))
    if not (isinstance(out.get("hook"), str) and isinstance(out.get("beats"), list) and out["beats"]):
        return None
    if not out["hook"].strip().endswith("?"):
        return None  # the hook must be a question; fall back to the template
    result = {"hook": out["hook"], "beats": [str(b) for b in out["beats"][:max(2, min(4, len(facts) + 1))]]}
    invented = numbers(" ".join([result["hook"]] + result["beats"])) - numbers(row["title"] + " " + " ".join(facts))
    if invented:  # a statistic not in the sourced facts: never publish it, use the fact-only template
        log.warning("LLM script for %s has unsourced numbers %s; using template", row["title"], sorted(invented))
        return None
    conn.execute("INSERT OR REPLACE INTO llm_cache VALUES (?,?,?,?)",
                 (key, json.dumps(result), cost, time.time()))
    return result


def _day_start():
    return calendar.timegm(time.strptime(db.today(), "%Y-%m-%d"))


def kind(source):
    return "github" if source == "github" else "news"


def videos_today(conn, which=None):
    rows = conn.execute(
        "SELECT t.source FROM contents c JOIN topics t ON t.id=c.topic_id WHERE c.created_at>=?",
        (_day_start(),)).fetchall()
    return sum(1 for r in rows if which is None or kind(r["source"]) == which)


def pick(rows, gh_room, news_room, min_gh, min_news):
    """Top-scoring rows per category, within each category's remaining daily room and score bar."""
    out, room, bar = [], {"github": gh_room, "news": news_room}, {"github": min_gh, "news": min_news}
    for r in sorted(rows, key=lambda r: -r["score"]):
        k = kind(r["source"])
        if room[k] > 0 and r["score"] >= bar[k]:
            out.append(r)
            room[k] -= 1
    return out


def run(conn):
    room = config.MAX_VIDEOS_PER_DAY - videos_today(conn)
    if room <= 0:
        return 0
    rows = conn.execute("SELECT * FROM topics WHERE status='QUEUED' AND renders<?",
                        (config.MAX_RENDERS_PER_TOPIC,)).fetchall()
    wanted = [s.strip() for s in os.environ.get("GENERATE_SOURCES", "").split(",") if s.strip()]
    if wanted:  # e.g. "rss,hn,arxiv" to make news-only videos
        rows = [r for r in rows if r["source"].split(":")[0] in wanted]
    rows = pick(rows, config.DAILY_GITHUB_VIDEOS - videos_today(conn, "github"),
                config.DAILY_NEWS_VIDEOS - videos_today(conn, "news"),
                config.GENERATE_SCORE, min(config.GENERATE_SCORE, config.GENERATE_SCORE_NEWS))[:room]
    n = 0
    for row in rows:
        try:
            db.set_status(conn, row["id"], "GENERATING")
            claims = json.loads(row["claims"] or "[]")
            hook, beats = template_script(row, claims)
            try:
                rewritten = llm_rewrite(conn, row, claims)
            except Exception as e:  # LLM is optional; fall back to the template
                log.warning("llm failed (%s); using template", e)
                rewritten = None
            if rewritten:
                hook, beats = rewritten["hook"], rewritten["beats"]
            cta, resource = pick_cta(row)
            outro = f"Comment {cta} and I'll send you the link."  # works on every platform
            script = " ".join([hook] + beats + [outro])
            caption = f"{hook}\n\nSource: {row['url']}"
            cur = conn.execute(
                """INSERT INTO contents (topic_id,hook,script,title,caption,hashtags,cta_keyword,sources,
                duration_target,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (row["id"], hook, db.js({"hook": hook, "beats": beats, "outro": outro}), row["title"][:90],
                 caption, db.js(HASHTAGS), cta, row["source_urls"], 45, "ready", time.time()))
            conn.execute("INSERT INTO cta_map VALUES (?,?,?,?)",
                         (cur.lastrowid, cta, row["id"], f"Here's the link: {resource}"))
            db.set_status(conn, row["id"], "RENDERING")
            n += 1
        except Exception as e:
            log.warning("generate %s failed: %s", row["title"], e)
            db.fail(conn, row["id"], e, resume_status="QUEUED")
        conn.commit()
    return n
