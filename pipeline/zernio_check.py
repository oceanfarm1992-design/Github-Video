"""Read-only Zernio diagnostic: connected accounts + comment automations. Prints no secrets."""
import json
import logging
import time

from . import zernio

log = logging.getLogger("zernio_check")


def _short(d, keys):
    return {k: d.get(k) for k in keys if k in d}


def run(conn=None):
    if not zernio.enabled():
        return "ZERNIO_API_KEY not set"
    out = {}
    try:
        data = zernio._call("/accounts")
        accts = data.get("accounts", data) if isinstance(data, dict) else data
        out["accounts"] = [_short(a, ("_id", "platform", "username", "isActive", "enabled", "permissions",
                                      "messagingRestriction", "platformStatus", "platformStatusReason",
                                      "needsReconnection", "lastRateLimitError")) for a in accts]
    except Exception as e:
        out["accounts_error"] = str(e)[:200]
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
    for line in json.dumps(out, indent=1).splitlines():
        log.info(line)
    return {k: (len(v) if isinstance(v, list) else v) for k, v in out.items()}
