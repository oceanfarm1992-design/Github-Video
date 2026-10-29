"""LONG VIDEOS: 8-15 minute 16:9 explainers for YouTube and Facebook, one every LONG_EVERY_DAYS days (5).

The formats rotate (LONG_FORMATS): a GitHub repo deep dive, an AI tools full review, a websites full tour,
counted from LONG_START. With LONG_EVERY_DAYS=0 the old weekday schedule (LONG_SCHEDULE) applies, and on those
days one Short of the same series skips YouTube and Facebook. The produce run starts the `long` stage as a separate workflow run
(own queue, longer time limit); it collects real material, has Claude write a grounded chaptered script,
renders, publishes and records the subject in Supabase.
"""
import datetime
import logging
import time
from pathlib import Path

from .. import config, db
from ..theme import kind as series_kind

log = logging.getLogger("longform")
FORMATS = ("github", "tools", "sites")


def schedule():
    """{weekday: format} from LONG_SCHEDULE ("0:github,2:tools,4:sites"; empty = off)."""
    out = {}
    for part in (config.LONG_SCHEDULE or "").split(","):
        day, _, fmt = part.strip().partition(":")
        if day.strip().isdigit() and fmt.strip() in FORMATS:
            out[int(day)] = fmt.strip()
    return out


def format_today(today=None):
    """Today's long-video format, or None. today: a datetime.date (default: today, UTC)."""
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    if config.LONG_EVERY_DAYS <= 0:
        return schedule().get(today.weekday())
    formats = [f.strip() for f in config.LONG_FORMATS.split(",") if f.strip() in FORMATS]
    try:
        n = (today - datetime.date.fromisoformat(config.LONG_START)).days
    except ValueError:
        return None
    if not formats or n < 0 or n % config.LONG_EVERY_DAYS:
        return None
    return formats[(n // config.LONG_EVERY_DAYS) % len(formats)]


def skip_short(conn, c):
    """True for the first Short of today's long-video series: it skips YouTube and Facebook."""
    fmt = format_today()
    if config.LONG_EVERY_DAYS > 0 or not fmt or series_kind(c.get("tsource") or "") != fmt:
        return False
    key = f"long-skip:{db.today()}"
    row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    if row:
        return row["value"] == str(c["id"])
    conn.execute("INSERT INTO kv (key, value) VALUES (?,?)", (key, str(c["id"])))
    return True


def dispatch_if_due(conn):
    """Called by the produce run: starts today's long-video run once."""
    from . import publish
    if not format_today():
        return 0
    key = f"long-dispatched:{db.today()}"
    if conn.execute("SELECT 1 FROM kv WHERE key=?", (key,)).fetchone():
        return 0
    try:
        if publish.dispatch():
            conn.execute("INSERT INTO kv (key, value) VALUES (?,?)", (key, str(time.time())))
            return 1
    except Exception as e:
        log.warning("could not start the long-video run: %s", e)
    return 0


def run(conn, fmt=None, publish_it=True):
    """Makes (and publishes) today's long video. fmt overrides the schedule (previews)."""
    from . import material, publish, render, writer
    fmt = fmt or format_today()
    if not fmt:
        return {"skipped": "not a long-video day"}
    if publish_it and publish.done_today(db.today()):
        return {"skipped": "already published today"}
    used = publish.used_keys()
    m = material.collect(conn, fmt, used)
    if not m:
        return {"skipped": f"no {fmt} subject with enough real material"}
    script = writer.write(m)
    if not script:
        return {"skipped": "no valid script"}
    out = Path(config.OUT_DIR) / "video"
    out.mkdir(parents=True, exist_ok=True)
    mp4, chapters, thumb = render.render(m, script, out)
    result = {"format": fmt, "subject": m["subject"], "video": str(mp4), "chapters": len(chapters),
              "llm_usd": script.get("cost_usd")}
    if not publish_it:
        return result
    for platform, fn in (("youtube_long", lambda: publish.youtube(conn, m, script, mp4, chapters, thumb)),
                         ("facebook_long", lambda: publish.facebook(m, script, mp4, chapters))):
        try:
            res = fn()
            if res:
                publish.record(platform, m["key"], res[0], res[1])
                result[platform] = res[1] or res[0]
        except Exception as e:
            log.warning("long video publish to %s failed: %s", platform, e)
            result[platform] = f"failed: {str(e)[:200]}"
    if any(k in result and not str(result[k]).startswith("failed") for k in ("youtube_long", "facebook_long")):
        for key in m.get("site_keys", []):
            publish.record("long_subject", key, "-", "")
        publish.record("long_day", db.today(), m["key"], "")
    return result
