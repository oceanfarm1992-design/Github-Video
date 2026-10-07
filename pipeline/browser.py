"""Headless Chromium capture of a page, top to bottom, for the scrolling "screen recording" scenes.

One full-page screenshot at 2x (crisp), which motion.py scrolls through smoothly. Cheaper and sharper
than recording a live browser in real time. Only public pages, no login, no interaction beyond scrolling.
"""
import json
import logging
import re
from pathlib import Path

from PIL import Image

from . import db

log = logging.getLogger("browser")
CSS_WIDTH = 540      # phone-ish width -> GitHub's single-column layout, readable in a Short
SCALE = 2            # 1080 px wide capture
MAX_CSS_HEIGHT = 7000
HIDE_CSS = """
  .js-cookie-consent-banner, [data-testid*='cookie'], .signup-prompt, .js-notification-shelf,
  .AppHeader-globalBar-end, .flash-full,
  #onetrust-banner-sdk, #onetrust-consent-sdk, #CybotCookiebotDialog, .cc-window, .osano-cm-window,
  #usercentrics-root, .fc-consent-root, #truste-consent-track, [aria-label='cookie consent'] { display: none !important; }
"""


# remove modal dialogs and fixed/sticky overlays covering >25% of the viewport (sign-up popups,
# promo banners), and re-enable scrolling that modals often lock
CLEAR_OVERLAYS_JS = """() => {
  const area = innerWidth * innerHeight;
  document.querySelectorAll('[role=dialog],[aria-modal=true]').forEach(el => el.remove());
  for (const el of document.querySelectorAll('body *')) {
    const s = getComputedStyle(el);
    if (s.position === 'fixed' || s.position === 'sticky') {
      const r = el.getBoundingClientRect();
      if (r.width * r.height > area * 0.25) el.remove();
    }
  }
  document.documentElement.style.overflow = 'auto';
  document.body.style.overflow = 'auto';
}"""


BLOCKED_RE = re.compile(
    r"why have i been blocked|attention required|just a moment\.\.\.|checking your browser|verify you are human|"
    r"access denied|are you a robot|enable javascript and cookies to continue|request unsuccessful|"
    r"unusual traffic|captcha", re.I)


def available():
    """True when Playwright + Chromium can be used here (heavy CI stages)."""
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


# Page sections in reading order: each h1-h3 heading with the first real paragraph after it and the box
# (CSS px, page coordinates) of the block that holds them. Used to zoom into important parts.
SECTIONS_JS = r"""(maxH) => {
  const out = [], clean = s => (s || '').replace(/\s+/g, ' ').trim();
  for (const h of document.querySelectorAll('h1,h2,h3')) {
    const r = h.getBoundingClientRect(), y = r.top + scrollY;
    if (r.width < 40 || r.height < 10 || y > maxH - 120) continue;
    const heading = clean(h.innerText);
    if (heading.length < 3 || heading.length > 120) continue;
    let text = '', pel = null, el = h.nextElementSibling, k = 0;
    while (el && k < 4 && !text) { const t = clean(el.innerText); if (t.length > 30) { text = t; pel = el; } el = el.nextElementSibling; k++; }
    // box = heading + its own paragraph (not the shared parent grid)
    let x0 = r.left, y0 = r.top, x1 = r.right, y1 = r.bottom;
    if (pel) { const q = pel.getBoundingClientRect(); if (q.height < 600) { x0 = Math.min(x0, q.left); y0 = Math.min(y0, q.top); x1 = Math.max(x1, q.right); y1 = Math.max(y1, q.bottom); } }
    out.push({heading, text: text.slice(0, 400), box: [x0, y0 + scrollY, x1 - x0, y1 - y0]});
  }
  return out;
}"""


def sections_for(page_path):
    """Sections recorded when the page was captured ([] if none)."""
    try:
        return json.loads(Path(page_path).with_suffix(".json").read_text(encoding="utf-8"))
    except Exception:
        return []


def supported(url):
    return bool(url) and url.startswith(("https://github.com/", "https://huggingface.co/"))


def capture(url, out_dir, any_site=False, max_css_height=None):
    """Returns a Path to a tall 1080-px-wide PNG of the page, or None. Cached per URL per day.
    any_site=True for curated public homepages (AI-tools videos); otherwise GitHub/Hugging Face only."""
    max_css_height = max_css_height or MAX_CSS_HEIGHT
    if not (supported(url) or (any_site and url and url.startswith("https://"))):
        return None
    out = Path(out_dir) / f"page_{db.sha(url + db.today())[:24]}.png"
    if out.exists():
        return out
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.info("playwright not installed; no page capture")
        return None
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            try:
                page = b.new_page(viewport={"width": CSS_WIDTH, "height": 960}, device_scale_factor=SCALE,
                                  color_scheme="dark", locale="en-US",
                                  bypass_csp=True)  # strict CSP sites would block our banner-hiding CSS
                page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                try:  # marketing sites rarely go fully idle (analytics); a best-effort wait is enough
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:
                    page.wait_for_timeout(2500)
                page.add_style_tag(content=HIDE_CSS)
                # scroll once so lazy README images load, then return to the top
                page.evaluate("""async (max) => {
                    for (let y = 0; y < Math.min(document.body.scrollHeight, max); y += 700) {
                        window.scrollTo(0, y); await new Promise(r => setTimeout(r, 120));
                    }
                    window.scrollTo(0, 0);
                }""", max_css_height)
                page.wait_for_timeout(800)
                page.keyboard.press("Escape")  # closes most sign-up / promo modals
                page.evaluate(CLEAR_OVERLAYS_JS)
                text = (page.title() + " " + page.evaluate("document.body ? document.body.innerText.slice(0, 3000) : ''"))
                if BLOCKED_RE.search(text):  # a bot-protection page, not the website: never show it in a video
                    log.info("capture of %s hit a bot-protection page; skipped", url)
                    return None
                height = min(page.evaluate("document.documentElement.scrollHeight"), max_css_height)
                # where the page's own sections are (heading + text under it), for zoom-in scenes
                try:
                    secs = page.evaluate(SECTIONS_JS, height)
                    out.with_suffix(".json").write_text(json.dumps(secs), encoding="utf-8")
                except Exception as e:
                    log.info("section scan failed for %s: %s", url, e)
                page.screenshot(path=str(out), full_page=True,
                                clip={"x": 0, "y": 0, "width": CSS_WIDTH, "height": height})
            finally:
                b.close()
        with Image.open(out) as im:
            if im.height < 900:  # too short to be worth showing
                out.unlink(missing_ok=True)
                return None
        return out
    except Exception as e:
        log.warning("page capture failed for %s: %s", url, e)
        out.unlink(missing_ok=True)
        return None
