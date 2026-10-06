"""Read-only Zernio diagnostic: connected accounts + comment automations. Prints no secrets."""
import json
import logging

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
        out["accounts"] = [_short(a, ("_id", "platform", "username", "name", "profileId", "isActive", "status")) |
                           {"fields": sorted(a.keys())} for a in accts]
    except Exception as e:
        out["accounts_error"] = str(e)[:200]
    try:
        data = zernio._call("/comment-automations")
        items = data.get("automations", data.get("data", data)) if isinstance(data, dict) else data
        out["automations"] = [_short(a, ("id", "name", "platform", "accountId", "platformPostId", "postId", "keywords",
                                         "matchMode", "isActive", "stats")) for a in items]
    except Exception as e:
        out["automations_error"] = str(e)[:200]
    for line in json.dumps(out, indent=1).splitlines():
        log.info(line)
    return {k: (len(v) if isinstance(v, list) else v) for k, v in out.items()}
