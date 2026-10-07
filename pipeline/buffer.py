"""Buffer adapter (https://developers.buffer.com, GraphQL API): publishes the video to TikTok and Pinterest.

Needs BUFFER_API_KEY (Actions secret). Channels are discovered from the Buffer account; Pinterest pins go
to BUFFER_PINTEREST_BOARD (board name or id) or else the first board. TikTok posts are marked
isAiGenerated (cloned voice + AI-written text: TikTok requires the label).
"""
import json
import logging
import os
import re

from .http import request

log = logging.getLogger("buffer")
API = "https://api.buffer.com"
SERVICES = ("tiktok", "pinterest")


def enabled():
    return bool(os.environ.get("BUFFER_API_KEY"))


def _gql(query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    _, _, r = request(API, data=body, method="POST", attempts=2, timeout=120, headers={
        "Authorization": f"Bearer {os.environ['BUFFER_API_KEY']}", "Content-Type": "application/json"})
    out = json.loads(r or b"{}")
    if out.get("errors"):
        raise RuntimeError("buffer: " + "; ".join(e.get("message", "?") for e in out["errors"])[:400])
    return out.get("data") or {}


_channels = None


def channels():
    """[{id, service, name, boards: [{serviceId, name}]}] for the account's first organization."""
    global _channels
    if _channels is None:
        orgs = _gql("query { account { organizations { id } } }")["account"]["organizations"]
        found = []
        for org in orgs:
            data = _gql("""query($org: OrganizationId!) { channels(input: {organizationId: $org}) {
                id service name
                metadata { ... on PinterestMetadata { boards { serviceId name } } } } }""", {"org": org["id"]})
            for ch in data.get("channels", []):
                ch["boards"] = ((ch.get("metadata") or {}).get("boards") or [])
                found.append(ch)
        _channels = found
    return _channels


def channel(service):
    return next((c for c in channels() if str(c.get("service", "")).lower() == service), None)


def available(service):
    """Channel connected (and, for Pinterest, at least one board to pin to)."""
    try:
        ch = channel(service) if enabled() else None
        if ch and service == "pinterest" and not ch.get("boards"):
            log.info("Pinterest has no boards yet: skipping Pinterest until one exists")
            return False
        return ch is not None
    except Exception as e:
        log.warning("buffer channel lookup failed: %s", e)
        return False


def board_id(ch):
    want = os.environ.get("BUFFER_PINTEREST_BOARD", "").strip().lower()
    boards = ch.get("boards") or []
    for b in boards:
        if want and want in (str(b.get("serviceId", "")).lower(), str(b.get("name", "")).lower()):
            return b["serviceId"]
    if not boards:
        raise RuntimeError("buffer: no Pinterest board found on the channel")
    return boards[0]["serviceId"]


def single_link(c):
    """The one destination link of a post (Pinterest), or None for multi-link lists."""
    resp = (c.get("_cta") or {}).get("response") or ""
    m = re.match(r"Here's the link: (\S+)", resp)
    return m.group(1) if m else None


CREATE = """mutation($input: CreatePostInput!) { createPost(input: $input) {
  ... on PostActionSuccess { post { id } }
  ... on MutationError { message } } }"""


def publish(service, c, video_url):
    """Returns (buffer_post_id, url). Raises on failure."""
    from .publish import social_caption, youtube_title
    ch = channel(service)
    title = youtube_title(c).replace(" #Shorts", "")[:100]
    meta = {"tiktok": {"isAiGenerated": True}} if service == "tiktok" else {
        "pinterest": {"boardServiceId": board_id(ch), "title": title,
                      **({"url": single_link(c)} if single_link(c) else {})}}
    base = {"channelId": ch["id"], "text": social_caption(c), "schedulingType": "automatic",
            "assets": [{"video": {"url": video_url, "metadata": {"thumbnailOffset": 1500}}}], "metadata": meta}
    try:
        data = _gql(CREATE, {"input": {**base, "mode": "shareNow"}})
    except RuntimeError as e:
        if "mode" not in str(e):
            raise
        data = _gql(CREATE, {"input": base})  # schema without a mode field: Buffer publishes from its queue
    res = data.get("createPost") or {}
    if res.get("message"):
        raise RuntimeError(f"buffer {service}: {res['message']}")
    post = res.get("post") or {}
    if not post.get("id"):
        raise RuntimeError(f"buffer {service}: no post id in response")
    return post["id"], ""
