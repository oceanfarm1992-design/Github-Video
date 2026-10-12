"""WEEKLY RANKING: "Top 5 new GitHub repos this week", ranked by stars.

The ranking is GitHub's own data: repositories created in the last 7 days, sorted by stars (GitHub
search). One topic per ISO week, queued on RANKING_WEEKDAY; the video counts down #5 -> #1 and ends on
a podium of the top 3.
"""
import logging
import re
import time
import urllib.parse

from . import config, db
from .http import get_json, gh_headers

log = logging.getLogger("ranking")
SEARCH = "https://api.github.com/search/repositories?sort=stars&order=desc&per_page=30&q="
MIN_STARS = 50


def clean(desc, limit=100):
    """English-readable description for the narration (the cloned voice reads English), or None."""
    desc = (desc or "").strip()
    if not desc or sum(ch.isascii() for ch in desc) / len(desc) < 0.9:
        return None
    desc = re.sub(r"\s+", " ", re.sub(r"[^\x20-\x7E]", "", desc)).strip(" -:")
    if len(desc) < 15:
        return None
    if len(desc) > limit:
        desc = desc[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "..."
    return desc if desc.endswith((".", "!", "?", "...")) else desc + "."


def build(conn, size=None):
    """Queues this week's ranking topic; returns its id or None."""
    size = size or config.RANKING_SIZE
    week = time.strftime("%G-W%V", time.gmtime())
    tid = db.sha(f"ranking:{week}")[:16]
    if conn.execute("SELECT 1 FROM topics WHERE id=?", (tid,)).fetchone():
        return None
    since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 7 * 86400))
    q = f"created:>={since} stars:>={MIN_STARS} fork:false archived:false"
    data, _ = get_json(conn, SEARCH + urllib.parse.quote(q), gh_headers(), ttl=3600)
    items = []
    for r in data.get("items", []):
        desc = clean(r.get("description"))
        if not desc:
            continue
        items.append({"name": r["full_name"], "url": r["html_url"], "description": desc,
                      "stars": r["stargazers_count"], "language": r.get("language")})
        if len(items) == size:
            break
    if len(items) < 3:
        log.warning("only %d ranked repos with an English description this week; not queued", len(items))
        return None
    for rank, it in enumerate(items, 1):
        it["rank"] = rank
    countdown = list(reversed(items))  # narrated #5 -> #1
    n = len(items)
    title = f"Top {n} new GitHub repos this week"
    claims = [{"text": f"{it['name']} has {it['stars']} stars", "source": it["url"]} for it in items]
    search_url = "https://github.com/search?type=repositories&s=stars&o=desc&q=" + urllib.parse.quote(q)
    conn.execute(
        """INSERT OR IGNORE INTO topics (id,title,source,url,published_at,discovered_at,content_hash,raw,summary,
        claims,source_urls,score,confidence,status,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (tid, title, f"ranking:{week}", search_url, db.now_iso(), db.now_iso(), db.sha(f"ranking:{week}"),
         db.js({"tools": countdown, "week": week, "since": since}), title, db.js(claims),
         db.js([it["url"] for it in items]), 100, 100, "QUEUED", time.time()))
    return tid


def run(conn):
    if not 0 <= config.RANKING_WEEKDAY <= 6 or time.gmtime().tm_wday != config.RANKING_WEEKDAY:
        return 0
    return 1 if build(conn) else 0
