"""PUBLISH via official APIs only (YouTube Data API, Meta Graph API).

A platform without credentials is skipped; if no platform is configured the job is
WAITING_FOR_API. Each (platform, content) is published at most once (UNIQUE row in `posts`).
"""
import json
import logging
import os
import time
import urllib.parse

from . import buffer, db, seo, zernio
from .http import request

log = logging.getLogger("publish")
GRAPH = "https://graph.facebook.com/v21.0"


def social_caption(c):
    """Facebook/Instagram/TikTok caption: no link (a link in the caption kills comments). The keyword comment
    triggers the DM with the link, which is what drives engagement. Keyword-first for in-app search."""
    cta = (c.get("_cta") or {}) if isinstance(c, dict) else {}
    kw = cta.get("keyword")
    what = "all the links" if (cta.get("response") or "").startswith("Here are the links") else "the link"
    ask = f"Comment {kw} and I'll DM you {what} 📩" if kw else ""
    return seo.social_caption(c, ask)


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


def link_block(response):
    """The approved CTA response as description text: one link, or the numbered list of a tools video."""
    if not response:
        return ""
    if response.startswith("Here's the link: "):
        return "Link: " + response.split("Here's the link: ", 1)[1]
    return response


def youtube_title(c):
    """Keyword-first title (the subject's name is searchable); YouTube allows 100 characters."""
    return f"{seo.title(c)} #Shorts"


def youtube_upload(token, c, links=None):
    """YouTube Data API v3 resumable upload. Shorts are detected by 9:16 + <=3 min.
    YouTube viewers are sent to the description, so the approved link(s) go there."""
    meta = {"snippet": {"title": youtube_title(c), "description": seo.youtube_description(c, links),
                        "tags": seo.youtube_tags(c), "categoryId": "28",
                        "defaultLanguage": "en", "defaultAudioLanguage": "en"},
            # containsSyntheticMedia: the narration is an AI clone of the owner's voice (YouTube's
            # "altered or synthetic content" disclosure)
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False,
                       "containsSyntheticMedia": True}}
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
        "description": social_caption(c), "access_token": token})
    return s["video_id"], f"https://www.facebook.com/reel/{s['video_id']}"


def instagram_reel(c, video_url):
    """Instagram Reels: create container -> poll until FINISHED -> media_publish."""
    ig, token = os.environ["META_IG_USER_ID"], os.environ["META_PAGE_ACCESS_TOKEN"]
    cont = _post(f"{GRAPH}/{ig}/media", {
        "media_type": "REELS", "video_url": video_url, "share_to_feed": "true", "access_token": token,
        "caption": social_caption(c)})
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
    if zernio.enabled():  # preferred for Facebook/Instagram when configured
        for plat in ("facebook", "instagram"):
            if zernio.available(plat):
                out[plat] = (True, lambda c, url, plat=plat: zernio.publish_reel(plat, c, url))
    elif os.environ.get("META_PAGE_ID") and os.environ.get("META_PAGE_ACCESS_TOKEN"):
        out["facebook"] = (True, facebook_reel)
        if os.environ.get("META_IG_USER_ID"):
            out["instagram"] = (True, instagram_reel)
    if buffer.enabled():  # TikTok + Pinterest through Buffer
        for svc in buffer.SERVICES:
            if buffer.available(svc):
                out[svc] = (True, lambda c, url, svc=svc: buffer.publish(svc, c, url))
    if zernio.enabled() and os.environ.get("STORIES", "1") != "0":  # the same video as a 24 h Story
        for plat in ("instagram", "facebook"):
            if zernio.available(plat):
                out[f"{plat}_story"] = (True, lambda c, url, plat=plat: zernio.publish_story(plat, c, url))
    return out


# platform length limits (seconds); longer videos skip that platform but still go everywhere else
MAX_SECONDS = {"facebook": 60, "instagram_story": 60, "facebook_story": 120}


def youtube_token_available():
    e = os.environ
    return bool(e.get("YOUTUBE_ACCESS_TOKEN") or (e.get("YOUTUBE_CLIENT_ID") and e.get("YOUTUBE_CLIENT_SECRET")
                                                   and e.get("YOUTUBE_REFRESH_TOKEN")))


def video_seconds(path, url=None):
    """Length in seconds from the local file, else from the hosted copy (a retry runs on a fresh runner
    that no longer has the file). 0.0 when unknown."""
    from .qc import probe
    for src in (path, url):
        if not src or not (src.startswith("http") or os.path.exists(src)):
            continue
        try:
            return float(probe(src)["format"]["duration"])
        except Exception as e:
            log.warning("could not read the length of %s: %s", src, e)
    return 0.0


def story_waits(name, done):
    """A Story goes out only after the same video is live as a Reel on that platform."""
    return name.endswith("_story") and name[:-len("_story")] not in done


# ------------------------------------------- durable publish log (Supabase)
def already_published(platform, topic_id):
    """(post_id, url) if Supabase says this topic is already on this platform. The SQLite state is
    saved only at the end of a run, so this is the guard against re-posting after a lost save."""
    from . import supa
    if not supa.configured():
        return None
    try:
        rows = supa.select("published_posts", platform=f"eq.{platform}", topic_id=f"eq.{topic_id}")
        return (rows[0]["post_id"], rows[0].get("url") or "") if rows else None
    except Exception as e:  # table missing (schema not re-run) or Supabase down: fall back to local state
        log.warning("published_posts lookup failed (%s); relying on local state", e)
        return None


def record_published(platform, topic_id, post_id, url):
    from . import supa
    if not supa.configured():
        return
    try:
        supa.upsert("published_posts", {"platform": platform, "topic_id": topic_id, "post_id": str(post_id),
                                        "url": url, "published_at": db.now_iso()}, "platform,topic_id")
    except Exception as e:
        log.warning("published_posts write failed: %s", e)


def cleanup_release_assets(days=14):
    """Delete hosted videos older than `days` from the public `videos` release (Instagram/Facebook
    fetch them within minutes of posting); keeps the release small and stops old files lingering."""
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        return 0
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    try:
        _, _, r = request(f"https://api.github.com/repos/{repo}/releases/tags/videos", headers=h)
    except RuntimeError:
        return 0
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days * 86400))
    n = 0
    for a in json.loads(r).get("assets", []):
        if a.get("created_at", "") < cutoff:
            try:
                request(f"https://api.github.com/repos/{repo}/releases/assets/{a['id']}", method="DELETE", headers=h)
                n += 1
            except RuntimeError as e:
                log.warning("could not delete old asset %s: %s", a.get("name"), e)
    return n


def run(conn):
    try:
        removed = cleanup_release_assets()
        if removed:
            log.info("removed %d hosted videos older than 14 days", removed)
    except Exception as e:
        log.warning("release cleanup failed: %s", e)
    configured = platforms()
    rows = conn.execute(
        "SELECT c.*, t.id AS tid, t.source AS tsource, t.raw AS traw FROM contents c JOIN topics t ON t.id=c.topic_id "
        "WHERE t.status IN ('READY','WAITING_FOR_API') AND c.status='qc_passed'").fetchall()
    n = 0
    for c in rows:
        c = dict(c)
        m = conn.execute("SELECT cta_keyword, response FROM cta_map WHERE video_id=?", (c["id"],)).fetchone()
        c["_cta"] = {"keyword": m["cta_keyword"], "response": m["response"]} if m else None
        if not configured:
            db.set_status(conn, c["tid"], "WAITING_FOR_API", "no platform credentials")
            continue
        done = {r["platform"] for r in conn.execute("SELECT platform FROM posts WHERE content_id=?", (c["id"],))}
        errors, video_url, seconds = [], None, None
        for name, (needs_url, fn) in configured.items():
            if name in done:
                continue
            if story_waits(name, done):
                log.info("skip %s for %s: the video is not live there yet", name, c["title"])
                continue
            try:
                prior = already_published(name, c["tid"])
                if not prior and name in MAX_SECONDS:
                    if seconds is None:
                        local = c["video_path"] and os.path.exists(c["video_path"])
                        video_url = video_url or (None if local else public_video_url(c))
                        seconds = video_seconds(c["video_path"], video_url)
                    if not 0 < seconds <= MAX_SECONDS[name]:
                        why = f"longer than its {MAX_SECONDS[name]} s limit" if seconds else "length unknown"
                        log.info("skip %s for %s: %s", name, c["title"], why)
                        continue
                if prior:  # durable guard: survives a lost local state
                    post_id, url = prior
                    log.info("%s already has %s on %s; recording, not re-posting", c["title"], post_id, name)
                elif name == "youtube":
                    tok = youtube_token()
                    post_id, url = youtube_upload(tok, c, link_block((c.get("_cta") or {}).get("response")))
                    seo.add_to_playlist(conn, tok, c, post_id)
                else:
                    video_url = video_url or public_video_url(c)
                    if not video_url:
                        raise RuntimeError("no public video URL (run inside GitHub Actions)")
                    post_id, url = fn(c, video_url)
                conn.execute(
                    "INSERT INTO posts (platform,post_id,content_id,published_at,status,url) VALUES (?,?,?,?,?,?)",
                    (name, post_id, c["id"], db.now_iso(), "PUBLISHED", url))
                if not prior:
                    record_published(name, c["tid"], post_id, url)
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
