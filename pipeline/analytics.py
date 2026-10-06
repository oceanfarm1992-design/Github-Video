"""ANALYZE + LEARN. Metrics via SQL/Python; learning only nudges bounded weights, never code."""
import json
import logging

from . import config, db
from .http import request
from .publish import youtube_token

log = logging.getLogger("analytics")


def collect(conn):
    posts = conn.execute("SELECT * FROM posts WHERE platform='youtube' AND status='PUBLISHED'").fetchall()
    if not posts:
        return 0
    token = youtube_token()
    if not token:
        return 0
    n = 0
    for i in range(0, len(posts), 50):
        chunk = posts[i:i + 50]
        ids = ",".join(p["post_id"] for p in chunk)
        # uncached on purpose: authenticated responses must not land in the public state database
        _, _, body = request(f"https://www.googleapis.com/youtube/v3/videos?part=statistics&id={ids}",
                             headers={"Authorization": f"Bearer {token}"})
        data = json.loads(body)
        by_id = {it["id"]: it["statistics"] for it in data.get("items", [])}
        for p in chunk:
            s = by_id.get(p["post_id"])
            if s:
                conn.execute(
                    "INSERT INTO metrics (post_row,collected_at,views,likes,comments,shares,saves) VALUES (?,?,?,?,?,?,?)",
                    (p["id"], db.now_iso(), int(s.get("viewCount", 0)), int(s.get("likeCount", 0)),
                     int(s.get("commentCount", 0)), 0, int(s.get("favoriteCount", 0))))
                n += 1
    return n


def learn(conn, min_samples=5):
    """Per-source multiplier = clamp(avg views of source / overall avg). Needs enough data."""
    rows = conn.execute(
        """SELECT t.source AS source, m.views AS views FROM metrics m
        JOIN posts p ON p.id=m.post_row JOIN contents c ON c.id=p.content_id JOIN topics t ON t.id=c.topic_id
        WHERE m.collected_at=(SELECT MAX(collected_at) FROM metrics WHERE post_row=m.post_row)""").fetchall()
    if len(rows) < min_samples:
        return {}
    overall = sum(r["views"] for r in rows) / len(rows) or 1
    per = {}
    for r in rows:
        per.setdefault(r["source"].split(":")[0], []).append(r["views"])
    lo, hi = config.WEIGHT_LIMITS
    out = {}
    for src, v in per.items():
        w = min(hi, max(lo, (sum(v) / len(v)) / overall))
        conn.execute("INSERT OR REPLACE INTO weights VALUES (?,?)", ("src:" + src, w))
        out[src] = round(w, 2)
    return out


def run(conn):
    return collect(conn), learn(conn)
