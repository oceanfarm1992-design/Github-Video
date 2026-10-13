"""Publishing a long video: YouTube (regular video with chapters, thumbnail, playlist, first comment) and
the Facebook Page (feed video through Zernio, links in the first comment). Records go to Supabase so a
subject is never covered twice (the long job does not save the shared SQLite state)."""
import json
import logging
import os

from .. import publish as short_publish, seo, supa, zernio
from ..http import request

log = logging.getLogger("longform.publish")
AI_VOICE = "Narrated with an AI clone of the creator's own voice. Facts come from each project's or site's own pages."
TAGS = {"github": ["#OpenSource", "#GitHub", "#AI"], "tools": ["#AITools", "#AI", "#Tech"],
        "sites": ["#UsefulWebsites", "#Websites", "#TechTips"]}


def stamp(sec):
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02}:{sec % 60:02}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02}"


def links_text(m):
    return "\n".join(f"- {name}: {url}" for name, url in m["links"])


def description(m, script, chapters):
    parts = [script["description"].strip(),
             "Chapters:\n" + "\n".join(f"{stamp(t)} {title}" for t, title in chapters),
             ("Links:\n" if len(m["links"]) > 1 else "Link:\n") + links_text(m),
             AI_VOICE, " ".join(TAGS.get(m["format"], []))]
    return "\n\n".join(p for p in parts if p)[:4900]


def youtube(conn, m, script, mp4, chapters, thumb):
    tok = short_publish.youtube_token()
    if not tok:
        return None
    tags = [t[:60] for t in script.get("tags", [])][:12]
    meta = {"snippet": {"title": script["title"][:100], "description": description(m, script, chapters),
                        "tags": tags, "categoryId": "28", "defaultLanguage": "en", "defaultAudioLanguage": "en"},
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}}
    data = open(mp4, "rb").read()
    _, hdr, _ = request(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        data=json.dumps(meta).encode(), method="POST",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(len(data))})
    loc = {k.lower(): v for k, v in hdr.items()}["location"]
    _, _, r = request(loc, data=data, method="PUT", headers={"Content-Type": "video/mp4"}, attempts=2, timeout=900)
    vid = json.loads(r)["id"]
    try:  # custom thumbnails need a verified channel; without it YouTube picks a frame
        request(f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={vid}", method="POST",
                data=open(thumb, "rb").read(), headers={"Authorization": f"Bearer {tok}", "Content-Type": "image/jpeg"})
    except RuntimeError as e:
        log.warning("thumbnail not set (verify the channel to allow custom thumbnails): %s", e)
    c = {"tsource": "long:" + m["format"], "title": script["title"]}
    seo.add_to_playlist(conn, tok, c, vid)
    short_publish.youtube_first_comment(tok, vid, c, links_text(m))
    return vid, f"https://youtu.be/{vid}"


def facebook(m, script, mp4, chapters):
    if not zernio.available("facebook"):
        return None
    url = short_publish.public_asset_url(str(mp4), "video/mp4")
    if not url:
        raise RuntimeError("no public video URL (run inside GitHub Actions)")
    acct = zernio.account("facebook")
    text = f"{script['title']}\n\n{script['description']}\n\n" + "\n".join(f"{stamp(t)} {title}" for t, title in chapters)
    body = {"content": text[:5000], "mediaItems": [{"type": "video", "url": url}],
            "platforms": [{"platform": "facebook", "accountId": acct["_id"],
                           "platformSpecificData": {"firstComment": links_text(m)}}],
            "publishNow": True}
    post = zernio._call("/posts", body, timeout=600).get("post", {})
    pid = post.get("_id")
    if not pid:
        raise RuntimeError("zernio facebook: no post id")
    plat = zernio.wait_published(pid, "facebook", timeout=1800)  # long uploads take Meta a while
    return pid, plat.get("platformPostUrl", "")


def used_keys():
    """Subjects already covered by a long video (Supabase), so none is repeated."""
    if not supa.configured():
        log.warning("Supabase not configured: long videos cannot check for repeated subjects")
        return set()
    try:
        rows = supa.select("published_posts", platform="in.(youtube_long,facebook_long,long_subject)",
                           select="topic_id")
        return {r["topic_id"] for r in rows}
    except Exception as e:
        log.warning("could not read used long-video subjects: %s", e)
        return set()


def record(platform, key, post_id, url):
    short_publish.record_published(platform, key, post_id, url)


def done_today(day):
    if not supa.configured():
        return False
    try:
        return bool(supa.select("published_posts", platform="eq.long_day", topic_id=f"eq.{day}"))
    except Exception:
        return False


def dispatch():
    """Starts the `long` stage as its own workflow run (its own queue and time limit)."""
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        return False
    request(f"https://api.github.com/repos/{repo}/actions/workflows/pipeline.yml/dispatches", method="POST",
            data=json.dumps({"ref": os.environ.get("GITHUB_REF_NAME", "main"), "inputs": {"stage": "long"}}).encode(),
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    return True
