"""SCORE: pure deterministic scoring, 0-100, strategy weights applied."""
import json
import math
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

from . import config

SOURCE_QUALITY = {"github": 0.8, "huggingface": 0.8, "arxiv": 0.7, "hn": 0.6}
DATE_FORMATS = ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z",
                "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z")


def weights(conn):
    w = {r["name"]: r["value"] for r in conn.execute("SELECT * FROM weights")}
    lo, hi = config.WEIGHT_LIMITS
    return lambda k: min(hi, max(lo, w.get(k, 1.0)))


def age_days(ts):
    if not ts:
        return 30.0
    try:
        d = parsedate_to_datetime(ts)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - d).total_seconds() / 86400)
    except (TypeError, ValueError):
        pass
    for fmt in DATE_FORMATS:
        try:
            d = datetime.strptime(ts.replace("Z", "+0000") if "%z" in fmt and ts.endswith("Z") else ts, fmt)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - d).total_seconds() / 86400)
        except ValueError:
            continue
    return 30.0


def score_row(row, w, duplicates=0):
    raw = json.loads(row["raw"] or "{}")
    src = row["source"].split(":")[0]
    novelty = max(0.0, 1 - age_days(row["published_at"]) / 14)
    pop = raw.get("stars") or raw.get("likes") or raw.get("hn_score") or 0
    trend = min(1.0, math.log10(pop + 1) / 4) if pop else 0.3
    quality = SOURCE_QUALITY.get(src, 0.9 if row["source"].startswith("rss:") else 0.5)
    has_repo = bool(row["github_url"]) or src == "github"
    usefulness = 0.5 + 0.3 * has_repo + 0.2 * bool(raw.get("license"))
    video = 0.4 + 0.3 * bool(row["summary"]) + 0.3 * (len(row["title"]) < 60)
    text = (row["title"] + (row["summary"] or "")).lower()
    audience = min(1.0, 0.4 + 0.15 * sum(k in text for k in ("llm", "agent", "open", "model", "rag", "mcp")))
    parts = {
        "novelty": 20 * novelty, "usefulness": 20 * usefulness, "trend": 20 * trend,
        "source_quality": 15 * quality * w("src:" + src), "video_potential": 10 * video, "audience_relevance": 15 * audience,
    }
    total = sum(v * w(k) for k, v in parts.items()) - 15 * duplicates
    return round(max(0.0, min(100.0, total)), 1)


def run(conn):
    w = weights(conn)
    n = 0
    for row in conn.execute("SELECT * FROM topics WHERE status='VERIFIED'").fetchall():
        s = score_row(row, w)
        if s < config.MIN_TOPIC_SCORE:
            conn.execute("UPDATE topics SET score=?, status='SKIPPED', error='low score', updated_at=? WHERE id=?",
                         (s, time.time(), row["id"]))
        else:
            conn.execute("UPDATE topics SET score=?, status='QUEUED', updated_at=? WHERE id=?",
                         (s, time.time(), row["id"]))
            n += 1
    return n
