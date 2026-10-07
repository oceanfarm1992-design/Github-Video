"""SITES series: "Websites that feel illegal to know - Part N" (they are all legal).

Each part shows SITES_PER_VIDEO websites from the curated catalog below, each opened live in Chromium and
described only with its own homepage text (same verification as AI-tools videos). Sites are not repeated
across parts. Catalog rules: legitimate, lawful, useful or surprising sites only. Never piracy, paywall
bypass, people-search / face-search, open-camera, hacking or anything that invades privacy.
"""
import logging
import re
import time
from pathlib import Path

from . import config, db
from .http import url_ok
from .generate import clean_claim
from .tools import describe, shorten, shows_in_browser

log = logging.getLogger("sites")
SERIES = "Websites that feel illegal to know"

# (name, homepage) in presentation order; the first ones make a strong Part 1.
CATALOG = [
    ("Radio Garden", "https://radio.garden"), ("LiveATC", "https://www.liveatc.net"),
    ("Have I Been Pwned", "https://haveibeenpwned.com"), ("Wayback Machine", "https://web.archive.org"),
    ("MarineTraffic", "https://www.marinetraffic.com"), ("Flightradar24", "https://www.flightradar24.com"),
    ("Photopea", "https://www.photopea.com"), ("remove.bg", "https://www.remove.bg"),
    ("Window Swap", "https://www.window-swap.com"),
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


# Safety screen for every catalog entry (also the imported list): never people-search / OSINT tracing,
# leaks, exploits or malware, exposed-device search, open or unsecured cameras, piracy or ROMs, paywall
# bypass, anonymity networks, gambling, adult, crypto / blockchain (financial-content risk, off-theme).
DENY_GROUPS = {
    # people-search, tracing, exposed devices and cameras, leaks, exploits, malware, hacking practice
    "privacy & security": (
        r"osint|people ?(search|finder)|person ?(search|finder|lookup)|whatsmyname|sherlock|maltego|spiderfoot|"
        r"harvester|recon-ng|intelx|intelligence x|geospy|face ?(search|check|recognition)|pimeyes|"
        r"reverse (phone|email|people)|background check|leakix|data ?leak|leak search|breach ?(search|data)|"
        r"exploit|malware|phish|shodan|censys|zoomeye|insecam|webcam taxi|\bhack(ing|tricks|viser|thissite)?\b|"
        r"hack the box|\bctf|pentest|penetration|vulnerab|security research|reverse engineer|seclists|"
        r"google dork|password (crack|recover)|keylog|spyware|\bspy\b|doxx|stalk"),
    # piracy, downloaders/converters, scraping, paywall bypass, game cheats: copyright and terms-of-service risk
    "legal & copyright": (
        r"torrent|\bpira(te|cy)|warez|\bcrack|keygen|serial key|rom ?hack|\broms?\b|emulator zone|"
        r"unpaywall|paywall|downloader|\bdownload (video|music|mp3)|youtube (to|download|converter)|"
        r"\bmp3 (convert|download)|video download|scrap(er|ing|y)\b|beautiful soup|"
        r"\bcheats?\b(?!\s*sheet|\.sh)|cheat engine|\baimbot|captcha (solv|bypass)|bypass|undetect"),
    # proxies, VPNs, anonymity networks, disposable identities
    "proxy & anonymity": (
        r"\bproxy|proxies|\bvpn\b|unblock|anonymi[sz]|\btor\b|onion|dark ?web|temp(orary)? ?mail|"
        r"disposable (email|mail)|burner|fake (email|name|identity|id\b|address)|10 ?minute ?mail"),
    # adult, dating, gambling, recreational drugs, weapons, alcohol, tobacco
    "adult & restricted": (
        r"adult|porn|\bnsfw|\bxxx|\bsex|nud(e|ity)|onlyfans|escort|hookup|dating|gambl|casino|betting|"
        r"\bbet\b|lotter|poker|cannabis|marijuana|\bweed\b|(recreational|illegal|buy) drugs?|\bvape|tobacco|"
        r"alcohol|firearm|\bguns?\b|weapon|ammo"),
    # investing, trading, loans, banking, payments, insurance, tax, crypto: financial-content risk
    # (stock PHOTO/VIDEO sites and World Bank open data are fine)
    "financial": (
        r"financ|fintech|invest|trading|\btrader|"
        r"\bstocks?\b(?!\s*(photo|video|image|asset|footage|music|illustration|vector))|stockanalysis|"
        r"market ?cap|forex|broker|\bloan|\bcredit|mortgage|banking|(?<!world )\bbank\b|payment|\bpay\b|"
        r"wallet|insurance|\btax\b|accounting|cryptocurrenc|\bcrypto\b|blockchain|\w*coin\b|\bdefi\b|"
        r"etherscan|solscan|\bnft|exchange rate|market data|\bwealth"),
}
DENY = re.compile("|".join(f"(?:{p})" for p in DENY_GROUPS.values()), re.I)


def deny_reason(text):
    """Which risk group blocks this entry, or None."""
    for group, pattern in DENY_GROUPS.items():
        if re.search(pattern, text, re.I):
            return group
    return None

# Theme fit for "feels illegal to know": surprising, explorable, free sites first; developer
# infrastructure last (still valid if it has content, just less of a "wow").
TIER1 = re.compile(r"archive|research|map|explor|space|astronom|satellite|ocean|marine|weather|earth|"
                   r"flight|plane|ship|vessel|iss\b|camera|webcam|radio|music|sound|game|history|book|"
                   r"librar|free|image|photo|video|ai|calculator|utilit|learn|educat|data|science|museum|"
                   r"art|privacy", re.I)
TIER2 = re.compile(r"design|productiv|icon|font|color|3d|maker|electronic|math|template|writing|"
                   r"presentation|chart|visual", re.I)


def _tier(name, category):
    text = f"{name} {category}"
    return 0 if TIER1.search(text) else 1 if TIER2.search(text) else 2


def load_catalog():
    """Hand-picked CATALOG first, then the imported list (pipeline/sites_catalog.json), screened by
    DENY, de-duplicated by URL and ordered by theme fit."""
    import json
    seen, out = set(), []

    def key(u):
        return re.sub(r"^https?://(www\.)?", "", u).rstrip("/").lower()

    for name, url in CATALOG:
        if key(url) not in seen and not DENY.search(name):
            seen.add(key(url))
            out.append((name, url))
    try:
        extra = json.loads((Path(__file__).with_name("sites_catalog.json")).read_text(encoding="utf-8"))
    except Exception:
        extra = []
    ranked = sorted(((_tier(e["name"], e.get("category", "")), i, e) for i, e in enumerate(extra)),
                    key=lambda x: (x[0], x[1]))
    for _, _, e in ranked:
        u = e.get("url", "")
        if u.startswith("https://") and key(u) not in seen and not DENY.search(f"{e['name']} {e.get('category', '')}"):
            seen.add(key(u))
            out.append((e["name"], u))
    return out


def used_urls(conn):
    """Sites already shown in any part (never repeated)."""
    import json
    used = set()
    for r in conn.execute("SELECT raw FROM topics WHERE source LIKE 'sites:%'"):
        used |= {t["url"] for t in json.loads(r["raw"] or "{}").get("tools", [])}
    return used


def next_part(conn):
    return conn.execute("SELECT COUNT(*) FROM topics WHERE source LIKE 'sites:%'").fetchone()[0] + 1


SKIP_HEADINGS = re.compile(r"cookie|privacy|newsletter|subscribe|sign ?(in|up)|log ?in|menu|footer|download (our|the) app|"
                           r"contact|terms|faq|follow us|get started|pricing", re.I)


def pick_sections(raw_sections, description, limit=4):
    """The page's own sections worth narrating: heading + one sentence of its text, in page order."""
    out, seen = [], set()
    for s in raw_sections:
        heading = clean_claim(s.get("heading", ""), limit=80)
        text = clean_claim((s.get("text") or "").split(". ")[0], limit=300)
        if not heading or not text or SKIP_HEADINGS.search(heading):
            continue
        heading = heading.rstrip(".")
        key = heading.lower()
        if key in seen or text.rstrip(".").lower() in description.lower():
            continue
        w, h = s["box"][2], s["box"][3]
        if w < 60 or h < 12 or h > 900:
            continue
        seen.add(key)
        out.append({"heading": heading, "text": shorten(text, 110), "box": s["box"]})
        if len(out) >= limit:
            break
    return out


def build_single(conn, part):
    """One website per video: its homepage explored section by section (camera zooms into each)."""
    from . import browser
    used = used_urls(conn)
    for name, url in load_catalog():
        if url in used:
            continue
        try:
            if not url_ok(url):
                continue
            desc = describe(name, url)
            if not desc or not shows_in_browser(url):
                log.info("site %s skipped (no description or cannot be shown)", name)
                continue
            page = browser.capture(url, Path(config.OUT_DIR) / "assets", any_site=True, max_css_height=2600)
            sections = pick_sections(browser.sections_for(page), desc) if page else []
            if len(sections) < 2:  # too little on the page to narrate a whole video
                log.info("site %s: only %d usable sections, skipped", name, len(sections))
                continue
        except Exception as e:
            log.info("site %s skipped: %s", name, e)
            continue
        site = {"name": name, "url": url, "description": desc}
        title = f"{SERIES} - Part {part}: {name}"
        tid = db.sha(f"sites:part-{part}:{db.today()}")[:16]
        claims = [{"text": f"{name}: {desc}", "source": url}] + \
                 [{"text": f"{s['heading']}: {s['text']}", "source": url} for s in sections]
        conn.execute(
            """INSERT OR IGNORE INTO topics (id,title,source,url,published_at,discovered_at,content_hash,raw,summary,
            claims,source_urls,score,confidence,status,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (tid, title, f"sites:part-{part}", url, db.now_iso(), db.now_iso(), db.sha(f"sites:part-{part}"),
             db.js({"series": SERIES, "part": part, "tools": [site], "sections": sections}), title,
             db.js(claims), db.js([url]), 90, 100, "QUEUED", time.time()))
        return tid
    log.warning("no catalog site left with enough content for part %d", part)
    return None


def build(conn, count=None, part=None):
    """Verify the next unused sites and queue Part N. Returns the topic id or None."""
    count = count or config.SITES_PER_VIDEO
    part = part or next_part(conn)
    if count == 1:
        return build_single(conn, part)
    used = used_urls(conn)
    items = []
    for name, url in load_catalog():
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
