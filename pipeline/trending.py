"""No. 1 GITHUB REPO OF THE DAY: GitHub's own daily Trending list (https://github.com/trending).

Once a day the produce run reads the Trending page, takes the top repository not covered before, and
queues it as the day's first GitHub video (score 100, so it takes the 9:05 slot). Its real rank and
"stars today" are kept, so a title only says "No. 1" when GitHub ranked it No. 1.
"""
import html
import json
import logging
import re
import time

from . import db
from .http import get_json, gh_headers, request
from .research import _fmt

log = logging.getLogger("trending")
URL = "https://github.com/trending?since=daily"
ARTICLE_RE = re.compile(r'<article class="Box-row".*?</article>', re.S)
NAME_RE = re.compile(r'<h2 class="h3 lh-condensed">\s*<a [^>]*href="/([^"/]+/[^"/]+)"', re.S)
TODAY_RE = re.compile(r"([\d,]+) stars today")


def parse(page):
    """[(rank, "owner/repo", stars_today)] in GitHub's order."""
    out = []
    for i, art in enumerate(ARTICLE_RE.findall(page), 1):
        m = NAME_RE.search(art)
        if not m:
            continue
        t = TODAY_RE.search(art)
        out.append((i, html.unescape(m.group(1)), int(t.group(1).replace(",", "")) if t else 0))
    return out


MADE = ('GENERATING', 'RENDERING', 'QC', 'READY', 'RETRY', 'WAITING_FOR_API', 'PUBLISHED')


def covered(conn, full):
    """Already made into a video (or in the making). A repo only waiting in the queue is upgraded instead."""
    return conn.execute(f"SELECT 1 FROM topics WHERE source='github' AND title=? AND status IN {MADE}",
                        (full,)).fetchone() is not None


def demote_stale(conn, keep=None):
    """A queued trending topic from another day (or replaced today) loses its label: "today" would be false."""
    import json
    for r in conn.execute("SELECT id, raw FROM topics WHERE source='github' AND status='QUEUED' AND raw LIKE '%\"trending\"%'"):
        raw = json.loads(r["raw"] or "{}")
        tr = raw.get("trending")
        if tr and r["id"] != keep and (tr.get("date") != db.today() or keep):
            raw.pop("trending")
            conn.execute("UPDATE topics SET raw=?, score=80 WHERE id=?", (db.js(raw), r["id"]))


def build(conn, entries, max_rank=10):
    from .ranking import clean
    for rank, full, today in entries[:max_rank]:
        if covered(conn, full):
            continue
        data, _ = get_json(conn, f"https://api.github.com/repos/{full}", gh_headers(), ttl=1800)
        desc = clean(data.get("description"), limit=160)
        if not desc or data.get("fork") or data.get("archived"):
            log.info("trending #%d %s: no English description (or fork/archived), next", rank, full)
            continue
        url = data["html_url"]
        raw = {"description": data.get("description"), "stars": data["stargazers_count"],
               "language": data.get("language"), "topics": data.get("topics", []),
               "license": (data.get("license") or {}).get("spdx_id"), "created_at": data["created_at"],
               "pushed_at": data["pushed_at"], "homepage": data.get("homepage"),
               "trending": {"rank": rank, "stars_today": today, "date": db.today()}}
        claims = [{"text": f"{full} is number {rank} on GitHub Trending today"
                           + (f", with {_fmt(today)} new stars today." if today else "."), "source": URL},
                  {"text": f"{full} has {_fmt(raw['stars'])} stars on GitHub.", "source": url},
                  {"text": f"Description: {data.get('description')}", "source": url}]
        if raw["language"]:
            claims.append({"text": f"Written primarily in {raw['language']}.", "source": url})
        if raw["license"]:
            claims.append({"text": f"License: {raw['license']}.", "source": url})
        tid = db.sha(f"gh:{data['id']}")[:16]  # same id discover uses, so the repo is never made twice
        conn.execute(f"DELETE FROM topics WHERE (id=? OR (source='github' AND title=?)) AND status NOT IN {MADE}",
                     (tid, full))
        conn.execute(
            """INSERT OR IGNORE INTO topics (id,title,source,url,github_url,repo_id,published_at,discovered_at,
            content_hash,raw,summary,claims,source_urls,score,confidence,status,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (tid, full, "github", url, url, f"gh:{data['id']}", data["created_at"], db.now_iso(),
             db.sha((full + url).lower()), db.js(raw), data.get("description"), db.js(claims),
             db.js([url, URL]), 100, 100, "QUEUED", time.time()))
        demote_stale(conn, keep=tid)
        log.info("No. %d trending today: %s (%s stars today)", rank, full, today)
        return tid
    log.warning("no new trending repo in the top %d today", max_rank)
    return None


def run(conn):
    """Queue today's trending repo. Re-checked each run until today's pick is No. 1 or is being made."""
    key = f"trending:{db.today()}"
    demote_stale(conn)
    done = db.kv_get(conn, key)
    if done:
        r = conn.execute("SELECT raw, status FROM topics WHERE id=?", (done,)).fetchone()
        if not r or r["status"] != "QUEUED" or (info(json.loads(r["raw"] or "{}")) or {}).get("rank") == 1:
            return 0
    try:
        _, _, body = request(URL, headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0"}, timeout=30)
        entries = parse(body.decode("utf-8", "replace"))
    except Exception as e:
        log.warning("trending page failed: %s", e)
        return 0
    if not entries:
        log.warning("trending page had no repositories (layout changed?)")
        return 0
    tid = build(conn, entries)
    db.kv_set(conn, key, tid or done or "none")
    return 1 if tid else 0


def trending_queued(conn, tid):
    return conn.execute("SELECT 1 FROM topics WHERE id=? AND status='QUEUED'", (tid,)).fetchone() is not None


def info(row_or_raw):
    """The trending block of a topic (or None): {"rank", "stars_today", "date"}."""
    import json
    raw = row_or_raw if isinstance(row_or_raw, dict) else {}
    if not raw and row_or_raw is not None:
        try:
            raw = json.loads(row_or_raw["raw"] or "{}")
        except (KeyError, TypeError, ValueError):
            raw = {}
    return raw.get("trending")


def label(rank):
    return "No. 1 GitHub Repo of the Day" if rank == 1 else f"No. {rank} on GitHub Trending Today"
