import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
  id TEXT PRIMARY KEY, title TEXT, source TEXT, url TEXT, github_url TEXT,
  repo_id TEXT, published_at TEXT, discovered_at TEXT, content_hash TEXT,
  raw TEXT, summary TEXT, claims TEXT, source_urls TEXT,
  score REAL DEFAULT 0, confidence REAL DEFAULT 0,
  status TEXT DEFAULT 'DISCOVERED', attempts INTEGER DEFAULT 0,
  next_retry_at REAL DEFAULT 0, resume_status TEXT, error TEXT, renders INTEGER DEFAULT 0,
  updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_topics_status ON topics(status);
CREATE INDEX IF NOT EXISTS idx_topics_hash ON topics(content_hash);
CREATE TABLE IF NOT EXISTS contents (
  id INTEGER PRIMARY KEY AUTOINCREMENT, topic_id TEXT, hook TEXT, script TEXT,
  title TEXT, caption TEXT, hashtags TEXT, cta_keyword TEXT, sources TEXT,
  duration_target INTEGER, status TEXT, video_path TEXT, template TEXT,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS cta_map (
  video_id INTEGER PRIMARY KEY, cta_keyword TEXT, resource_id TEXT, response TEXT
);
CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT, post_id TEXT, content_id INTEGER,
  published_at TEXT, status TEXT, url TEXT, UNIQUE(platform, content_id)
);
CREATE TABLE IF NOT EXISTS metrics (
  post_row INTEGER, collected_at TEXT, views INTEGER, likes INTEGER, comments INTEGER,
  shares INTEGER, saves INTEGER, followers INTEGER, profile_visits INTEGER,
  link_clicks INTEGER, cta_comments INTEGER, dm_requests INTEGER
);
CREATE TABLE IF NOT EXISTS http_cache (
  url TEXT PRIMARY KEY, etag TEXT, last_modified TEXT, body TEXT, fetched_at REAL
);
CREATE TABLE IF NOT EXISTS llm_cache (key TEXT PRIMARY KEY, response TEXT, cost REAL, created_at REAL);
CREATE TABLE IF NOT EXISTS spend (day TEXT, calls INTEGER, usd REAL, PRIMARY KEY(day));
CREATE TABLE IF NOT EXISTS weights (name TEXT PRIMARY KEY, value REAL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""


def today():
    return time.strftime("%Y-%m-%d", time.gmtime())


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha(text):
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def connect(path=None):
    path = path or config.DB_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def db(path=None):
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def set_status(conn, topic_id, status, error=None):
    conn.execute(
        "UPDATE topics SET status=?, error=?, updated_at=? WHERE id=?",
        (status, error, time.time(), topic_id),
    )


def fail(conn, topic_id, error, resume_status="DISCOVERED"):
    """Exponential backoff: 2^attempt * 10 min; FAILED after MAX_ATTEMPTS."""
    row = conn.execute("SELECT attempts FROM topics WHERE id=?", (topic_id,)).fetchone()
    attempts = (row["attempts"] if row else 0) + 1
    if attempts >= config.MAX_ATTEMPTS:
        status, nxt = "FAILED", 0
    else:
        status, nxt = "RETRY", time.time() + (2 ** attempts) * 600
    conn.execute(
        "UPDATE topics SET status=?, attempts=?, next_retry_at=?, resume_status=?, error=?, updated_at=? WHERE id=?",
        (status, attempts, nxt, resume_status, str(error)[:500], time.time(), topic_id),
    )


def requeue_retries(conn):
    conn.execute(
        "UPDATE topics SET status=resume_status, updated_at=? "
        "WHERE status='RETRY' AND next_retry_at<=?",
        (time.time(), time.time()),
    )


def js(v):
    return json.dumps(v, ensure_ascii=False)


def kv_get(conn, key, default=None):
    r = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def kv_set(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, value))


def prune(conn, http_days=7):
    """Keep the public state database small: drop cached pages older than `http_days` and stale
    comment-check markers. Dedupe history (topics, posts) is kept."""
    cutoff = time.time() - http_days * 86400
    n = conn.execute("DELETE FROM http_cache WHERE fetched_at < ?", (cutoff,)).rowcount
    conn.execute("DELETE FROM kv WHERE key LIKE 'checked:%' AND CAST(value AS REAL) < ?", (time.time() - 14 * 86400,))
    return n
