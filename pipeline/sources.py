"""New AI tools from two free official APIs, added to the AI-tools catalogs (pipeline/catalog.py).

Hugging Face Spaces - public API, trending AI apps (main channel's AI-tools series).
Product Hunt        - official GraphQL API (PRODUCT_HUNT_API token), new AI products (main, AI for Kids,
                      AI for Normal People). Product Hunt's API terms restrict commercial use without their
                      permission; the owner chose to use it.

Only names and links come from these lists. Each tool is sorted into a topic by keywords (no AI, free), checked
live like every catalog entry (site answers, has its own description, name matches the site), and what a video
says about it still comes from the tool's own page at build time. Found tools are kept for one year.
"""
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request

from . import catalog, db
from .http import request

log = logging.getLogger("sources")
KEEP_DAYS = 365
MAX_CHECKS = 30            # live checks per source per day (each fetches the tool's site)
HF_MIN_LIKES = 30
HF_API = "https://huggingface.co/api/spaces?sort=trendingScore&limit=100&full=true"
PH_API = "https://api.producthunt.com/v2/api/graphql"
PH_QUERY = """query($topic: String!, $after: DateTime!) { posts(first: 50, order: VOTES, topic: $topic, postedAfter: $after) { edges { node {
  name tagline website votesCount topics(first: 6) { edges { node { slug } } } } } } }"""

# series -> [(category, keyword regex)], first match wins; categories are the catalogs' own keys
TOPICS = {
    "tools": [
        ("video", r"\bvideos?\b|animat|\bclips?\b|lip ?sync"), ("music", r"\bmusic|\bsongs?\b|\bbeats?\b"),
        ("voice", r"\bvoices?\b|speech|text.to.speech|\btts\b|transcri|dubb"),
        ("image", r"\bimages?\b|\bphotos?\b|\bpictures?\b|illustrat|\bdiffusion\b|\bart\b"),
        ("coding", r"\bcod(e|ing)\b|developer|programm|\bapi\b|github|\bsql\b"),
        ("automation", r"\bagents?\b|automat|workflow"), ("design", r"design|presentation|slides|\blogo|\bui\b"),
        ("research", r"research|papers?\b|summar|\bpdfs?\b|documents?|notes?\b"),
        ("assistants", r"\bchat|assistant")],
    "everyday": [
        ("writing", r"\bwrit|\bemails?\b|grammar"), ("photos", r"\bphotos?\b|background remov|\bselfie"),
        ("travel", r"\btravel|\btrips?\b|flights?\b|itinerar"), ("cooking", r"recipe|\bcook|\bmeals?\b|\bfood"),
        ("accessibility", r"accessib|\bblind|\bdeaf|captions?\b|dyslex"),
        ("learning", r"\blearn|\bcourses?\b|languages?\b|\bstudy"),
        ("organised", r"productiv|\btasks?\b|calendar|schedul|to.?do|reminder"),
        ("home", r"\bhome\b|\bplants?\b|\bpets?\b|garden|\bdiy\b")],
    "kids": [
        ("teachers", r"teacher|classroom|lesson"), ("homework", r"homework|\bmath|tutor"),
        ("reading", r"\bread(ing)?\b|stor(y|ies)|\bbooks?\b"), ("coding", r"\bcod(e|ing)\b|programm"),
        ("drawing", r"\bdraw|\bart\b|colou?r"), ("music", r"\bmusic|\bsongs?\b"),
        ("languages", r"languages?\b|vocabular")],
}
KIDS_RE = re.compile(r"\bkids?\b|child|student|teacher|classroom|parent|school|toddler", re.I)
EVERYDAY_TOPICS = {"productivity", "writing", "photography", "travel", "food-drink", "education", "lifestyle",
                   "home", "languages", "accessibility", "personal-finance-2", "health-fitness"}
NOT_FOR_VIDEOS = re.compile(r"\b(nsfw|adult|porn|nude|undress|casino|betting|crypto|forex|loan)\b", re.I)


def topic_for(series, text):
    for cat, pat in TOPICS[series]:
        if re.search(pat, text, re.I):
            return cat
    return None


def final_url(url):
    """The tool's homepage behind a redirect link (Product Hunt's /r/ links), without tracking parameters."""
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        u = urllib.parse.urlparse(r.geturl())
    return f"https://{u.netloc}{u.path if u.path not in ('', '/') else '/'}"


def hugging_face():
    """[(series, category, name, url)] from trending Spaces."""
    _, _, body = request(HF_API, attempts=2)
    out = []
    for s in json.loads(body):
        card = s.get("cardData") or {}
        desc = card.get("short_description") or ""
        title = card.get("title") or s["id"].split("/")[-1].replace("-", " ").replace("_", " ")
        if (s.get("likes") or 0) < HF_MIN_LIKES or not desc or NOT_FOR_VIDEOS.search(f"{title} {desc}"):
            continue
        cat = topic_for("tools", f"{title} {desc} {' '.join(s.get('tags', []))}")
        if cat:
            out.append(("tools", cat, title.strip()[:60], f"https://huggingface.co/spaces/{s['id']}"))
    return out


def product_hunt(token):
    """[(series, category, name, link)] from new AI products on Product Hunt."""
    out = []
    for topic in ("artificial-intelligence",):
        after = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 7 * 86400))  # this week's most voted
        _, _, body = request(PH_API, method="POST", data=json.dumps({"query": PH_QUERY,
                                                                     "variables": {"topic": topic, "after": after}}).encode(),
                             headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                                      "Accept": "application/json"}, attempts=2)
        data = json.loads(body)
        if data.get("errors"):
            raise RuntimeError(f"product hunt: {str(data['errors'])[:300]}")
        edges = (data.get("data") or {}).get("posts", {}).get("edges", [])
        log.info("product hunt: %d AI products this week", len(edges))
        for e in edges:
            p = e["node"]
            text = f"{p['name']} {p.get('tagline') or ''}"
            if not p.get("website") or NOT_FOR_VIDEOS.search(text) or (p.get("votesCount") or 0) < 20:
                continue
            slugs = {t["node"]["slug"] for t in p.get("topics", {}).get("edges", [])}
            cat = topic_for("tools", text)
            if cat:
                out.append(("tools", cat, p["name"][:60], p["website"]))
            if slugs & EVERYDAY_TOPICS and (cat := topic_for("everyday", text)):
                out.append(("everyday", cat, p["name"][:60], p["website"]))
            if KIDS_RE.search(text) and (cat := topic_for("kids", text)):
                out.append(("kids", cat, p["name"][:60], p["website"]))
    return out


def add(conn, found, label):
    """Checks and stores new tools; returns how many were added."""
    catalog._ensure(conn)
    known = {s: {catalog.norm(u) for _, _, e in catalog.merged(conn, s).values() for _, u in e} for s in TOPICS}
    added = checks = 0
    homes = {}
    for series, cat, name, link in found:
        if checks >= MAX_CHECKS:
            break
        try:
            url = homes.get(link) or (final_url(link) if "producthunt.com/r/" in link else link)
            homes[link] = url
            if catalog.norm(url) in known[series]:
                continue
            checks += 1
            if not catalog.verify(name, url):
                log.info("%s: %s (%s) not verified; skipped", label, name, url)
                continue
        except Exception as e:
            log.info("%s: %s skipped (%s)", label, name, e)
            continue
        phrase, tag, _ = catalog.base(series)[cat]
        conn.execute("INSERT OR IGNORE INTO catalog_tools VALUES (?,?,?,?,?,?,?)",
                     (series, cat, phrase, tag, name, url, time.time()))
        known[series].add(catalog.norm(url))
        added += 1
    return added


def run(conn):
    """Once a day: new tools from Hugging Face and Product Hunt; tools found more than a year ago are dropped."""
    catalog._ensure(conn)
    conn.execute("DELETE FROM catalog_tools WHERE added_at < ?", (time.time() - KEEP_DAYS * 86400,))
    out = {}
    jobs = [("hugging_face", lambda: hugging_face())]
    # Product Hunt hides every product's own link behind producthunt.com/r/ redirects, which Cloudflare blocks
    # for scripts; it stays off (PRODUCT_HUNT=1 turns it back on if that changes)
    token = os.environ.get("PRODUCT_HUNT_API") if os.environ.get("PRODUCT_HUNT") == "1" else None
    if token:
        jobs.append(("product_hunt", lambda: product_hunt(token)))
    for name, fetch in jobs:  # each source once a day; a failed one retries on the next run
        if db.kv_get(conn, f"sources_done:{name}") == db.today():
            continue
        try:
            found = fetch()
            out[name] = add(conn, found, name.replace("_", " "))
            if found:  # an empty answer is retried on the next run
                db.kv_set(conn, f"sources_done:{name}", db.today())
        except Exception as e:
            log.warning("%s failed: %s", name, e)
    return out
