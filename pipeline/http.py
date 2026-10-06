"""HTTP with timeout, retry + exponential backoff, rate-limit handling and ETag caching."""
import json
import logging
import time
import urllib.error
import urllib.request

from . import config

log = logging.getLogger("http")
UA = "ai-news-pipeline/1.0 (+https://github.com)"


def request(url, headers=None, data=None, method=None, attempts=3):
    h = {"User-Agent": UA, **(headers or {})}
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers=h, data=data, method=method)
            with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            if e.code == 304:
                return 304, dict(e.headers), b""
            last = e
            if e.code in (403, 429):
                reset = e.headers.get("Retry-After")
                wait = min(int(reset), 60) if reset and reset.isdigit() else 2 ** (i + 2)
                log.warning("rate limited %s, wait %ss", url, wait)
                time.sleep(wait)
            elif 500 <= e.code < 600:
                time.sleep(2 ** i)
            else:
                break  # 4xx: don't retry
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            time.sleep(2 ** i)
    raise RuntimeError(f"request failed {url}: {last}")


def cached_get(conn, url, headers=None, ttl=0):
    """GET with ETag/Last-Modified revalidation. Returns (body_text, changed)."""
    row = conn.execute("SELECT * FROM http_cache WHERE url=?", (url,)).fetchone()
    h = dict(headers or {})
    if row:
        if ttl and time.time() - row["fetched_at"] < ttl:
            return row["body"], False
        if row["etag"]:
            h["If-None-Match"] = row["etag"]
        if row["last_modified"]:
            h["If-Modified-Since"] = row["last_modified"]
    status, rh, body = request(url, h)
    if status == 304 and row:
        conn.execute("UPDATE http_cache SET fetched_at=? WHERE url=?", (time.time(), url))
        return row["body"], False
    text = body.decode("utf-8", "replace")
    low = {k.lower(): v for k, v in rh.items()}
    conn.execute(
        "INSERT OR REPLACE INTO http_cache VALUES (?,?,?,?,?)",
        (url, low.get("etag"), low.get("last-modified"), text, time.time()),
    )
    return text, True


def get_json(conn, url, headers=None, ttl=0):
    text, changed = cached_get(conn, url, headers, ttl)
    return json.loads(text), changed


def gh_headers():
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if config.GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    return h


def url_ok(url):
    """Deterministic URL validation (no LLM). HEAD, falling back to GET."""
    if not url or not url.startswith(("http://", "https://")):
        return False
    for method in ("HEAD", "GET"):
        try:
            status, _, _ = request(url, method=method, attempts=2)
            if status < 400:
                return True
        except RuntimeError:
            continue
    return False
