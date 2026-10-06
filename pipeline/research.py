"""RESEARCH + VERIFY: structured claims drawn only from fetched metadata; URLs validated."""
import json
import logging
import time

from . import db
from .http import get_json, gh_headers, page_description, url_ok

log = logging.getLogger("research")


def _fmt(n):
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def research_one(conn, row):
    raw = json.loads(row["raw"] or "{}")
    claims, sources = [], [row["url"]]
    summary = raw.get("description") or ""
    if row["source"] == "github":
        data, _ = get_json(conn, f"https://api.github.com/repos/{row['title']}", gh_headers(), ttl=1800)
        raw.update(stars=data["stargazers_count"], license=(data.get("license") or {}).get("spdx_id"),
                   pushed_at=data["pushed_at"], description=data.get("description"))
        summary = data.get("description") or summary
        claims.append({"text": f"{row['title']} has {_fmt(raw['stars'])} stars on GitHub.", "source": row["url"]})
        if summary:
            claims.append({"text": f"Description: {summary}", "source": row["url"]})
        if raw.get("language"):
            claims.append({"text": f"Written primarily in {raw['language']}.", "source": row["url"]})
        if raw.get("license"):
            claims.append({"text": f"License: {raw['license']}.", "source": row["url"]})
    elif row["source"] == "huggingface":
        claims.append({"text": f"{row['title']} has {_fmt(raw.get('likes', 0))} likes on Hugging Face.",
                       "source": row["url"]})
        if raw.get("pipeline_tag"):
            claims.append({"text": f"Task: {raw['pipeline_tag']}.", "source": row["url"]})
    else:
        if not summary and row["source"] != "arxiv":
            summary = page_description(row["url"]) or ""  # first-party text from the page itself
        if summary:
            claims.append({"text": summary[:300], "source": row["url"]})
        if raw.get("hn_score"):
            claims.append({"text": f"Trending on Hacker News ({raw['hn_score']} points).", "source": row["url"]})
        claims.append({"text": f"Reported by {row['source'].split(':')[-1]}.", "source": row["url"]})
    return raw, summary, claims, sources


def run(conn, limit=30):
    n = 0
    for row in conn.execute("SELECT * FROM topics WHERE status='FILTERED' LIMIT ?", (limit,)).fetchall():
        try:
            db.set_status(conn, row["id"], "RESEARCHING")
            raw, summary, claims, sources = research_one(conn, row)
            valid_url = url_ok(row["url"])
            gh_ok = url_ok(row["github_url"]) if row["github_url"] else True
            sourced = all(c.get("source") for c in claims)
            confidence = round(100 * (0.5 * valid_url + 0.2 * gh_ok + 0.2 * sourced
                                      + 0.1 * (len(claims) >= 2)))
            conn.execute(
                "UPDATE topics SET raw=?, summary=?, claims=?, source_urls=?, confidence=?, updated_at=? WHERE id=?",
                (db.js(raw), summary, db.js(claims), db.js(sources), confidence, time.time(), row["id"]))
            if valid_url and gh_ok and sourced and claims:
                db.set_status(conn, row["id"], "VERIFIED")
                n += 1
            else:
                db.set_status(conn, row["id"], "SKIPPED", "failed verification")
        except Exception as e:
            log.warning("research %s failed: %s", row["title"], e)
            db.fail(conn, row["id"], e, resume_status="FILTERED")
        conn.commit()
    return n
