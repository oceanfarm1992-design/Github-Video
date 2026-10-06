"""Zernio adapter: publishes Facebook/Instagram Reels and sets up keyword -> DM comment automations.

Zernio is a third-party aggregator that calls the platforms' official APIs on our behalf
(https://docs.zernio.com). Used instead of direct Meta Graph credentials when ZERNIO_API_KEY is set.
"""
import json
import logging
import os

from .http import request

log = logging.getLogger("zernio")
BASE = "https://zernio.com/api/v1"


def enabled():
    return bool(os.environ.get("ZERNIO_API_KEY"))


def _call(path, body=None, method=None):
    h = {"Authorization": f"Bearer {os.environ['ZERNIO_API_KEY']}", "Content-Type": "application/json"}
    _, _, r = request(BASE + path, headers=h, method=method or ("POST" if body is not None else "GET"),
                      data=json.dumps(body).encode() if body is not None else None, attempts=2)
    return json.loads(r or b"{}")


_accounts = None


def account(platform):
    """First connected account for the platform (override with ZERNIO_<PLATFORM>_ACCOUNT_ID)."""
    global _accounts
    override = os.environ.get(f"ZERNIO_{platform.upper()}_ACCOUNT_ID")
    if _accounts is None:
        data = _call("/accounts")
        _accounts = data.get("accounts", data) if isinstance(data, dict) else data
    for a in _accounts:
        if a.get("platform") == platform and (not override or a.get("_id") == override):
            return a
    return None


def available(platform):
    try:
        return enabled() and account(platform) is not None
    except Exception as e:
        log.warning("zernio accounts lookup failed: %s", e)
        return False


def _first(d, *keys):
    for k in keys:
        if d.get(k):
            return d[k]
    return None


def publish_reel(platform, c, video_url):
    """Returns (platform_post_id, url). Raises on failure."""
    acct = account(platform)
    data = {"shareToFeed": True} if platform == "instagram" else {"contentType": "reel", "title": c["title"][:100]}
    body = {
        "content": c["caption"] + "\n" + " ".join(json.loads(c["hashtags"])),
        "mediaItems": [{"type": "video", "url": video_url}],
        "platforms": [{"platform": platform, "accountId": acct["_id"], "platformSpecificData": data}],
        "publishNow": True,
    }
    post = _call("/posts", body).get("post", {})
    plat = next((p for p in post.get("platforms", []) if p.get("platform") == platform), {})
    if plat.get("status") not in (None, "published", "publishing", "pending"):
        raise RuntimeError(f"zernio {platform}: {plat.get('errorMessage') or plat.get('status')}")
    pid = _first(plat, "platformPostId") or post.get("_id")
    try:
        create_automation(platform, acct, pid, c)
    except Exception as e:  # posting succeeded; automation can be re-created in the Zernio dashboard
        log.warning("comment automation for %s failed: %s", pid, e)
    return pid, plat.get("platformPostUrl", "")


def create_automation(platform, acct, platform_post_id, c):
    cta = c.get("_cta")
    if not cta or not platform_post_id:
        return
    _call("/comment-automations", {
        "profileId": _first(acct, "profileId") or (acct.get("profile") or {}).get("_id") or acct.get("profile"),
        "accountId": acct["_id"],
        "name": f"{platform}:{c['title'][:40]}:{cta['keyword']}",
        "platformPostId": platform_post_id,
        "keywords": [cta["keyword"]],
        "matchMode": "word",
        "dmMessage": cta["response"],
        "commentReply": "Sent you a DM with the link.",
    })
