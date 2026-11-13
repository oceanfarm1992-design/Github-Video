"""Sister channels: "AI for Kids" (for parents and teachers) and "AI for Normal People" (everyday AI).

Each channel has its own YouTube channel (its own refresh token) and its own Facebook / Instagram / TikTok
accounts in Zernio (CHANNEL_ACCOUNTS maps channel -> platform -> Zernio account id). A topic belongs to a
channel by its source prefix ("kids:<category>", "everyday:<category>", "long:kids"); everything else is the
main channel. The main channel never posts to an account mapped to a sister channel, and a sister channel
posts only to its own mapped accounts (an unmapped platform is skipped, never sent to the main account).

Schedule: one Short every SHORT_EVERY_DAYS days per channel (the two channels take turns, so one sister
Short a day), and one long video every LONG_EVERY_DAYS days, on days that do not clash with the main
channel's long video.
"""
import datetime
import json
import logging
import os
from dataclasses import dataclass

from . import config

log = logging.getLogger("channels")
MAIN = "main"


@dataclass(frozen=True)
class Channel:
    key: str            # source prefix and series kind
    name: str           # display name
    token_env: str      # GitHub secret with the channel's YouTube refresh token
    short_offset: int   # Short day: (days since CHANNELS_START) % SHORT_EVERY_DAYS == short_offset
    long_offset: int    # long day: (days since LONG_START) % LONG_EVERY_DAYS == long_offset (main uses 0)


CHANNELS = {
    "kids": Channel("kids", "AI for Kids", "YOUTUBE_REFRESH_TOKEN_KIDS", 0, 2),
    "everyday": Channel("everyday", "AI for Normal People", "YOUTUBE_REFRESH_TOKEN_EVERYDAY", 1, 4),
}


def of_source(source):
    """Channel key of a topic source: 'kids:art' -> kids, 'long:everyday' -> everyday, else main."""
    parts = str(source or "").split(":")
    if parts[0] in CHANNELS:
        return parts[0]
    if parts[0] == "long" and len(parts) > 1 and parts[1] in CHANNELS:
        return parts[1]
    return MAIN


def _days_since(start, today):
    try:
        return (today - datetime.date.fromisoformat(start)).days
    except ValueError:
        return -1


def _today(today=None):
    return today or datetime.datetime.now(datetime.timezone.utc).date()


def enabled():
    """Channels turned on (CHANNELS_ENABLED, comma list; empty = none)."""
    return [k.strip() for k in config.CHANNELS_ENABLED.split(",") if k.strip() in CHANNELS]


def short_due(key, today=None):
    """True on the days this channel gets a Short."""
    every = max(1, config.CHANNEL_SHORT_EVERY_DAYS)
    n = _days_since(config.CHANNELS_START, _today(today))
    return key in enabled() and n >= 0 and n % every == CHANNELS[key].short_offset % every


def long_due(today=None):
    """The sister channel whose long video is due today, or None."""
    every = config.CHANNEL_LONG_EVERY_DAYS
    today = _today(today)
    if every <= 0 or _days_since(config.CHANNELS_START, today) < 0:
        return None
    n = _days_since(config.LONG_START, today)
    for key in enabled():
        if n >= 0 and n % every == CHANNELS[key].long_offset % every:
            return key
    return None


# ------------------------------------------------------------------ accounts
def account_map():
    """{channel: {platform: zernio_account_id}} from CHANNEL_ACCOUNTS (a JSON repo variable)."""
    raw = os.environ.get("CHANNEL_ACCOUNTS", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        log.warning("CHANNEL_ACCOUNTS is not valid JSON; sister channels have no social accounts")
        return {}
    return {k: {p: str(i) for p, i in (v or {}).items() if i} for k, v in data.items()
            if (k in CHANNELS or k == MAIN) and isinstance(v, dict)}


def claimed_ids():
    """Zernio account ids/usernames that belong to a sister channel (the main channel never posts there)."""
    return {i.lower() for k, plats in account_map().items() if k != MAIN for i in plats.values()}


def account_id(channel, platform):
    """The Zernio account id (or username) mapped to this channel and platform, or None."""
    return account_map().get(channel, {}).get(platform)


def pick_account(accounts, platform, channel=MAIN, override=None):
    """The Zernio account a channel posts to on a platform, or None.
    Sister channels: only their mapped account. Main: its pinned account (CHANNEL_ACCOUNTS "main" or the
    ZERNIO_<PLATFORM>_ACCOUNT_ID override), else the first account no sister channel has claimed."""
    def ids(a):
        return {str(a.get("_id", "")).lower(), str(a.get("username", "")).lower()} - {""}
    same = [a for a in accounts if a.get("platform") == platform]
    want = account_id(channel, platform) or (override if channel == MAIN else None)
    if want:
        return next((a for a in same if want.lower() in ids(a)), None)
    if channel != MAIN:
        return None
    claimed = claimed_ids()
    return next((a for a in same if not ids(a) & claimed), None)


def watermark(key):
    """Channel name shown on the video: the sister channel's own name, else WATERMARK_TEXT (main)."""
    if key in CHANNELS:
        return CHANNELS[key].name
    return os.environ.get("WATERMARK_TEXT") or ""


def youtube_client(channel):
    """(client_id, client_secret) for the channel's YouTube token: its own OAuth client when stored
    (YOUTUBE_CLIENT_ID_KIDS ...), else the main channel's."""
    up = "_" + channel.upper() if channel in CHANNELS else ""
    own = (os.environ.get("YOUTUBE_CLIENT_ID" + up), os.environ.get("YOUTUBE_CLIENT_SECRET" + up))
    return own if up and all(own) else (os.environ.get("YOUTUBE_CLIENT_ID"), os.environ.get("YOUTUBE_CLIENT_SECRET"))


def youtube_refresh_token(channel):
    if channel == MAIN:
        return os.environ.get("YOUTUBE_REFRESH_TOKEN")
    return os.environ.get(CHANNELS[channel].token_env) if channel in CHANNELS else None
