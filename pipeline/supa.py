"""Minimal Supabase (PostgREST) client over urllib. Needs SUPABASE_URL + SUPABASE_SERVICE_KEY."""
import json
import os
import urllib.parse

from .http import request


def _key():
    """New-style `sb_secret_...` key, or a legacy service_role JWT."""
    return os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_KEY") or ""


def configured():
    return bool(os.environ.get("SUPABASE_URL") and _key())


def _h(extra=None):
    k = _key()
    h = {"apikey": k, "Content-Type": "application/json", **(extra or {})}
    if k.startswith("eyJ"):  # legacy JWT keys go in Authorization too; sb_secret_ keys are apikey-only
        h["Authorization"] = f"Bearer {k}"
    return h


def _url(table, params=None):
    base = os.environ["SUPABASE_URL"].rstrip("/") + f"/rest/v1/{table}"
    return base + ("?" + urllib.parse.urlencode(params) if params else "")


def select(table, **filters):
    """filters use PostgREST syntax, e.g. platform='eq.youtube'."""
    _, _, r = request(_url(table, filters), headers=_h())
    return json.loads(r or b"[]")


def insert_once(table, row, on_conflict):
    """Insert; returns the row if new, None if the unique key already existed."""
    _, _, r = request(_url(table, {"on_conflict": on_conflict}), data=json.dumps(row).encode(), method="POST",
                      headers=_h({"Prefer": "resolution=ignore-duplicates,return=representation"}))
    rows = json.loads(r or b"[]")
    return rows[0] if rows else None


def upsert(table, row, on_conflict):
    request(_url(table, {"on_conflict": on_conflict}), data=json.dumps(row).encode(), method="POST",
            headers=_h({"Prefer": "resolution=merge-duplicates"}))


def update(table, row, **filters):
    request(_url(table, filters), data=json.dumps(row).encode(), method="PATCH", headers=_h())


def delete(table, **filters):
    request(_url(table, filters), method="DELETE", headers=_h())
