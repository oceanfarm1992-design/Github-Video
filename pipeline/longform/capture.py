"""Desktop-width capture for long videos: a tall 1920-px-wide screenshot of a page plus its sections
(heading, the full text under it up to the next heading, and the section's box), so the narration can
explain each part while the camera moves to it.
"""
import json
import logging
from pathlib import Path

from PIL import Image

from .. import db
from ..browser import BLOCKED_RE, CLEAR_OVERLAYS_JS, HIDE_CSS

log = logging.getLogger("longform.capture")
CSS_WIDTH = 1280
SCALE = 1.5          # 1920 px wide
MAX_CSS_HEIGHT = 9000

# every h1-h3 with the text that follows it (until the next heading), and the box around both
DEEP_SECTIONS_JS = r"""(maxH) => {
  const out = [], clean = s => (s || '').replace(/\s+/g, ' ').trim();
  // on GitHub only the README counts (the sidebar has its own 'Releases', 'Contributors' headings)
  const root = document.querySelector('article.markdown-body') || document.body;
  const heads = [...root.querySelectorAll('h1,h2,h3')].filter(h => {
    const r = h.getBoundingClientRect(); return r.width > 40 && r.height > 10 && r.top + scrollY < maxH - 80;
  });
  const blocks = [...root.querySelectorAll('p,li,pre,blockquote,td')].map(el => {
    const q = el.getBoundingClientRect();
    return {el, y: q.top + scrollY, x0: q.left, x1: q.right, y1: q.bottom + scrollY, w: q.width};
  }).filter(b => b.w >= 40);
  heads.forEach((h, i) => {
    const heading = clean(h.innerText);
    if (heading.length < 3 || heading.length > 140) return;
    const r = h.getBoundingClientRect(), top = r.top + scrollY;
    const nextTop = i + 1 < heads.length ? heads[i + 1].getBoundingClientRect().top + scrollY : Math.min(maxH, top + 900);
    let text = '', x0 = r.left, x1 = r.right, y1 = r.bottom + scrollY;
    for (const b of blocks) {
      if (b.y <= top || b.y >= nextTop || text.length > 2400) continue;
      const t = clean(b.el.innerText);
      if (t.length < 2) continue;
      text += (text ? '\n' : '') + (b.el.tagName === 'LI' ? '- ' : '') + t;
      x0 = Math.min(x0, b.x0); x1 = Math.max(x1, b.x1); y1 = Math.max(y1, b.y1);
    }
    out.push({heading, text: text.slice(0, 2400), box: [x0, top, x1 - x0, Math.min(y1, nextTop) - top]});
  });
  return out;
}"""


# small fixed/sticky cookie and consent bars that the size rule in CLEAR_OVERLAYS_JS leaves on screen
COOKIE_BARS_JS = r"""() => {
  for (const el of document.querySelectorAll('body *')) {
    const s = getComputedStyle(el);
    if ((s.position === 'fixed' || s.position === 'sticky') && /cookie|consent|gdpr/i.test(el.innerText || ''))
      el.remove();
  }
}"""


def capture(url, out_dir, max_css_height=MAX_CSS_HEIGHT):
    """(png_path, sections) for a public page, or (None, []) when it cannot be shown."""
    out = Path(out_dir) / f"desk_{db.sha(url + db.today())[:24]}.png"
    meta = out.with_suffix(".json")
    if out.exists() and meta.exists():
        return out, json.loads(meta.read_text(encoding="utf-8"))
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.info("playwright not installed; no capture")
        return None, []
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            try:
                page = b.new_page(viewport={"width": CSS_WIDTH, "height": 800}, device_scale_factor=SCALE,
                                  color_scheme="dark", locale="en-US", bypass_csp=True)
                page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:
                    page.wait_for_timeout(2500)
                page.add_style_tag(content=HIDE_CSS)
                page.evaluate("""async (max) => {
                    for (let y = 0; y < Math.min(document.body.scrollHeight, max); y += 700) {
                        window.scrollTo(0, y); await new Promise(r => setTimeout(r, 120));
                    }
                    window.scrollTo(0, 0);
                }""", max_css_height)
                page.wait_for_timeout(800)
                page.keyboard.press("Escape")
                page.evaluate(CLEAR_OVERLAYS_JS)
                page.evaluate(COOKIE_BARS_JS)
                text = page.title() + " " + page.evaluate("document.body ? document.body.innerText.slice(0, 3000) : ''")
                if BLOCKED_RE.search(text):
                    log.info("capture of %s hit a bot-protection page; skipped", url)
                    return None, []
                height = min(page.evaluate("document.documentElement.scrollHeight"), max_css_height)
                secs = page.evaluate(DEEP_SECTIONS_JS, height)
                page.screenshot(path=str(out), full_page=True,
                                clip={"x": 0, "y": 0, "width": CSS_WIDTH, "height": height})
            finally:
                b.close()
        with Image.open(out) as im:
            if im.height < 700:
                out.unlink(missing_ok=True)
                return None, []
        meta.write_text(json.dumps(secs), encoding="utf-8")
        return out, secs
    except Exception as e:
        log.warning("desktop capture failed for %s: %s", url, e)
        out.unlink(missing_ok=True)
        return None, []
