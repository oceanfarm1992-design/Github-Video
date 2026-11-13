"""Free long-video script (no AI): the narration is the pages' own text, read in order.

Same shape as the Claude writer's script (chapters of paragraphs, each citing a visual), so render and publish
do not change. An intro, one chapter per item (its homepage description, then its page sections), and a
recap. Every sentence comes from the material, so there is nothing invented to check.
"""
import re

from .writer import MAX_WORDS, validate

MIN_SCRIPT_WORDS = 700    # shorter than about 6 minutes: no long video that day
PARA_WORDS = 55           # the voice model reads one paragraph at a time (about 512 sound units at most)
SENTENCE_WORDS = 28       # longer sentences (lists run together) are split at commas or cut
URL_RE = re.compile(r"https?://\S+|www\.\S+")


def words(text):
    return len(re.findall(r"[A-Za-z0-9']+", text or ""))


def clean(text):
    """Speakable text: no links, no markdown or symbols the voice cannot say; '' when mostly not English."""
    text = URL_RE.sub("", text or "")
    text = re.sub(r"[`*_#>|\[\]{}]", " ", text)
    text = text.replace("&", " and ").replace("→", ", ").replace("•", ". ")
    kept = "".join(ch for ch in text if ch.isascii() or ch in "—–’‘“”…")
    if len(kept.strip()) < 0.8 * len(text.strip()):
        return ""
    kept = re.sub(r"\s+", " ", kept).strip(" -:;,")
    return kept if not kept or kept[-1] in ".!?" else kept + "."


def sentences(text):
    out = []
    for s in (x.strip() for x in re.split(r"(?<=[.!?])\s+", text)):
        while s and words(s) > SENTENCE_WORDS:  # split a run-on at a comma/semicolon, else at the word limit
            ws = s.split()
            cut = next((i for i in range(SENTENCE_WORDS, 8, -1) if ws[i - 1].endswith((",", ";", ":"))), SENTENCE_WORDS)
            out.append(" ".join(ws[:cut]).rstrip(",;:") + ".")
            s = " ".join(ws[cut:])
            s = s[:1].upper() + s[1:]
        if s:
            out.append(s)
    return out


def take(text, limit):
    """Whole sentences up to `limit` words."""
    out, n = [], 0
    for s in sentences(text):
        w = words(s)
        if out and n + w > limit:
            break
        out.append(s)
        n += w
    return " ".join(out)


def items_of(m):
    """[(name, overview visual, [section visuals])] in page order."""
    out = {}
    for v in m["visuals"]:
        if v.get("kind") == "overview":
            out[v["page"]] = (v["label"].removesuffix(" homepage"), v, [])
        elif v.get("kind") == "page" and v.get("page") in out:
            out[v["page"]][2].append(v)
    return [out[k] for k in sorted(out, key=lambda p: next(i for i, v in enumerate(m["visuals"]) if v.get("page") == p))]


def github_meta(m):
    """{name: (rank, stars)} from the Top 10 material."""
    return {name: (int(rank), stars) for rank, name, stars in
            re.findall(r"RANK (\d+): (\S+) \([^)]*\)\nStars: ([\d,]+)", m["facts"])}


def phrase_of(m):
    s = m["subject"]
    return s.split("AI tools for ", 1)[1].split(" (")[0] if "AI tools for " in s else s


def intro(m, names):
    n, fmt = len(names), m["format"]
    if fmt == "github_top10":
        return (f"These are the top {n} new GitHub repositories of this week, ranked by their GitHub stars. "
                f"We count down from number {n} to number one, and for each one we look at its own page.")
    if fmt == "pdf_tour":
        group = m["subject"].split(": ", 1)[-1]
        return (f"This is a full tour of the {group} tools on Privacy PDF Tools. We go through {n} tools, "
                f"one by one, using each tool's own page.")
    who = "Parents and teachers, " if fmt == "kids" else ""
    return (f"{who}are you looking for AI tools for {phrase_of(m)}? In this video we look at {n} of them, "
            f"one by one, using each tool's own website: " + ", ".join(names[:-1]) + f" and {names[-1]}.")


def outro(names):
    return ("That's the full list: " + ", ".join(names[:-1]) + f" and {names[-1]}. All the links are in the "
            "description. Tell me in the comments which one was the most useful for you, and subscribe for more.")


def title_of(m, n):
    fmt, phrase = m["format"], phrase_of(m)
    if fmt == "github_top10":
        return f"Top {n} New GitHub Repos This Week (Ranked by Stars)"
    if fmt == "pdf_tour":
        return f"{m['subject']} - Full Tour, Tool by Tool"
    if fmt == "kids":
        return f"{n} AI Tools for {phrase[0].upper() + phrase[1:]} - Full Guide for Parents and Teachers"[:100]
    if fmt == "everyday":
        return f"{n} Easy AI Tools for {phrase[0].upper() + phrase[1:]} - Explained Simply"[:100]
    return f"{n} AI Tools for {phrase[0].upper() + phrase[1:]} - Explained One by One"[:100]


def write(m):
    """Script dict from the pages' own text, or None when there is not enough of it for a long video."""
    items = items_of(m)
    if len(items) < 3:
        return None
    gh = github_meta(m) if m["format"] == "github_top10" else {}
    per_item = max(120, (MAX_WORDS - 160) // len(items))
    chapters = []
    names = [n.split("/")[-1] if gh else n for n, _, _ in items]
    for (name, over, secs), short in zip(items, names):
        desc = clean(over.get("text", ""))
        if gh and name in gh:
            rank, stars = gh[name]
            lead = f"Number {rank}: {short}, with {stars} stars on GitHub. {desc}"
            title = f"#{rank} {short}"
        else:
            lead, title = f"{short}. {desc}", short
        paras = [{"text": take(lead, PARA_WORDS), "visual": over["id"]}]
        budget = per_item - words(paras[0]["text"])
        for v in secs:
            text = clean(v.get("text", ""))
            heading = v["label"].split("section '", 1)[-1].rstrip("'")
            if not text or budget < 30:
                continue
            body = take(text if text.lower().startswith(heading.lower()) else f"{heading}. {text}",
                        min(PARA_WORDS, budget))
            if words(body) < 12:
                continue
            paras.append({"text": body, "visual": v["id"]})
            budget -= words(body)
        chapters.append({"title": title[:60], "paragraphs": paras})
    first = items[0][1]["id"]
    chapters.insert(0, {"title": "Introduction", "paragraphs": [{"text": intro(m, names), "visual": first}]})
    chapters.append({"title": "Summary", "paragraphs": [{"text": outro(names), "visual": first}]})
    script = {"title": title_of(m, len(items)),
              "description": f"{m['subject']}: " + ", ".join(names) + ". Every description comes from the "
                             "tool's or project's own page.",
              "tags": [m["subject"][:60]] + [n[:60] for n in names][:10], "chapters": chapters, "cost_usd": 0.0}
    total = sum(words(p["text"]) for ch in chapters for p in ch["paragraphs"])
    # page text only (intro/outro are fixed sentences with counts): no numbers or links that are not on the pages
    body = {"title": "", "chapters": chapters[1:-1]}
    problems = [p for p in validate(body, m, total) if "words" not in p and "chapters" not in p]
    if total < MIN_SCRIPT_WORDS or problems:
        return None
    return script
