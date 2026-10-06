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
    # "processing"/"scheduled"/"publishing" are normal in-flight states; only explicit failures raise.
    if plat.get("status") in ("failed", "error", "rejected") or (plat.get("errorMessage") and plat.get("status") != "published"):
        raise RuntimeError(f"zernio {platform}: {plat.get('errorMessage') or plat.get('status')}")
    pid = post.get("_id") or _first(plat, "platformPostId")  # Zernio id: used to list/reply to comments
    if not pid:
        raise RuntimeError(f"zernio {platform}: no post id in response")
    try:
        create_automation(platform, acct, _first(plat, "platformPostId"), post.get("_id"), c)
    except Exception as e:  # posting succeeded; automation can be re-created in the Zernio dashboard
        log.warning("comment automation for %s failed: %s", pid, e)
    return pid, plat.get("platformPostUrl", "")


def create_automation(platform, acct, platform_post_id, zernio_post_id, c):
    """Scope to the live platform post if known, else to the Zernio post id (pending posts)."""
    cta = c.get("_cta")
    if not cta or not (platform_post_id or zernio_post_id):
        return
    scope = {"platformPostId": platform_post_id} if platform_post_id else {"postId": zernio_post_id}
    _call("/comment-automations", {**scope,
        "profileId": _first(acct, "profileId") or (acct.get("profile") or {}).get("_id") or acct.get("profile"),
        "accountId": acct["_id"],
        "name": f"{platform}:{c['title'][:40]}:{cta['keyword']}",
        "keywords": [cta["keyword"]],
        "matchMode": "word",
        "dmMessage": cta["response"],
        "commentReply": "Sent you a DM with the link.",
    })


# ------------------------------------------------------------- comments
def list_comments(zernio_post_id, platform):
    """Top-level comments on a Zernio post (cached by Zernio for up to 10 minutes)."""
    acct = account(platform)
    cursor = None
    for _ in range(5):  # at most 500 comments per post per run
        q = f"?accountId={acct['_id']}&limit=100" + (f"&cursor={cursor}" if cursor else "")
        data = _call(f"/inbox/comments/{zernio_post_id}{q}")
        d = data.get("data", data)
        items = d.get("comments", d.get("items", d if isinstance(d, list) else []))
        for c in items:
            frm = c.get("from") or c.get("author") or {}
            yield {"comment_id": c.get("id"), "author_id": frm.get("id"), "author_name": frm.get("name"),
                   "body": c.get("message"), "is_owner": bool(c.get("isOwner"))}
        cursor = d.get("nextCursor") if isinstance(d, dict) else None
        if not cursor or not (d.get("hasMore") if isinstance(d, dict) else False):
            break


def reply_comment(zernio_post_id, platform, comment_id, text):
    """Public reply to one comment. Idempotency-Key makes a retried call post only once."""
    import uuid
    acct = account(platform)
    key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{acct['_id']}|{zernio_post_id}|{comment_id}|{text}"))
    h = {"Authorization": f"Bearer {os.environ['ZERNIO_API_KEY']}", "Content-Type": "application/json",
         "Idempotency-Key": key}
    request(f"{BASE}/inbox/comments/{zernio_post_id}", headers=h, method="POST", attempts=2,
            data=json.dumps({"accountId": acct["_id"], "message": text, "commentId": comment_id}).encode())
