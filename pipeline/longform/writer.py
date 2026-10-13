"""Long-video script: chapters of spoken paragraphs written by Claude from the real material only.

Each paragraph cites one visual id (page section, code block, stats), so the picture always matches the
words. Scripts with numbers that are not in the material, unknown visual ids or a length far from the
target are rejected (one retry with the problems listed), never published.
"""
import json
import logging
import os
import re

from .. import config
from ..generate import numbers

log = logging.getLogger("longform.writer")
MODEL = os.environ.get("LONG_MODEL", "claude-opus-5-5")
PRICE = {"claude-opus-5-5": (4.0, 20.0)}
WORDS_PER_MIN = 118  # measured: the cloned voice at VOICE_SPEED 1.1, with paragraph pauses
MIN_WORDS, MAX_WORDS = 980, 1650  # about 8 to 15 minutes including chapter cards

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "chapters": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "paragraphs": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}, "visual": {"type": "integer"}},
                    "required": ["text", "visual"], "additionalProperties": False}},
            },
            "required": ["title", "paragraphs"], "additionalProperties": False}},
    },
    "required": ["title", "description", "tags", "chapters"],
    "additionalProperties": False,
}

SYSTEM = """You write narration scripts for long-form YouTube explainer videos on an AI and open-source channel.
The narration is read aloud by a text-to-speech voice over screen footage of the pages being discussed.

Accuracy rules (most important):
- Use only facts stated in the MATERIAL. Never add features, numbers, prices, dates, comparisons, benchmarks,
  user counts or opinions presented as facts. If the material does not say it, leave it out.
- Never invent commands or code. When a paragraph is about a code visual, explain what that code does in plain
  words; do not read it character by character.
- Do not claim something is free, open source, private, safe or legal unless the material says so.

Writing rules:
- Friendly, clear spoken English for curious viewers who are not experts. Short sentences. No markdown, no
  emojis, no lists, no URLs, no stage directions.
- Explain jargon briefly, using the material.
- Chapters: an introduction first (open with a question about the viewer's need, then say what the video
  covers), content chapters next, a summary last (recap the key points, say the links are in the description,
  ask viewers to comment which part was most useful, and to subscribe).
- Each paragraph is 30 to 70 words and cites exactly one visual id from the VISUALS list that shows what the
  paragraph talks about. Move through the visuals in a natural order; reuse an overview visual for general
  paragraphs.
- Title: at most 90 characters, starts with the subject's name or keyword, no clickbait beyond the material.
- Description: two or three sentences summarising the video. Tags: up to 12 search keywords."""

BRIEFS = {
    "github": ("A complete walkthrough of one GitHub repository: what it is, the problem it solves, its main "
               "features, how it works, how to install it and how to use it, and who it is for. Cover only the "
               "parts the README actually explains."),
    "tools": ("A full review of the AI tools below, one chapter per tool, in the order given: what each tool is "
              "and what its own website says it does. No rankings and no claims of 'best'."),
    "sites": ("A full tour of the websites below, one chapter per website, in the order given, in the series "
              "'Websites that feel illegal to know' (they are all legal; say so once in the introduction). "
              "Explain what each site lets you do, section by section, from its own pages."),
}


def target_words(material_words):
    return max(MIN_WORDS, min(MAX_WORDS - 50, int(material_words * 0.5)))


def _prompt(m, target, problems=None):
    vis = "\n".join(f"[{v['id']}] {v['label']}" + (f"\n    {v['text'][:900]}" if v.get("text") else "")
                    for v in m["visuals"])
    extra = ("\n\nYOUR PREVIOUS DRAFT WAS REJECTED. Fix these problems:\n- " + "\n- ".join(problems)) if problems else ""
    return (f"VIDEO: {BRIEFS[m['format']]}\nSUBJECT: {m['subject']}\n"
            f"TARGET LENGTH: about {target} words in total (between {int(target * 0.9)} and {int(target * 1.1)}).\n\n"
            f"MATERIAL:\n{m['facts']}\n\nVISUALS (cite these ids):\n{vis}{extra}")


def validate(script, m, target):
    """List of problems (empty = publishable)."""
    problems = []
    ids = {v["id"] for v in m["visuals"]}
    paras = [p for ch in script.get("chapters", []) for p in ch.get("paragraphs", [])]
    if len(script.get("chapters", [])) < 3:
        problems.append("use at least 3 chapters")
    bad = sorted({p["visual"] for p in paras} - ids)
    if bad:
        problems.append(f"visual ids {bad} do not exist")
    text = " ".join([script.get("title", "")] + [p["text"] for p in paras])
    n_words = len(re.findall(r"[A-Za-z0-9']+", text))
    if not 0.9 * target <= n_words <= 1.15 * target:
        problems.append(f"the script has {n_words} words; it must be close to {target}")
    source = m["facts"] + " " + m["subject"] + " " + " ".join(v.get("text", "") + " " + v["label"] for v in m["visuals"])
    invented = numbers(text) - numbers(source)
    if invented:
        problems.append(f"these numbers are not in the material: {sorted(invented)}; remove them")
    if re.search(r"https?://|www\.", text):
        problems.append("do not include URLs in the narration")
    return problems


def _call(prompt):
    import anthropic
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    # fallbacks="default": if a safety classifier declines, the API re-runs the request on Anthropic's
    # recommended fallback model instead of returning a refusal
    with client.beta.messages.stream(
        model=MODEL, max_tokens=32000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        system=SYSTEM, messages=[{"role": "user", "content": prompt}],
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
    ) as stream:
        msg = stream.get_final_message()
    pin, pout = PRICE.get(MODEL, (4.0, 20.0))
    cost = (msg.usage.input_tokens * pin + msg.usage.output_tokens * pout) / 1e6
    if msg.stop_reason in ("refusal", "max_tokens"):
        log.warning("long script stopped: %s", msg.stop_reason)
        return None, cost
    text = next((b.text for b in msg.content if b.type == "text"), "")
    return json.loads(text), cost


def write(m):
    """Validated script dict, or None. Needs ANTHROPIC_API_KEY."""
    if not config.ANTHROPIC_API_KEY:
        log.warning("ANTHROPIC_API_KEY not set; no long video")
        return None
    target = target_words(m["material_words"])
    problems, spent = None, 0.0
    for attempt in range(2):
        try:
            script, cost = _call(_prompt(m, target, problems))
        except Exception as e:
            log.warning("long script request failed: %s", e)
            return None
        spent += cost
        if not script:
            return None
        problems = validate(script, m, target)
        if not problems:
            log.info("long script for %s: %d chapters, $%.3f", m["subject"], len(script["chapters"]), spent)
            script["cost_usd"] = round(spent, 4)
            return script
        log.warning("long script attempt %d rejected: %s", attempt + 1, problems)
    return None
