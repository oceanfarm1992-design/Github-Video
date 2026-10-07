"""PROMO series: one video a day promoting one tool of the owner's site, Privacy PDF Tools.

Tools are discovered from the homepage (/tool/... links), so new tools join the rotation automatically.
Each video is a guided tour of the tool's own page: tagline, About, How to use, plus the page's own
privacy line. Words come only from the site itself. Rotation: tools never shown first, then the one
shown longest ago.
"""
import logging
import re
import time
from pathlib import Path

from . import config, db
from .generate import clean_claim
from .http import page_description, request
from .tools import shorten, shows_in_browser

log = logging.getLogger("promo")
SITE = "https://privacypdftools.com"
BRAND = "Privacy PDF Tools"
# legitimate for the user's own files, but "remove a PDF password" videos can read as cracking to platform
# moderation; kept out of the automatic rotation (add back by removing them here)
EXCLUDE = {"unlock-pdf", "remove-password"}
SECTION_ORDER = ("about", "how to use")


def discover():
    """[(slug, url)] of the site's tools, in homepage order."""
    try:
        _, _, body = request(SITE + "/", attempts=2)
        slugs = re.findall(r'href=["\']/tool/([a-z0-9-]+)', body.decode("utf-8", "replace"))
    except RuntimeError as e:
        log.warning("could not list tools: %s", e)
        return []
    seen, out = set(), []
    for s in slugs:
        if s not in seen and s not in EXCLUDE:
            seen.add(s)
            out.append((s, f"{SITE}/tool/{s}"))
    return out


def next_tool(conn, tools):
    """Never-shown tools first (homepage order), then the least recently shown."""
    last = {}
    for r in conn.execute("SELECT source, MAX(updated_at) t FROM topics WHERE source LIKE 'promo:%' GROUP BY source"):
        last[r["source"].split(":", 1)[1]] = r["t"] or 0
    fresh = [t for t in tools if t[0] not in last]
    if fresh:
        return fresh[0]
    return min(tools, key=lambda t: last.get(t[0], 0)) if tools else None


def tour(name, url, description, sections):
    """Hook + narrated beats with the page box each beat spotlights (all words from the site)."""
    action = description.split(": ", 1)[-1].split(". ")[0]
    action = re.split(r"\s+[—-]\s+|, in the order", action)[0].strip().rstrip(".")
    hook = (f"Need to {action[0].lower() + action[1:]}, without uploading your files?"
            if 8 < len(action) < 90 else f"Need a free {name} tool that never uploads your files?")
    beats = [{"text": f"Meet {name}, on {BRAND}.", "box": sections[0]["box"] if sections else None}]
    for key in SECTION_ORDER:
        s = next((x for x in sections if x["heading"].lower().startswith(key)), None)
        if s and s.get("text"):
            first = clean_claim(s["text"].split(". ")[0], limit=300)
            if first:
                beats.append({"text": shorten(first, 130), "box": s["box"]})
    privacy = re.search(r"(Runs 100% in your browser[^.]*\.)\s*(No [^.]*\.)?", description)
    if privacy:
        beats.append({"text": clean_claim(" ".join(p for p in privacy.groups() if p), limit=200), "box": None})
    return hook, beats


def build(conn):
    """Queue today's tool video. Returns the topic id or None."""
    from . import browser
    tools = discover()
    for _ in range(len(tools)):
        pick = next_tool(conn, tools)
        if not pick:
            return None
        slug, url = pick
        tools = [t for t in tools if t[0] != slug]
        desc = page_description(url) or ""
        page = browser.capture(url, Path(config.OUT_DIR) / "assets", any_site=True, max_css_height=2600) \
            if shows_in_browser(url) else None
        sections = browser.sections_for(page) if page else []
        name = (sections[0]["heading"] if sections else slug.replace("-", " ").title()).strip()
        if not desc or not page:
            log.info("promo tool %s skipped (no description or page capture)", slug)
            continue
        hook, beats = tour(name, url, desc, sections)
        if len(beats) < 3:
            log.info("promo tool %s: too little page text, skipped", slug)
            continue
        tid = db.sha(f"promo:{slug}:{db.today()}")[:16]
        claims = [{"text": b["text"], "source": url} for b in beats]
        conn.execute(
            """INSERT OR IGNORE INTO topics (id,title,source,url,published_at,discovered_at,content_hash,raw,summary,
            claims,source_urls,score,confidence,status,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (tid, f"{name} - {BRAND}", f"promo:{slug}", url, db.now_iso(), db.now_iso(),
             db.sha(f"promo:{slug}:{db.today()}"),
             db.js({"brand": BRAND, "tool": {"name": name, "url": url, "description": desc},
                    "tour": {"hook": hook, "beats": beats}}),
             desc, db.js(claims), db.js([url]), 95, 100, "QUEUED", time.time()))
        return tid
    return None


def run(conn):
    """Queue the next promo video if none is waiting and the daily quota is on."""
    if config.DAILY_PROMO_VIDEOS <= 0:
        return 0
    waiting = conn.execute(
        "SELECT COUNT(*) FROM topics WHERE source LIKE 'promo:%' AND status IN "
        "('QUEUED','GENERATING','RENDERING','QC','READY','RETRY')").fetchone()[0]
    if waiting:
        return 0
    return 1 if build(conn) else 0
