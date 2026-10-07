"""Headless Chromium capture of a page, top to bottom, for the scrolling "screen recording" scenes.

One full-page screenshot at 2x (crisp), which motion.py scrolls through smoothly. Cheaper and sharper
than recording a live browser in real time. Only public pages, no login, no interaction beyond scrolling.
"""
import logging
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
                                  color_scheme="dark", locale="en-US")
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
                height = min(page.evaluate("document.documentElement.scrollHeight"), max_css_height)
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
