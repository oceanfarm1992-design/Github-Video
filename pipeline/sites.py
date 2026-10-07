"""SITES series: "Websites that feel illegal to know - Part N" (they are all legal).

Each part shows SITES_PER_VIDEO websites from the curated catalog below, each opened live in Chromium and
described only with its own homepage text (same verification as AI-tools videos). Sites are not repeated
across parts. Catalog rules: legitimate, lawful, useful or surprising sites only. Never piracy, paywall
bypass, people-search / face-search, open-camera, hacking or anything that invades privacy.
"""
import logging
import time

from . import config, db
from .http import url_ok
from .tools import describe, shows_in_browser

log = logging.getLogger("sites")
SERIES = "Websites that feel illegal to know"

# (name, homepage) in presentation order; the first ones make a strong Part 1.
CATALOG = [
    ("Radio Garden", "https://radio.garden"), ("LiveATC", "https://www.liveatc.net"),
    ("Have I Been Pwned", "https://haveibeenpwned.com"), ("Wayback Machine", "https://web.archive.org"),
    ("MarineTraffic", "https://www.marinetraffic.com"), ("Flightradar24", "https://www.flightradar24.com"),
    ("Photopea", "https://www.photopea.com"), ("remove.bg", "https://www.remove.bg"),
    ("Temp Mail", "https://temp-mail.org"), ("Window Swap", "https://www.window-swap.com"),
    ("EarthCam", "https://www.earthcam.com"), ("Windy", "https://www.windy.com"),
    ("earth nullschool", "https://earth.nullschool.net"), ("VirusTotal", "https://www.virustotal.com"),
    ("BuiltWith", "https://builtwith.com"), ("Downdetector", "https://downdetector.com"),
    ("camelcamelcamel", "https://camelcamelcamel.com"), ("JustWatch", "https://www.justwatch.com"),
    ("Open Library", "https://openlibrary.org"), ("Project Gutenberg", "https://www.gutenberg.org"),
    ("Freesound", "https://freesound.org"), ("Squoosh", "https://squoosh.app"), ("TinyWow", "https://tinywow.com"),
    ("iLovePDF", "https://www.ilovepdf.com"), ("NASA's Eyes", "https://eyes.nasa.gov"),
    ("Stellarium Web", "https://stellarium-web.org"), ("Quick, Draw!", "https://quickdraw.withgoogle.com"),
    ("Google Arts & Culture", "https://artsandculture.google.com"), ("Musopen", "https://musopen.org"),
    ("Can You Run It", "https://www.systemrequirementslab.com/cyri"), ("Similarweb", "https://www.similarweb.com"),
    ("Wappalyzer", "https://www.wappalyzer.com"), ("SkylineWebcams", "https://www.skylinewebcams.com"),
    ("The True Size Of", "https://www.thetruesize.com"), ("Neal.fun", "https://neal.fun"),
    ("TinEye", "https://tineye.com"), ("PDF24 Tools", "https://tools.pdf24.org"),
    ("PairDrop", "https://pairdrop.net"), ("Wormhole", "https://wormhole.app"),
]


def used_urls(conn):
    """Sites already shown in any part (never repeated)."""
    import json
    used = set()
    for r in conn.execute("SELECT raw FROM topics WHERE source LIKE 'sites:%'"):
        used |= {t["url"] for t in json.loads(r["raw"] or "{}").get("tools", [])}
    return used


def next_part(conn):
    return conn.execute("SELECT COUNT(*) FROM topics WHERE source LIKE 'sites:%'").fetchone()[0] + 1


def build(conn, count=None, part=None):
    """Verify the next unused sites and queue Part N. Returns the topic id or None."""
    count = count or config.SITES_PER_VIDEO
    part = part or next_part(conn)
    used = used_urls(conn)
    items = []
    for name, url in CATALOG:
        if len(items) >= count:
            break
        if url in used:
            continue
        try:
            if not url_ok(url):
                log.info("site %s not reachable, skipped", name)
                continue
            desc = describe(name, url)
            if desc and not shows_in_browser(url):
                log.info("%s: page cannot be shown (bot protection or load failure), skipped", name)
                continue
            if not desc:
                log.info("site %s: no usable description, skipped", name)
                continue
            items.append({"name": name, "url": url, "description": desc})
        except Exception as e:
            log.info("site %s skipped: %s", name, e)
    if len(items) < min(count, 3):
        log.warning("only %d verified sites left for part %d; not queued", len(items), part)
        return None
    title = f"{SERIES} - Part {part}"
    tid = db.sha(f"sites:part-{part}:{db.today()}")[:16]
    claims = [{"text": f"{t['name']}: {t['description']}", "source": t["url"]} for t in items]
    # stored under "tools" so the renderer's one-scene-per-item path is shared with AI-tools videos
    conn.execute(
        """INSERT OR IGNORE INTO topics (id,title,source,url,published_at,discovered_at,content_hash,raw,summary,
        claims,source_urls,score,confidence,status,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (tid, title, f"sites:part-{part}", items[0]["url"], db.now_iso(), db.now_iso(),
         db.sha(f"sites:part-{part}"), db.js({"series": SERIES, "part": part, "tools": items}), title,
         db.js(claims), db.js([t["url"] for t in items]), 90, 100, "QUEUED", time.time()))
    return tid


def run(conn):
    """Queue the next part if none is waiting and the daily quota is on."""
    if config.DAILY_SITES_VIDEOS <= 0:
        return 0
    waiting = conn.execute(
        "SELECT COUNT(*) FROM topics WHERE source LIKE 'sites:%' AND status IN "
        "('QUEUED','GENERATING','RENDERING','QC','READY','RETRY')").fetchone()[0]
    if waiting:
        return 0
    return 1 if build(conn) else 0
