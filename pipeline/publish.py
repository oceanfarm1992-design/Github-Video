"""PUBLISH via official APIs only (YouTube Data API, Meta Graph API).

A platform without credentials is skipped; if no platform is configured the job is
WAITING_FOR_API. Each (platform, content) is published at most once (UNIQUE row in `posts`).
"""
import json
import logging
import os
import time
import urllib.parse

from . import db
from .http import request

log = logging.getLogger("publish")
GRAPH = "https://graph.facebook.com/v21.0"


def _form(d):
    return urllib.parse.urlencode(d).encode()


def _post(url, data, headers=None):
    _, _, r = request(url, data=_form(data), method="POST",
                      headers={"Content-Type": "application/x-www-form-urlencoded", **(headers or {})})
    return json.loads(r)


# ---------------------------------------------------------------- YouTube
def youtube_token():
    """Prefer a refresh-token flow (tokens expire in 1h); fall back to a raw access token."""
    cid, sec, rt = (os.environ.get(k) for k in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"))
    if cid and sec and rt:
        return _post("https://oauth2.googleapis.com/token", {
            "client_id": cid, "client_secret": sec, "refresh_token": rt, "grant_type": "refresh_token"})["access_token"]
    return os.environ.get("YOUTUBE_ACCESS_TOKEN") or None


def youtube_upload(token, c, resource_url=None):
    """YouTube Data API v3 resumable upload. Shorts are detected by 9:16 + <=60s.
    YouTube viewers are sent to the description, so the approved resource link goes there."""
    desc = c["caption"] + (f"\n\nLink: {resource_url}" if resource_url else "")
    meta = {"snippet": {"title": (c["title"] + " #Shorts")[:100],
                        "description": desc + "\n\n" + " ".join(json.loads(c["hashtags"])),
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
    vid = json.loads(r)["id"]
    return vid, f"https://youtube.com/shorts/{vid}"


# ------------------------------------------------- public hosting (for Instagram)
def public_video_url(c):
    """Host the mp4 as a GitHub release asset (public repo => public URL). Needs Actions env."""
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        return None
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    name = os.path.basename(c["video_path"])
    api = f"https://api.github.com/repos/{repo}/releases"
    try:
        _, _, r = request(f"{api}/tags/videos", headers=h)
        rel = json.loads(r)
    except RuntimeError:
        _, _, r = request(api, method="POST", headers={**h, "Content-Type": "application/json"},
                          data=json.dumps({"tag_name": "videos", "name": "Rendered videos",
                                           "body": "Public hosting for rendered videos."}).encode())
        rel = json.loads(r)
    if not any(a["name"] == name for a in rel.get("assets", [])):
        request(f"https://uploads.github.com/repos/{repo}/releases/{rel['id']}/assets?name={name}",
                method="POST", data=open(c["video_path"], "rb").read(), attempts=2,
                headers={**h, "Content-Type": "video/mp4"})
    return f"https://github.com/{repo}/releases/download/videos/{name}"


# ------------------------------------------------------------------ Meta
def facebook_reel(c, video_url):
    """Facebook Page Reels: start -> hosted-file upload -> finish/publish."""
    page, token = os.environ["META_PAGE_ID"], os.environ["META_PAGE_ACCESS_TOKEN"]
    s = _post(f"{GRAPH}/{page}/video_reels", {"upload_phase": "start", "access_token": token})
    request(s["upload_url"], method="POST", data=b"",
            headers={"Authorization": f"OAuth {token}", "file_url": video_url})
    _post(f"{GRAPH}/{page}/video_reels", {
        "upload_phase": "finish", "video_id": s["video_id"], "video_state": "PUBLISHED",
        "description": c["caption"] + "\n" + " ".join(json.loads(c["hashtags"])), "access_token": token})
    return s["video_id"], f"https://www.facebook.com/reel/{s['video_id']}"


def instagram_reel(c, video_url):
    """Instagram Reels: create container -> poll until FINISHED -> media_publish."""
    ig, token = os.environ["META_IG_USER_ID"], os.environ["META_PAGE_ACCESS_TOKEN"]
    cont = _post(f"{GRAPH}/{ig}/media", {
        "media_type": "REELS", "video_url": video_url, "share_to_feed": "true", "access_token": token,
        "caption": c["caption"] + "\n" + " ".join(json.loads(c["hashtags"]))})
    for _ in range(30):
        _, _, r = request(f"{GRAPH}/{cont['id']}?fields=status_code&access_token={token}")
        status = json.loads(r).get("status_code")
        if status == "FINISHED":
            break
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"instagram container {status}")
        time.sleep(10)
    else:
        raise RuntimeError("instagram container timed out")
    media = _post(f"{GRAPH}/{ig}/media_publish", {"creation_id": cont["id"], "access_token": token})
    _, _, r = request(f"{GRAPH}/{media['id']}?fields=permalink&access_token={token}")
    return media["id"], json.loads(r).get("permalink", "")


def platforms():
    """name -> (needs_public_url, callable); only platforms with credentials."""
    out = {}
    if youtube_token_available():
        out["youtube"] = (False, None)
    if os.environ.get("META_PAGE_ID") and os.environ.get("META_PAGE_ACCESS_TOKEN"):
        out["facebook"] = (True, facebook_reel)
        if os.environ.get("META_IG_USER_ID"):
            out["instagram"] = (True, instagram_reel)
    return out


def youtube_token_available():
    e = os.environ
    return bool(e.get("YOUTUBE_ACCESS_TOKEN") or (e.get("YOUTUBE_CLIENT_ID") and e.get("YOUTUBE_CLIENT_SECRET")
                                                   and e.get("YOUTUBE_REFRESH_TOKEN")))


def run(conn):
    configured = platforms()
    rows = conn.execute(
        "SELECT c.*, t.id AS tid FROM contents c JOIN topics t ON t.id=c.topic_id "
        "WHERE t.status IN ('READY','WAITING_FOR_API') AND c.status='qc_passed'").fetchall()
    n = 0
    for c in rows:
        if not configured:
            db.set_status(conn, c["tid"], "WAITING_FOR_API", "no platform credentials")
            continue
        done = {r["platform"] for r in conn.execute("SELECT platform FROM posts WHERE content_id=?", (c["id"],))}
        errors, video_url = [], None
        for name, (needs_url, fn) in configured.items():
            if name in done:
                continue
            try:
                if name == "youtube":
                    m = conn.execute("SELECT response FROM cta_map WHERE video_id=?", (c["id"],)).fetchone()
                    resource = m["response"].split("link: ", 1)[-1] if m else None
                    post_id, url = youtube_upload(youtube_token(), c, resource)
                else:
                    video_url = video_url or public_video_url(c)
                    if not video_url:
                        raise RuntimeError("no public video URL (run inside GitHub Actions)")
                    post_id, url = fn(c, video_url)
                conn.execute(
                    "INSERT INTO posts (platform,post_id,content_id,published_at,status,url) VALUES (?,?,?,?,?,?)",
                    (name, post_id, c["id"], db.now_iso(), "PUBLISHED", url))
                done.add(name)
                n += 1
            except Exception as e:
                log.warning("publish %s to %s failed: %s", c["title"], name, e)
                errors.append(f"{name}: {e}")
            conn.commit()
        if errors:
            db.fail(conn, c["tid"], "; ".join(errors), resume_status="READY")
        else:
            db.set_status(conn, c["tid"], "PUBLISHED")
        conn.commit()
    return n
