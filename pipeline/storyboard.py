"""Per-series storyboard: the hook frame, the end card, which scene gets which data visual, and logos.

Every word on these cards is either fixed series wording or a fact taken from the topic / GitHub API
(stars, repo age, part number, tool count). Promises ("every day", "tomorrow") are shown only when the
configured schedule actually delivers them.
"""
import json
import logging
import time

from . import config, ghdata
from .visuals import short_num

log = logging.getLogger("storyboard")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _raw(topic):
    try:
        return json.loads(topic["raw"] or "{}") if topic else {}
    except (KeyError, IndexError, TypeError, ValueError):
        return {}


def _age_days(iso):
    try:
        return max(1, int((time.time() - ghdata._ts(iso)) // 86400))
    except Exception:
        return None


def intro(kind, topic, title, n_items=None, gh=None):
    """Hook frame text: {"tag", "title", "sub"}."""
    raw = _raw(topic)
    if kind == "github":
        info = gh or {}
        stars = info.get("stars", raw.get("stars"))
        days = _age_days(info.get("created_at") or raw.get("created_at"))
        lang = info.get("language") or raw.get("language")
        tag = f"OPEN SOURCE · {lang.upper()}" if lang and len(lang) <= 12 else "OPEN SOURCE"
        if stars and days and days <= 60:
            head = f"{short_num(stars)} stars in {days} day{'s' if days != 1 else ''}"
        elif stars:
            head = f"{short_num(stars)} stars on GitHub"
        else:
            head = title.split("/")[-1]
        return {"tag": tag, "title": head, "sub": title}
    if kind == "tools":
        return {"title": f"{n_items} AI tools for {raw.get('phrase', 'your work')}", "sub": None}
    if kind == "sites":
        items = raw.get("tools") or [{}]
        name = items[0].get("name") if len(items) == 1 else None
        return {"title": "Websites that feel illegal to know",
                "sub": f"Part {raw.get('part', 1)}" + (f" · {name}" if name else "")}
    if kind == "promo":
        return {"title": raw.get("tool", {}).get("name", title), "sub": raw.get("brand")}
    if kind == "ranking":
        return {"title": f"Top {n_items} new GitHub repos this week", "sub": "Ranked by stars"}
    return {"title": title[:70], "sub": None}


def endcard(kind, topic, n_items=None):
    """End card: comment poll + what comes next (only promises the schedule keeps)."""
    raw = _raw(topic)
    if kind == "tools":
        return {"question": "Which one would you use?", "options": [str(i) for i in range(1, (n_items or 3) + 1)],
                "line": "More AI tools every day" if config.DAILY_TOOLS_VIDEOS >= 1 else None}
    if kind == "sites":
        return {"question": "Did you know this website?", "options": ["YES", "NO"],
                "line": f"Part {raw.get('part', 1) + 1} tomorrow" if config.DAILY_SITES_VIDEOS >= 1 else None}
    if kind == "promo":
        return {"question": "Would you use it?", "options": ["YES", "NO"],
                "line": "A new free PDF tool every day" if config.DAILY_PROMO_VIDEOS >= 1 else None}
    if kind == "ranking":
        return {"question": "Which one is your #1?", "options": [str(i) for i in range(1, (n_items or 5) + 1)],
                "line": f"New top {n_items or 5} every {WEEKDAYS[config.RANKING_WEEKDAY]}"
                if 0 <= config.RANKING_WEEKDAY <= 6 else None}
    if kind == "github":
        return {"question": "Would you try it?", "options": ["YES", "NO"],
                "line": "New open-source finds every day" if config.DAILY_GITHUB_VIDEOS >= 1 else None}
    return {"question": "Would you use this?", "options": ["YES", "NO"], "line": None}


def github_visuals(n_scenes, gh):
    """Data visuals for a GitHub video's middle scenes: scene 1 keeps the repo page; then the star
    growth + stats, then the README's install command; the rest keep the page."""
    out = [None] * n_scenes
    middle = list(range(2, n_scenes - 1))
    if gh and middle:
        out[middle.pop(0)] = ("stats", gh)
    if gh and gh.get("install") and middle:
        out[middle.pop(0)] = ("terminal", gh["install"])
    return out


def tool_logos(tools, out_dir, front=1, back=1):
    """Per scene: the tool's own icon (scene list = hook + one per tool + outro)."""
    return [None] * front + [ghdata.logo(t["url"], out_dir) for t in tools] + [None] * back


def tool_compares(tools, out_dir, front=1, back=1):
    """Before/after only where the catalog lists the tool's own example images (never invented)."""
    out = [None] * front
    for i, t in enumerate(tools):
        pair = t.get("compare")
        if pair and len(pair) == 2:
            b = ghdata.fetch_image(pair[0], f"{out_dir}/cmp_{i}_a.png", 200)
            a = ghdata.fetch_image(pair[1], f"{out_dir}/cmp_{i}_b.png", 200)
            out.append(("compare", (b, a)) if a and b else None)
        else:
            out.append(None)
    return out + [None] * back


def ranking_media(items, out_dir):
    """Owner avatars (badges) for each ranked repo and the podium data for the top 3."""
    avatars = []
    for it in items:
        owner = it["name"].split("/")[0]
        avatars.append(ghdata.fetch_image(f"https://github.com/{owner}.png?size=256",
                                          f"{out_dir}/avatar_{owner}.png") if owner else None)
    ranked = sorted(zip(items, avatars), key=lambda p: p[0].get("rank", 99))[:3]
    podium = [{"name": it["name"], "stars": it.get("stars"), "avatar": av} for it, av in ranked]
    return avatars, podium
