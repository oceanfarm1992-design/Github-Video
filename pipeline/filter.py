"""FILTER: deterministic relevance + dedupe (URL, repo id, title similarity, content hash)."""
import difflib
import json
import re

from . import config, db


def _norm(t):
    return re.sub(r"[^a-z0-9 ]", "", t.lower())


def relevant(row):
    raw = json.loads(row["raw"] or "{}")
    blob = f"{row['title']} {raw.get('description') or ''} {' '.join(raw.get('topics', []))}".lower()
    if row["source"] in ("arxiv", "huggingface") or row["source"].startswith("rss:"):
        return True  # curated AI sources: every item is on-topic
    return any(k in blob for k in config.AI_KEYWORDS)


def run(conn):
    # one-time retry of RSS items rejected by URL verification (older rule treated bot-blocked 403s as dead)
    conn.execute("UPDATE topics SET status='FILTERED', attempts=1 WHERE status='SKIPPED' AND "
                 "error='failed verification' AND source LIKE 'rss:%' AND attempts=0")
    # self-heal: re-open items skipped as irrelevant by an older, stricter rule
    for r in conn.execute("SELECT * FROM topics WHERE status='SKIPPED' AND error='irrelevant'").fetchall():
        if relevant(r):
            db.set_status(conn, r["id"], "DISCOVERED")
    seen = conn.execute(
        "SELECT title,content_hash FROM topics WHERE status NOT IN ('DISCOVERED','SKIPPED')").fetchall()
    hashes = {r["content_hash"] for r in seen}
    titles = [_norm(r["title"]) for r in seen]
    kept = skipped = 0
    for row in conn.execute("SELECT * FROM topics WHERE status='DISCOVERED'").fetchall():
        nt = _norm(row["title"])
        dup = row["content_hash"] in hashes or any(
            difflib.SequenceMatcher(None, nt, t).ratio() > 0.88 for t in titles[-400:])
        if dup or not relevant(row):
            db.set_status(conn, row["id"], "SKIPPED", "duplicate" if dup else "irrelevant")
            skipped += 1
        else:
            db.set_status(conn, row["id"], "FILTERED")
            hashes.add(row["content_hash"])
            titles.append(nt)
            kept += 1
    return kept, skipped
