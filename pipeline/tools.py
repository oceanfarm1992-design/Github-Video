"""AI-TOOLS videos: "N AI tools for <category>", each tool shown live in Chromium.

The catalog below is only a list of names + official homepages (curation, not facts). What the video
says about each tool comes from that tool's OWN homepage description, fetched at build time; a tool
whose site does not load or has no description is skipped. No ranking claims ("best"): items are
numbered in catalog order. One category per video; a category is reused only after REUSE_DAYS.
"""
import json
import logging
import re
import time

from . import config, db
from .generate import clean_claim
from .http import page_description, url_ok

log = logging.getLogger("tools")
REUSE_DAYS = 30

# category key -> (spoken phrase, hashtag, [(name, homepage), ...]); more tools than needed, so dead
# sites or missing descriptions can be skipped and the video still has enough items.
CATALOG = {
    "video": ("video generation", "#AIVideo", [
        ("Runway", "https://runwayml.com"), ("Pika", "https://pika.art"),
        ("Luma Dream Machine", "https://lumalabs.ai/dream-machine"), ("Kling AI", "https://klingai.com"),
        ("Sora", "https://openai.com/sora"), ("Google Veo", "https://deepmind.google/models/veo/"),
        ("Hailuo AI", "https://hailuoai.video"), ("HeyGen", "https://www.heygen.com"),
        ("Synthesia", "https://www.synthesia.io"), ("InVideo AI", "https://invideo.io"),
        ("Pictory", "https://pictory.ai"), ("CapCut", "https://www.capcut.com"),
        ("Descript", "https://www.descript.com"), ("Higgsfield", "https://higgsfield.ai"),
        ("Vidu", "https://www.vidu.com")]),
    "image": ("image generation", "#AIArt", [
        ("Midjourney", "https://www.midjourney.com"), ("Leonardo.Ai", "https://leonardo.ai"),
        ("Ideogram", "https://ideogram.ai"), ("Adobe Firefly", "https://firefly.adobe.com"),
        ("Stability AI", "https://stability.ai"), ("FLUX by Black Forest Labs", "https://bfl.ai"),
        ("Recraft", "https://www.recraft.ai"), ("Krea", "https://www.krea.ai"),
        ("Playground", "https://playground.com"), ("Freepik AI", "https://www.freepik.com/ai/image-generator"),
        ("Microsoft Designer", "https://designer.microsoft.com"), ("Canva AI", "https://www.canva.com/ai-image-generator/")]),
    "voice": ("voice and speech", "#AIVoice", [
        ("ElevenLabs", "https://elevenlabs.io"), ("Murf AI", "https://murf.ai"), ("PlayHT", "https://play.ht"),
        ("Resemble AI", "https://www.resemble.ai"), ("WellSaid", "https://wellsaidlabs.com"),
        ("Speechify", "https://speechify.com"), ("Adobe Podcast", "https://podcast.adobe.com"),
        ("Krisp", "https://krisp.ai"), ("LOVO", "https://lovo.ai"), ("Cartesia", "https://cartesia.ai"),
        ("Hume AI", "https://www.hume.ai")]),
    "music": ("music creation", "#AIMusic", [
        ("Suno", "https://suno.com"), ("Udio", "https://www.udio.com"), ("AIVA", "https://www.aiva.ai"),
        ("Soundraw", "https://soundraw.io"), ("Mubert", "https://mubert.com"), ("Beatoven.ai", "https://www.beatoven.ai"),
        ("Boomy", "https://boomy.com"), ("Stable Audio", "https://stableaudio.com"), ("Loudly", "https://www.loudly.com"),
        ("Riffusion", "https://www.riffusion.com")]),
    "coding": ("coding", "#AICoding", [
        ("GitHub Copilot", "https://github.com/features/copilot"), ("Claude Code", "https://www.anthropic.com/claude-code"),
        ("Cursor", "https://cursor.com"), ("Windsurf", "https://windsurf.com"), ("Replit", "https://replit.com"),
        ("Bolt", "https://bolt.new"), ("Lovable", "https://lovable.dev"), ("v0", "https://v0.dev"),
        ("Tabnine", "https://www.tabnine.com"), ("Amazon Q Developer", "https://aws.amazon.com/q/developer/"),
        ("Gemini Code Assist", "https://codeassist.google"), ("Zed", "https://zed.dev")]),
    "assistants": ("everyday AI chat", "#AIAssistant", [
        ("ChatGPT", "https://chatgpt.com"), ("Claude", "https://claude.ai"), ("Gemini", "https://gemini.google.com"),
        ("Microsoft Copilot", "https://copilot.microsoft.com"), ("Perplexity", "https://www.perplexity.ai"),
        ("Le Chat by Mistral", "https://chat.mistral.ai"), ("Grok", "https://grok.com"),
        ("DeepSeek", "https://chat.deepseek.com"), ("Meta AI", "https://www.meta.ai"), ("Poe", "https://poe.com"),
        ("Pi", "https://pi.ai"), ("Qwen Chat", "https://chat.qwen.ai")]),
    "design": ("presentations and design", "#AIDesign", [
        ("Gamma", "https://gamma.app"), ("Beautiful.ai", "https://www.beautiful.ai"), ("Canva", "https://www.canva.com"),
        ("Figma AI", "https://www.figma.com/ai/"), ("Pitch", "https://pitch.com"), ("Plus AI", "https://plusai.com"),
        ("Napkin AI", "https://www.napkin.ai"), ("Uizard", "https://uizard.io"),
        ("Stitch by Google", "https://stitch.withgoogle.com"), ("Framer AI", "https://www.framer.com/ai/"),
        ("Visme", "https://www.visme.co")]),
    "research": ("research and notes", "#AIResearch", [
        ("NotebookLM", "https://notebooklm.google"), ("Elicit", "https://elicit.com"), ("Consensus", "https://consensus.app"),
        ("Notion AI", "https://www.notion.com/product/ai"), ("Otter.ai", "https://otter.ai"),
        ("Fireflies.ai", "https://fireflies.ai"), ("Granola", "https://www.granola.ai"), ("Scite", "https://scite.ai"),
        ("Semantic Scholar", "https://www.semanticscholar.org"), ("Reflect", "https://reflect.app")]),
    "automation": ("automation and AI agents", "#AIAgents", [
        ("Zapier", "https://zapier.com/ai"), ("Make", "https://www.make.com"), ("n8n", "https://n8n.io"),
        ("Lindy", "https://www.lindy.ai"), ("Relevance AI", "https://relevanceai.com"), ("Manus", "https://manus.im"),
        ("Genspark", "https://www.genspark.ai"), ("Gumloop", "https://www.gumloop.com"), ("Bardeen", "https://www.bardeen.ai"),
        ("Dify", "https://dify.ai"), ("Flowise", "https://flowiseai.com"), ("CrewAI", "https://www.crewai.com")]),
    "local": ("running AI free on your own PC", "#LocalAI", [
        ("Ollama", "https://ollama.com"), ("LM Studio", "https://lmstudio.ai"), ("ComfyUI", "https://www.comfy.org"),
        ("Jan", "https://jan.ai"), ("GPT4All", "https://www.nomic.ai/gpt4all"), ("Open WebUI", "https://openwebui.com"),
        ("AnythingLLM", "https://anythingllm.com"), ("Upscayl", "https://upscayl.org"),
        ("Stability Matrix", "https://lykos.ai"), ("Pinokio", "https://pinokio.co"), ("Whisper", "https://github.com/openai/whisper")]),
}


DEAD_RE = re.compile(r"\b(discontinu\w*|shut(ting)? down|sunset\w*|no longer (available|supported)|deprecat\w*|"
                     r"end of life|has been retired|is closing)\b", re.I)
LOW_INFO_RE = re.compile(r"^(official .{0,40}(site|website)|home( page)?|welcome to\b.{0,40})\.?$", re.I)
SPOKEN_LIMIT = 85  # characters; short, so 10 tools fit the 90 s Reels limit


def shorten(text, limit=SPOKEN_LIMIT):
    """Cut at a natural break (clause, then word) and end with a period: it is read aloud."""
    if len(text) <= limit:
        return text if text.endswith((".", "!", "?")) else text + "."
    cut = text[:limit]
    for sep in (", ", "; ", " - ", " — ", ": "):
        i = cut.rfind(sep)
        if i >= limit * 0.5:
            return cut[:i].rstrip(" ,;:-—") + "."
    words = cut.rsplit(" ", 1)[0].rstrip(" ,;:-—").split()
    while len(words) > 4 and words[-1].lower().strip(",;:") in TRAILING_STOP:  # never end on "of", "the"...
        words.pop()
    return " ".join(words).rstrip(" ,;:-—") + "."


TRAILING_STOP = {"a", "an", "the", "of", "to", "and", "or", "for", "with", "in", "on", "at", "by", "from",
                 "that", "which", "your", "our", "its", "their", "into", "across", "every", "any", "is", "are"}


def describe(name, url):
    """One spoken sentence about the tool, from its own homepage, or None (skip the tool)."""
    desc = page_description(url)
    if not desc or DEAD_RE.search(desc):  # a page announcing a shutdown is not a tool to recommend
        return None
    first = clean_claim(desc.split(". ")[0], limit=400)
    if not first or LOW_INFO_RE.match(first.strip()) or len(re.findall(r"[A-Za-z]{2,}", first)) < 4:
        return None
    return shorten(first.rstrip(". ") if first.endswith("...") else first)


def shows_in_browser(url):
    """Capture the page now (cached for the render): a site behind a bot wall or that fails to load would
    show a block page or nothing in the video, so it is swapped for the next catalog entry. Without
    Chromium (local runs) this check is skipped."""
    from pathlib import Path
    from . import browser
    if not browser.available():
        return True
    assets = Path(config.OUT_DIR) / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    return browser.capture(url, assets, any_site=True, max_css_height=2600) is not None


def next_category(conn):
    """First catalog category not used in the last REUSE_DAYS days."""
    cutoff = time.time() - REUSE_DAYS * 86400
    recent = {r["source"].split(":", 1)[1] for r in conn.execute(
        "SELECT source FROM topics WHERE source LIKE 'tools:%' AND updated_at>=?", (cutoff,))}
    return next((k for k in CATALOG if k not in recent), None)


def build(conn, category, count=None):
    """Verify tools for a category and queue a topic. Returns the topic id or None."""
    count = count or config.TOOLS_PER_VIDEO
    phrase, tag, entries = CATALOG[category]
    tools = []
    for name, url in entries:
        if len(tools) >= count:
            break
        try:
            if not url_ok(url):
                log.info("tool %s: site not reachable, skipped", name)
                continue
            desc = describe(name, url)
            if desc and not shows_in_browser(url):
                log.info("%s: page cannot be shown (bot protection or load failure), skipped", name)
                continue
            if not desc:
                log.info("tool %s: no site description, skipped", name)
                continue
            tools.append({"name": name, "url": url, "description": desc})
        except Exception as e:
            log.info("tool %s skipped: %s", name, e)
    if len(tools) < min(count, 5):
        log.warning("only %d verified tools for %s; not queued", len(tools), category)
        return None
    n = len(tools)
    tid = db.sha(f"tools:{category}:{db.today()}")[:16]
    claims = [{"text": f"{t['name']}: {t['description']}", "source": t["url"]} for t in tools]
    conn.execute(
        """INSERT OR IGNORE INTO topics (id,title,source,url,published_at,discovered_at,content_hash,raw,summary,
        claims,source_urls,score,confidence,status,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (tid, f"{n} AI tools for {phrase}", f"tools:{category}", tools[0]["url"], db.now_iso(), db.now_iso(),
         db.sha(f"tools:{category}:{db.today()}"), db.js({"category": category, "phrase": phrase, "hashtag": tag,
                                                          "tools": tools}),
         f"{n} AI tools for {phrase}", db.js(claims), db.js([t["url"] for t in tools]), 90, 100, "QUEUED",
         time.time()))
    return tid


def run(conn):
    """Queue one tools video if none is waiting and today's tools quota is not used yet."""
    if config.DAILY_TOOLS_VIDEOS <= 0:
        return 0
    waiting = conn.execute(
        "SELECT COUNT(*) FROM topics WHERE source LIKE 'tools:%' AND status IN "
        "('QUEUED','GENERATING','RENDERING','QC','READY','RETRY')").fetchone()[0]
    if waiting:
        return 0
    cat = next_category(conn)
    if not cat:
        log.info("all tool categories used in the last %d days", REUSE_DAYS)
        return 0
    return 1 if build(conn, cat) else 0
