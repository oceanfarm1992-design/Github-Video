"""ENGAGE: answer keyword comments with the approved link, via official APIs only.

Rules (Skill.md): one primary CTA keyword per video; never guess which link to send; only the
approved response stored in `cta_links` is ever sent. Comment events (personal data) live only
in Supabase, never in the public SQLite/`data` branch.
"""
import json
import logging
import os
import re
import time
import urllib.parse

from . import db, supa
from .http import request
from .publish import GRAPH, _post, youtube_token

log = logging.getLogger("engage")
MAX_REPLIES_PER_RUN = int(os.environ.get("MAX_REPLIES_PER_RUN", "40"))
LOOKBACK_DAYS = 14  # Meta private replies are only allowed within 7 days of the comment


def matches(text, keyword):
    """Whole-word, case-insensitive match of the video's single CTA keyword."""
    return bool(text and re.search(rf"(?<![A-Za-z0-9]){re.escape(keyword)}(?![A-Za-z0-9])", text, re.I))


def sync_links(conn):
    """Mirror cta_map -> Supabase for published posts (public URLs only; idempotent)."""
    n = 0
    rows = conn.execute(
        """SELECT p.platform, p.post_id, m.cta_keyword, m.response FROM posts p
        JOIN cta_map m ON m.video_id=p.content_id WHERE p.status='PUBLISHED'""").fetchall()
    for r in rows:
        url = r["response"].split("link: ", 1)[-1]
        supa.upsert("cta_links", {"platform": r["platform"], "post_id": r["post_id"], "keyword": r["cta_keyword"],
                                  "resource_url": url, "response": r["response"]}, "platform,post_id")
        n += 1
    return n


# ---------------------------------------------------------------- fetchers
def yt_comments(post_id, token, own_channel):
    q = urllib.parse.urlencode({"part": "snippet", "videoId": post_id, "maxResults": 100, "order": "time"})
    _, _, r = request(f"https://www.googleapis.com/youtube/v3/commentThreads?{q}",
                      headers={"Authorization": f"Bearer {token}"})
    for it in json.loads(r).get("items", []):
        s = it["snippet"]["topLevelComment"]["snippet"]
        author = (s.get("authorChannelId") or {}).get("value")
        if author and author != own_channel:
            yield {"comment_id": it["snippet"]["topLevelComment"]["id"], "author_id": author,
                   "author_name": s.get("authorDisplayName"), "body": s.get("textOriginal")}


def yt_own_channel(token):
    _, _, r = request("https://www.googleapis.com/youtube/v3/channels?part=id&mine=true",
                      headers={"Authorization": f"Bearer {token}"})
    items = json.loads(r).get("items", [])
    return items[0]["id"] if items else None


def meta_comments(platform, post_id):
    token = os.environ["META_PAGE_ACCESS_TOKEN"]
    fields = "id,text,from" if platform == "instagram" else "id,message,from"
    _, _, r = request(f"{GRAPH}/{post_id}/comments?fields={fields}&limit=100&access_token={token}")
    for c in json.loads(r).get("data", []):
        frm = c.get("from") or {}
        yield {"comment_id": c["id"], "author_id": frm.get("id"), "author_name": frm.get("name") or frm.get("username"),
               "body": c.get("text") or c.get("message")}


# ----------------------------------------------------------------- repliers
def yt_reply(token, comment_id, text):
    request("https://www.googleapis.com/youtube/v3/comments?part=snippet", method="POST",
            data=json.dumps({"snippet": {"parentId": comment_id, "textOriginal": text}}).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})


def meta_private_reply(comment_id, text):
    """Private reply (DM) to a comment. Page token; needs instagram_manage_messages / pages_messaging."""
    _post(f"{GRAPH}/{os.environ['META_PAGE_ID']}/messages", {
        "recipient": json.dumps({"comment_id": comment_id}), "message": json.dumps({"text": text}),
        "access_token": os.environ["META_PAGE_ACCESS_TOKEN"]})


def run(conn):
    if not supa.configured():
        log.info("Supabase not configured; skipping engage")
        return 0
    synced = sync_links(conn)
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - LOOKBACK_DAYS * 86400))
    posts = conn.execute("SELECT platform, post_id FROM posts WHERE status='PUBLISHED' AND published_at>=?",
                         (since,)).fetchall()
    token = own = None
    sent = 0
    for p in posts:
        if sent >= MAX_REPLIES_PER_RUN:
            break
        link = (supa.select("cta_links", platform=f"eq.{p['platform']}", post_id=f"eq.{p['post_id']}") or [None])[0]
        if not link:
            continue
        try:
            if p["platform"] == "youtube":
                token = token or youtube_token()
                own = own or yt_own_channel(token)
                comments = list(yt_comments(p["post_id"], token, own))
            elif p["platform"] in ("facebook", "instagram") and os.environ.get("META_PAGE_ACCESS_TOKEN"):
                comments = list(meta_comments(p["platform"], p["post_id"]))
            else:
                continue
        except Exception as e:
            log.warning("fetch comments %s/%s failed: %s", p["platform"], p["post_id"], e)
            continue
        for c in comments:
            if sent >= MAX_REPLIES_PER_RUN or not matches(c["body"], link["keyword"]):
                continue
            ev = supa.insert_once("comment_events", {
                "platform": p["platform"], "post_id": p["post_id"], "comment_id": c["comment_id"],
                "author_id": c["author_id"], "author_name": c["author_name"], "body": (c["body"] or "")[:500],
                "keyword": link["keyword"]}, "platform,comment_id")
            if ev is None:
                continue  # already handled in an earlier run
            if c["author_id"] and supa.select("comment_events", platform=f"eq.{p['platform']}", post_id=f"eq.{p['post_id']}",
                                             author_id=f"eq.{c['author_id']}", status="eq.replied"):
                supa.update("comment_events", {"status": "skipped", "error": "author already received link"}, id=f"eq.{ev['id']}")
                continue
            try:
                if p["platform"] == "youtube":
                    yt_reply(token, c["comment_id"], link["response"])
                else:
                    meta_private_reply(c["comment_id"], link["response"])
                supa.update("comment_events", {"status": "replied", "replied_at": db.now_iso()}, id=f"eq.{ev['id']}")
                sent += 1
            except Exception as e:
                log.warning("reply %s failed: %s", c["comment_id"], e)
                supa.update("comment_events", {"status": "failed", "error": str(e)[:300]}, id=f"eq.{ev['id']}")
    return {"links_synced": synced, "replies": sent}
