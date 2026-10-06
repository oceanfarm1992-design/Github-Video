"""DISCOVER: cheap metadata only. A failing source never stops the others."""
import logging
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from . import config, db
from .score import age_days
from .http import cached_get, get_json, gh_headers

log = logging.getLogger("discover")

RSS_FEEDS = {
    "openai": "https://openai.com/news/rss.xml",
    "huggingface": "https://huggingface.co/blog/feed.xml",
    "google-ai": "https://blog.google/technology/ai/rss/",
    "aws-ml": "https://aws.amazon.com/blogs/machine-learning/feed/",
}
ARXIV = ("http://export.arxiv.org/api/query?search_query=cat:cs.AI+OR+cat:cs.CL+OR+cat:cs.LG"
         "&sortBy=submittedDate&sortOrder=descending&max_results=25")
HN = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{}.json"
HF_MODELS = "https://huggingface.co/api/models?sort=trendingScore&limit=20"
MAX_AGE_DAYS = 14
ATOM = "{http://www.w3.org/2005/Atom}"


def _strip(t):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t or "")).strip()


def add(conn, **t):
    """Insert a candidate; returns 1 if new. Exact-id dedupe only; fuzzy dedupe is FILTER's job."""
    if t["source"] != "github" and age_days(t.get("published_at")) > MAX_AGE_DAYS:
        return 0  # skip stale history (RSS feeds expose years of posts)
    tid = db.sha(t.get("repo_id") or t["url"])[:16]
    if conn.execute("SELECT 1 FROM topics WHERE id=?", (tid,)).fetchone():
        return 0
    conn.execute(
        """INSERT INTO topics (id,title,source,url,github_url,repo_id,published_at,discovered_at,
        content_hash,raw,status,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (tid, t["title"], t["source"], t["url"], t.get("github_url"), t.get("repo_id"),
         t.get("published_at"), db.now_iso(), db.sha((t["title"] + t["url"]).lower()),
         db.js(t.get("raw", {})), "DISCOVERED", time.time()),
    )
    return 1


def github_search(conn):
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    queries = [
        f"topic:llm created:>{since} stars:>100",
        f"topic:ai-agents created:>{since} stars:>100",
        f"topic:machine-learning created:>{since} stars:>150",
        f"topic:llm pushed:>{since} stars:>2000",
    ]
    n = 0
    for q in queries:
        url = ("https://api.github.com/search/repositories?per_page=20&sort=stars&order=desc&q="
               + urllib.parse.quote(q))
        data, _ = get_json(conn, url, gh_headers(), ttl=1500)
        for r in data.get("items", []):
            n += add(conn, title=r["full_name"], source="github", url=r["html_url"],
                     github_url=r["html_url"], repo_id=f"gh:{r['id']}", published_at=r["created_at"],
                     raw={"description": r.get("description"), "stars": r["stargazers_count"],
                          "language": r.get("language"), "topics": r.get("topics", []),
                          "license": (r.get("license") or {}).get("spdx_id"),
                          "created_at": r["created_at"], "pushed_at": r["pushed_at"],
                          "homepage": r.get("homepage")})
    return n


def rss(conn, name, url):
    text, changed = cached_get(conn, url)
    if not changed:
        return 0
    root = ET.fromstring(text)
    n = 0
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        title = _strip(it.findtext("title"))
        if link and title:
            n += add(conn, title=title, source=f"rss:{name}", url=link,
                     published_at=it.findtext("pubDate"),
                     raw={"description": _strip(it.findtext("description"))[:600]})
    for e in root.iter(ATOM + "entry"):
        l = e.find(ATOM + "link")
        link = l.get("href") if l is not None else ""
        title = _strip(e.findtext(ATOM + "title"))
        if link and title:
            n += add(conn, title=title, source=f"rss:{name}", url=link,
                     published_at=e.findtext(ATOM + "updated"),
                     raw={"description": _strip(e.findtext(ATOM + "summary"))[:600]})
    return n


def arxiv(conn):
    text, changed = cached_get(conn, ARXIV, ttl=3 * 3600)
    if not changed:
        return 0
    n = 0
    for e in ET.fromstring(text).iter(ATOM + "entry"):
        link = (e.findtext(ATOM + "id") or "").strip()
        n += add(conn, title=_strip(e.findtext(ATOM + "title")), source="arxiv", url=link,
                 published_at=e.findtext(ATOM + "published"),
                 raw={"description": _strip(e.findtext(ATOM + "summary"))[:600]})
    return n


def hackernews(conn):
    ids, _ = get_json(conn, HN, ttl=900)
    n = 0
    for i in ids[:60]:
        item, _ = get_json(conn, HN_ITEM.format(i), ttl=24 * 3600)
        if not item or item.get("type") != "story" or not item.get("url"):
            continue
        title = item.get("title", "")
        if any(k in title.lower() for k in config.AI_KEYWORDS):
            gh = item["url"] if "github.com/" in item["url"] else None
            n += add(conn, title=title, source="hn", url=item["url"], github_url=gh,
                     published_at=datetime.fromtimestamp(item["time"], timezone.utc).isoformat(),
                     raw={"hn_score": item.get("score", 0), "hn_comments": item.get("descendants", 0)})
    return n


def huggingface(conn):
    data, _ = get_json(conn, HF_MODELS, ttl=3 * 3600)
    n = 0
    for m in data:
        n += add(conn, title=m["id"], source="huggingface", url=f"https://huggingface.co/{m['id']}",
                 published_at=m.get("createdAt"),
                 raw={"likes": m.get("likes", 0), "downloads": m.get("downloads", 0),
                      "pipeline_tag": m.get("pipeline_tag")})
    return n


def run(conn):
    sources = [("github", github_search), ("arxiv", arxiv), ("hn", hackernews), ("hf", huggingface)]
    sources += [(f"rss:{k}", (lambda c, k=k, u=u: rss(c, k, u))) for k, u in RSS_FEEDS.items()]
    total = 0
    for name, fn in sources:
        try:
            n = fn(conn)
            conn.commit()
            log.info("%s: %d new", name, n)
            total += n
        except Exception as e:  # one failed source must not stop the pipeline
            log.warning("source %s failed: %s", name, e)
    return total
