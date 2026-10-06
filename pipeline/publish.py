"""PUBLISH via official APIs only. Missing credentials => WAITING_FOR_API (no workarounds)."""
import json
import logging
import os
import urllib.parse

from . import db
from .http import request

log = logging.getLogger("publish")


def youtube_token():
    """Prefer a refresh-token flow (tokens expire in 1h); fall back to a raw access token."""
    cid, sec, rt = (os.environ.get(k) for k in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"))
    if cid and sec and rt:
        body = urllib.parse.urlencode({"client_id": cid, "client_secret": sec, "refresh_token": rt,
                                       "grant_type": "refresh_token"}).encode()
        _, _, r = request("https://oauth2.googleapis.com/token", data=body, method="POST",
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
        return json.loads(r)["access_token"]
    return os.environ.get("YOUTUBE_ACCESS_TOKEN") or None


def youtube_upload(token, c):
    """YouTube Data API v3 resumable upload. Shorts are detected by 9:16 + <=60s."""
    meta = {"snippet": {"title": (c["title"] + " #Shorts")[:100],
                        "description": c["caption"] + "\n\n" + " ".join(json.loads(c["hashtags"])),
                        "categoryId": "28"},
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}}
    data = open(c["video_path"], "rb").read()
    _, hdr, _ = request(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        data=json.dumps(meta).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(len(data))})
    loc = {k.lower(): v for k, v in hdr.items()}["location"]
    _, _, r = request(loc, data=data, method="PUT", headers={"Content-Type": "video/mp4"}, attempts=2)
    return json.loads(r)["id"]


def run(conn):
    rows = conn.execute(
        "SELECT c.*, t.id AS tid FROM contents c JOIN topics t ON t.id=c.topic_id "
        "WHERE t.status IN ('READY','WAITING_FOR_API') AND c.status='qc_passed'").fetchall()
    n = 0
    for c in rows:
        if conn.execute("SELECT 1 FROM posts WHERE platform='youtube' AND content_id=?", (c["id"],)).fetchone():
            db.set_status(conn, c["tid"], "PUBLISHED")
            continue
        try:
            token = youtube_token()
            if not token:
                db.set_status(conn, c["tid"], "WAITING_FOR_API", "no YouTube credentials")
                continue
            vid = youtube_upload(token, c)
            conn.execute("INSERT INTO posts (platform,post_id,content_id,published_at,status,url) VALUES (?,?,?,?,?,?)",
                         ("youtube", vid, c["id"], db.now_iso(), "PUBLISHED", f"https://youtube.com/shorts/{vid}"))
            db.set_status(conn, c["tid"], "PUBLISHED")
            n += 1
        except Exception as e:
            log.warning("publish %s failed: %s", c["title"], e)
            db.fail(conn, c["tid"], e, resume_status="READY")
        conn.commit()
    return n
