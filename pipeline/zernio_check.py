"""Read-only Zernio diagnostic: connected accounts + comment automations. Prints no secrets."""
import json
import logging
import time

from . import zernio

log = logging.getLogger("zernio_check")


def _short(d, keys):
    return {k: d.get(k) for k in keys if k in d}


def youtube_channels():
    """Which YouTube channel each stored token posts to (1 quota unit each), so a token made for the wrong
    channel is caught before anything is uploaded."""
    import json
    from . import channels, publish
    from .http import request
    out = {}
    for ch in ["main", *channels.CHANNELS]:
        try:
            tok = publish.youtube_token(ch) if publish.youtube_token_available(ch) else None
            if not tok:
                out[ch] = "no token"
                continue
            _, _, r = request("https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
                              headers={"Authorization": f"Bearer {tok}"})
            items = json.loads(r).get("items", [])
            out[ch] = {"id": items[0]["id"], "title": items[0]["snippet"]["title"]} if items else "no channel"
        except Exception as e:
            out[ch] = f"error: {str(e)[:150]}"
    ids = [v["id"] for v in out.values() if isinstance(v, dict)]
    if len(ids) != len(set(ids)):
        out["WARNING"] = "two channels share one YouTube channel: re-run youtube_auth.py and pick the right one"
    return out


def free_models():
    """Each free model answers a tiny JSON request (no personal data)."""
    from . import gemini, groq
    out = {}
    probe = 'Answer with JSON only: {"ok": true}'
    if gemini.gemini_available():
        try:
            out["gemini"] = {"models": gemini.models(), "answer": gemini._gemini_json(probe, None, 50)}
        except Exception as e:
            out["gemini"] = f"error: {str(e)[:150]}"
    if groq.available():
        try:
            out["groq"] = {"models": groq.models(), "answer": groq.generate_json(probe, None, 50)}
        except Exception as e:
            out["groq"] = f"error: {str(e)[:150]}"
    return out


def run(conn=None):
    out = {}
    if not zernio.enabled():
        out["zernio"] = "ZERNIO_API_KEY not set"
    try:
        data = zernio._call("/accounts")
        accts = data.get("accounts", data) if isinstance(data, dict) else data
        out["accounts"] = [_short(a, ("_id", "platform", "username", "isActive", "enabled", "permissions",
                                      "messagingRestriction", "platformStatus", "platformStatusReason",
                                      "needsReconnection", "lastRateLimitError")) for a in accts]
    except Exception as e:
        out["accounts_error"] = str(e)[:200]
    import os
    out["youtube_channels"] = youtube_channels()
    out["free_models"] = free_models()
    if os.environ.get(zernio.SISTER_KEY):  # second workspace: the sister channels' accounts
        try:
            data = zernio._call("/accounts", api=zernio.SISTER_KEY)
            accts = data.get("accounts", data) if isinstance(data, dict) else data
            out["sister_accounts"] = [_short(a, ("_id", "platform", "username", "isActive", "platformStatus",
                                                 "needsReconnection")) for a in accts]
        except Exception as e:
            out["sister_accounts_error"] = str(e)[:200]
    try:
        data = zernio._call("/comment-automations")
        items = data.get("automations", data.get("data", data)) if isinstance(data, dict) else data
        out["automations"] = []
        for a in items:
            row = _short(a, ("name", "platform", "postId", "platformPostId", "keywords", "isActive", "stats"))
            # comments on the post: counts and flags only (comment text and names are personal data and
            # Actions logs are public)
            try:
                cs = list(zernio.list_comments(a.get("postId"), a.get("platform")))
                kw = (a.get("keywords") or [""])[0]
                row["comments"] = {"total": len(cs), "from_page_owner": sum(c["is_owner"] for c in cs),
                                   "with_keyword": sum(bool(kw) and kw.lower() in (c["body"] or "").lower()
                                                       for c in cs)}
            except Exception as e:
                row["comments_error"] = str(e)[:300]
            out["automations"].append(row)
    except Exception as e:
        out["automations_error"] = str(e)[:300]
    # outgoing DMs in the last 24 h per account: times and delivery status only (no names, no text)
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 86400))
    out["dms_last_24h"] = {}
    for a in out.get("accounts", []):
        rows = []
        try:
            for folder in ("inbox", "requests"):
                data = zernio._call(f"/inbox/conversations?accountId={a['_id']}&folder={folder}&limit=20")
                convs = data.get("conversations", data.get("data", data)) if isinstance(data, dict) else data
                for cv in convs or []:
                    if (cv.get("updatedTime") or "") < since:
                        continue
                    m = zernio._call(f"/inbox/conversations/{cv['id']}/messages?accountId={a['_id']}")
                    msgs = m.get("messages", m.get("data", m)) if isinstance(m, dict) else m
                    for msg in msgs or []:
                        if msg.get("direction") == "outgoing" and (msg.get("createdAt") or "") >= since:
                            rows.append({"folder": folder, "at": msg.get("createdAt"), "status": msg.get("status")})
        except Exception as e:
            rows.append({"error": str(e)[:300]})
        out["dms_last_24h"][a.get("platform")] = rows
    # Buffer (TikTok, Pinterest): connected channels and Pinterest boards (names only, no secrets)
    from . import buffer
    if buffer.enabled():
        try:
            out["buffer_channels"] = [{"service": ch.get("service"), "name": ch.get("name"),
                                       "boards": [b.get("name") for b in ch.get("boards") or []]}
                                      for ch in buffer.channels()]
        except Exception as e:
            out["buffer_error"] = str(e)[:300]
        try:  # schema check: per-network post metadata inputs (type names only)
            meta = buffer._gql("""query { t: __type(name: "CreatePostInput") {
                inputFields { name type { name kind ofType { name } } } } }""")
            mt = next((f["type"] for f in (meta.get("t") or {}).get("inputFields", []) if f["name"] == "metadata"), {})
            mname = mt.get("name") or (mt.get("ofType") or {}).get("name")
            fields = buffer._gql("""query($n: String!) { t: __type(name: $n) {
                inputFields { name type { name kind ofType { name } } } } }""", {"n": mname})
            out["buffer_post_metadata"] = {f["name"]: f["type"].get("name") or (f["type"].get("ofType") or {}).get("name")
                                           for f in (fields.get("t") or {}).get("inputFields", [])}
            li = out["buffer_post_metadata"].get("linkedin")
            if li:
                lf = buffer._gql("""query($n: String!) { t: __type(name: $n) {
                    inputFields { name type { name kind ofType { name kind } } } } }""", {"n": li})
                out["buffer_linkedin_input"] = [(f["name"], f["type"].get("kind"), f["type"].get("name")
                                                 or (f["type"].get("ofType") or {}).get("name"))
                                                for f in (lf.get("t") or {}).get("inputFields", [])]
        except Exception as e:
            out["buffer_schema_error"] = str(e)[:300]
    for line in json.dumps(out, indent=1).splitlines():
        log.info(line)
    return {k: (len(v) if isinstance(v, list) else v) for k, v in out.items()}
