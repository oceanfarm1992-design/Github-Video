"""Source material for a long video: real text only, plus the visuals the narration can point at.

github - one repo: GitHub stats, the full README, its sections on the rendered repo page, its code blocks
tools  - one catalog category: each tool's own homepage sections
sites  - several catalog websites: each site's own homepage sections

Every visual has an id; the script writer may only cite those ids, so each paragraph is shown with the
exact part of the page (or the exact code block) it talks about.
"""
import json
import logging
import re
import time
from pathlib import Path

from .. import config, ghdata, sites, tools
from ..http import url_ok
from . import capture

log = logging.getLogger("longform.material")
MIN_WORDS = 700          # less real material than this cannot fill 8 minutes without padding
MAX_CODE_BLOCKS = 8
MAX_CODE_LINES = 24


def words(text):
    return len(re.findall(r"[A-Za-z0-9']+", text or ""))


def _clean_sections(secs, limit=14):
    out, seen = [], set()
    for s in secs:
        heading = (s.get("heading") or "").strip()
        text = (s.get("text") or "").strip()
        if not heading or sites.SKIP_HEADINGS.search(heading) or heading.lower() in seen or words(text) < 12:
            continue
        w, h = s["box"][2], s["box"][3]
        if w < 80 or h < 20:
            continue
        seen.add(heading.lower())
        out.append({"heading": heading, "text": text, "box": s["box"]})
        if len(out) >= limit:
            break
    return out


def code_blocks(readme):
    """README code blocks exactly as written (shell commands, examples), trimmed to fit a panel."""
    out = []
    for lang, body in re.findall(r"```([\w+-]*)\n(.*?)```", readme, re.S):
        lines = [ln.rstrip() for ln in body.strip("\n").splitlines()]
        if not lines or len(lines) > 60 or ghdata.SECRET_RE.search(body):
            continue
        prose = lang.lower() in ("", "text", "txt", "md", "markdown") and             sum(len(x) for x in lines) / len(lines) > 60  # a sentence in a box, not code
        if prose:
            continue
        out.append({"lang": lang or "text", "lines": lines[:MAX_CODE_LINES]})
        if len(out) >= MAX_CODE_BLOCKS:
            break
    return out


def _strip_markdown(md):
    md = re.sub(r"```.*?```", " ", md, flags=re.S)
    md = re.sub(r"<[^>]+>", " ", md)
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", md)
    md = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", md)
    return re.sub(r"[ \t]+", " ", md)


# ------------------------------------------------------------------ github
def github(conn, used, out_dir):
    rows = conn.execute(
        """SELECT title, github_url, raw FROM topics WHERE source='github' AND status='PUBLISHED'
        AND updated_at>=? ORDER BY updated_at DESC""", (time.time() - 21 * 86400,)).fetchall()
    rows = sorted(rows, key=lambda r: -(json.loads(r["raw"] or "{}").get("stars") or 0))
    for r in rows:
        full = ghdata.repo_name(r["github_url"])
        if not full or f"github:{full.lower()}" in used:
            continue
        try:
            readme = ghdata._get(f"/repos/{full}/readme", "application/vnd.github.raw").decode("utf-8", "replace")
        except Exception as e:
            log.info("%s: no README (%s)", full, e)
            continue
        prose = _strip_markdown(readme)
        if words(prose) < MIN_WORDS or sum(ch.isascii() for ch in prose) / max(1, len(prose)) < 0.9:
            log.info("%s: README too thin or not English for a long video", full)
            continue
        info = ghdata.for_video(r["github_url"], out_dir, conn)
        page, secs = capture.capture(r["github_url"], out_dir)
        if not page or not info:
            continue
        sections = _clean_sections(secs)
        visuals = [{"id": 0, "kind": "overview", "page": 0, "label": "The repository page"},
                   {"id": 1, "kind": "stats", "label": "Stars, forks, language, licence, last update"}]
        for s in sections:
            visuals.append({"id": len(visuals), "kind": "page", "page": 0, "box": s["box"],
                            "label": f"README section '{s['heading']}'", "text": s["text"][:1200]})
        for c in code_blocks(readme):
            visuals.append({"id": len(visuals), "kind": "code", "code": c,
                            "label": f"README code block ({c['lang']})", "text": "\n".join(c["lines"])})
        facts = (f"Repository: {info['name']}\nStars: {info['stars']:,}\nForks: {info['forks']:,}\n"
                 f"Language: {info.get('language')}\nLicense: {info.get('license')}\n"
                 f"Created: {(info.get('created_at') or '')[:10]}\nLast update: {(info.get('pushed_at') or '')[:10]}\n"
                 f"\nREADME:\n{prose[:60000]}")
        return {"format": "github", "subject": info["name"], "key": f"github:{full.lower()}",
                "facts": facts, "visuals": visuals, "pages": [(str(page), r["github_url"], info["name"])],
                "links": [(info["name"], r["github_url"])], "stats": info, "material_words": words(prose)}
    return None


# ------------------------------------------------------------------- tools
def _site_pages(entries, out_dir, need, max_items, min_sections):
    """Capture each site; keep those with a description and enough real sections."""
    pages, items = [], []
    for name, url in entries:
        if len(items) >= max_items:
            break
        try:
            if not url_ok(url):
                continue
            desc = tools.describe(name, url)
            page, secs = capture.capture(url, out_dir, max_css_height=6000) if desc else (None, [])
        except Exception as e:
            log.info("%s skipped: %s", name, e)
            continue
        sections = _clean_sections(secs, limit=6)
        if not page or len(sections) < min_sections:
            log.info("%s: not enough page content for a long video", name)
            continue
        pages.append((str(page), url, name))
        items.append({"name": name, "url": url, "description": desc, "sections": sections, "page": len(pages) - 1})
    return (pages, items) if len(items) >= need else (None, None)


def _visuals_for(items):
    visuals = []
    for it in items:
        visuals.append({"id": len(visuals), "kind": "overview", "page": it["page"], "label": f"{it['name']} homepage",
                        "text": it["description"]})
        for s in it["sections"]:
            visuals.append({"id": len(visuals), "kind": "page", "page": it["page"], "box": s["box"],
                            "label": f"{it['name']}: section '{s['heading']}'", "text": s["text"][:1200]})
    return visuals


def tools_review(conn, used, out_dir):
    for category, (phrase, _tag, entries) in tools.CATALOG.items():
        if f"tools:{category}" in used:
            continue
        pages, items = _site_pages(entries, out_dir, need=5, max_items=8, min_sections=2)
        if not items:
            continue
        facts = "\n\n".join(f"TOOL {i}: {it['name']} ({it['url']})\nDescription: {it['description']}"
                            for i, it in enumerate(items, 1))
        visuals = _visuals_for(items)
        return {"format": "tools", "subject": f"AI tools for {phrase}", "key": f"tools:{category}",
                "facts": facts, "visuals": visuals, "pages": pages,
                "links": [(it["name"], it["url"]) for it in items],
                "material_words": words(facts) + sum(words(v.get("text", "")) for v in visuals)}
    return None


# ------------------------------------------------------------------- sites
def sites_tour(conn, used, out_dir):
    shorts = sites.used_urls(conn)
    entries = [(n, u) for n, u in sites.load_catalog() if u not in shorts and f"site:{u}" not in used][:40]
    pages, items = _site_pages(entries, out_dir, need=5, max_items=6, min_sections=3)
    if not items:
        return None
    facts = "\n\n".join(f"WEBSITE {i}: {it['name']} ({it['url']})\nDescription: {it['description']}"
                        for i, it in enumerate(items, 1))
    visuals = _visuals_for(items)
    return {"format": "sites", "subject": "Websites that feel illegal to know",
            "key": "sites:" + ",".join(sorted(it["url"] for it in items)),
            "site_keys": [f"site:{it['url']}" for it in items],
            "facts": facts, "visuals": visuals, "pages": pages,
            "links": [(it["name"], it["url"]) for it in items],
            "material_words": words(facts) + sum(words(v.get("text", "")) for v in visuals)}


BUILDERS = {"github": github, "tools": tools_review, "sites": sites_tour}


def collect(conn, fmt, used):
    out_dir = Path(config.OUT_DIR) / "assets"
    out_dir.mkdir(parents=True, exist_ok=True)
    m = BUILDERS[fmt](conn, used, out_dir)
    if m and m["material_words"] < MIN_WORDS:
        log.info("%s material for %s is too thin (%d words)", fmt, m["subject"], m["material_words"])
        return None
    return m
