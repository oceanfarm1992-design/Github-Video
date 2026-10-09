"""Zernio adapter: publishes Facebook/Instagram Reels and sets up keyword -> DM comment automations.

Zernio is a third-party aggregator that calls the platforms' official APIs on our behalf
(https://docs.zernio.com). Used instead of direct Meta Graph credentials when ZERNIO_API_KEY is set.
"""
import json
import logging
import re
import os

from .http import request

log = logging.getLogger("zernio")
BASE = "https://zernio.com/api/v1"


def enabled():
    return bool(os.environ.get("ZERNIO_API_KEY"))


def _call(path, body=None, method=None, timeout=None, idempotency_key=None):
    h = {"Authorization": f"Bearer {os.environ['ZERNIO_API_KEY']}", "Content-Type": "application/json"}
    if idempotency_key:  # a retried request replays the first response instead of posting twice
        h["Idempotency-Key"] = idempotency_key
    _, _, r = request(BASE + path, headers=h, method=method or ("POST" if body is not None else "GET"),
                      data=json.dumps(body).encode() if body is not None else None, attempts=2, timeout=timeout)
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
    from .publish import social_caption  # local import: publish imports this module
    """Returns (platform_post_id, url). Raises on failure."""
    acct = account(platform)
    data = {"shareToFeed": True} if platform == "instagram" else {"contentType": "reel", "title": c["title"][:100]}
    body = {
        "content": social_caption(c),
        "mediaItems": [{"type": "video", "url": video_url}],
        "platforms": [{"platform": platform, "accountId": acct["_id"], "platformSpecificData": data}],
        "publishNow": True,
    }
    import uuid
    key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"post|{acct['_id']}|{video_url}"))
    try:
        # publishNow uploads the video before answering: allow 3 minutes (a 20 s timeout plus an automatic
        # retry once created a duplicate request that Zernio rejected with 409)
        post = _call("/posts", body, timeout=180, idempotency_key=key).get("post", {})
    except RuntimeError as e:
        existing = re.search(r'"existingPostId"\s*:\s*"([^"]+)"', str(e))
        if "409" not in str(e) or not existing:
            raise
        # Zernio: this exact content is already on this account (an earlier attempt succeeded) -> record it
        log.info("%s: already posted as %s; recording it instead of failing", platform, existing.group(1))
        try:
            create_automation(platform, acct, None, existing.group(1), c)
        except Exception as ae:
            log.warning("comment automation for %s failed: %s", existing.group(1), ae)
        return existing.group(1), ""
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


def publish_story(platform, c, video_url):
    """The same video as a Story (24 h, no caption shown; viewers reply by DM). Returns (post_id, url)."""
    acct = account(platform)
    body = {"mediaItems": [{"type": "video", "url": video_url}],
            "platforms": [{"platform": platform, "accountId": acct["_id"],
                           "platformSpecificData": {"contentType": "story"}}],
            "publishNow": True}
    import uuid
    key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"story|{acct['_id']}|{video_url}"))
    try:
        post = _call("/posts", body, timeout=180, idempotency_key=key).get("post", {})
    except RuntimeError as e:
        existing = re.search(r'"existingPostId"\s*:\s*"([^"]+)"', str(e))
        if "409" not in str(e) or not existing:
            raise
        return existing.group(1), ""
    plat = next((p for p in post.get("platforms", []) if p.get("platform") == platform), {})
    if plat.get("status") in ("failed", "error", "rejected") or (plat.get("errorMessage") and plat.get("status") != "published"):
        raise RuntimeError(f"zernio {platform} story: {plat.get('errorMessage') or plat.get('status')}")
    pid = post.get("_id") or _first(plat, "platformPostId")
    if not pid:
        raise RuntimeError(f"zernio {platform} story: no post id in response")
    return pid, plat.get("platformPostUrl", "")


PUBLIC_REPLY = "Check your inbox, thank you!"  # public reply under the keyword comment; the DM carries the link


def _profile_id(acct):
    """Zernio returns profileId as an object {_id, name}; the automation API wants the id string."""
    p = acct.get("profileId")
    return p.get("_id") if isinstance(p, dict) else p


FIRST_NAME_TOKEN = "{{first_name}}"  # Zernio fills this from the commenter (Facebook provides real names)


def first_name(display_name):
    """A real first name from a commenter's display name, or None for usernames like 'ai_fan_92'."""
    name = (display_name or "").strip()
    first = name.split()[0] if name else ""
    if not re.fullmatch(r"[A-Za-zÀ-ɏ'\-]{2,20}", first):
        return None
    if " " not in name and not first[0].isupper():  # single lowercase word: almost always a username
        return None
    return first[0].upper() + first[1:]


def dm_messages(title, response, name=None):
    """Conversational DM wordings (first = main, rest = rotation). A bare, identical link message from a
    Page someone never chatted with is what Messenger files as spam; a greeting with their name, context
    and an invitation to reply look like a conversation, and a reply moves the thread to the main inbox.
    name: the commenter's first name, Zernio's {{first_name}} token, or None (no name in the greeting)."""
    short = title.split("/")[-1] if "/" in title and " " not in title else title
    short = short if len(short) <= 60 else short[:57].rsplit(" ", 1)[0] + "..."
    if response.startswith("Here's the link: "):
        body = "here's the link you asked for:\n" + response.split("Here's the link: ", 1)[1]
    else:  # tools video: numbered list of links
        body = "here are the links you asked for:\n" + response.split(":\n", 1)[-1]
    if name:
        hi, hey, thanks = f"Hi {name}!", f"Hey {name},", f"Thanks for your comment, {name}!"
    else:
        hi, hey, thanks = "Hi!", "Hey,", "Thanks for your comment!"
    return [
        f"{hi} Thanks for commenting on our video about {short} \U0001F64C\n\nAs promised, {body}\n\n"
        "Any questions? Just reply here, I read every message.",
        f"{hey} thanks for watching our {short} video!\n\n{body[0].upper() + body[1:]}\n\n"
        "Reply and tell me what you think of it.",
        f"{thanks} \U0001F60A\n\nFor the {short} video, {body}\n\n"
        "Want more like this? Reply YES and I'll keep you posted.",
    ]


def create_automation(platform, acct, platform_post_id, zernio_post_id, c):
    """Scope to the live platform post if known, else to the Zernio post id (pending posts)."""
    cta = c.get("_cta")
    if not cta or not (platform_post_id or zernio_post_id):
        return
    scope = {"platformPostId": platform_post_id} if platform_post_id else {"postId": zernio_post_id}
    # Facebook gives commenters' real names; Instagram often only a username, so no name there
    msgs = dm_messages(c["title"], cta["response"], FIRST_NAME_TOKEN if platform == "facebook" else None)
    _call("/comment-automations", {**scope,
        "profileId": _profile_id(acct),
        "accountId": acct["_id"],
        "name": f"{platform}:{c['title'][:40]}:{cta['keyword']}",
        "keywords": [cta["keyword"]],
        "matchMode": "word",
        "dmMessage": msgs[0],
        "dmMessageVariations": msgs[1:],
        "commentReply": PUBLIC_REPLY,
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


_platform_ids = None


def platform_post_id(zernio_post_id):
    """Instagram media / Facebook post id for a Zernio post (the private-reply endpoint needs it).
    Read from our comment automations, which carry both ids; falls back to the Zernio id."""
    global _platform_ids
    if _platform_ids is None:
        try:
            data = _call("/comment-automations")
            items = data.get("automations", data.get("data", data)) if isinstance(data, dict) else data
            _platform_ids = {a.get("postId"): a.get("platformPostId") for a in items if a.get("postId")}
        except Exception as e:
            log.warning("could not map platform post ids: %s", e)
            _platform_ids = {}
    return _platform_ids.get(zernio_post_id) or zernio_post_id


def private_reply(zernio_post_id, platform, comment_id, text):
    """Send the commenter a DM (Meta private reply). Meta allows ONE per comment, within 7 days.
    Returns "sent", or "already" when that comment's private reply was used (e.g. by the automation)."""
    acct = account(platform)
    pid = platform_post_id(zernio_post_id)
    try:
        _call(f"/inbox/comments/{pid}/{comment_id}/private-reply", {"accountId": acct["_id"], "message": text[:1000]})
        return "sent"
    except RuntimeError as e:
        msg = str(e)
        if "privateReplyConsumed" in msg or "already been sent" in msg or "only allows one private reply" in msg:
            return "already"
        raise
