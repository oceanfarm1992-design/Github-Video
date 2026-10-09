"""SEO for every platform: keyword-first titles, searchable descriptions, YouTube tags and series playlists.

Everything is built from text the pipeline already verified (hook, beats, the site's own description,
GitHub topics); nothing new is claimed here.
"""
import json
import logging
import re

log = logging.getLogger("seo")

TITLE_MAX = 92  # YouTube allows 100; " #Shorts" is appended

SERIES = {
    "github": {
        "keywords": ["open source", "GitHub", "AI", "GitHub projects", "open source AI", "developer tools",
                     "programming", "coding"],
        "about": "Daily open-source AI and GitHub projects worth knowing. Follow for a new find every day.",
        "playlist": "Open-Source GitHub Finds",
    },
    "tools": {
        "keywords": ["AI tools", "best AI tools", "AI websites", "artificial intelligence", "AI apps",
                     "productivity", "tech tips"],
        "about": "The best AI tools, explained one by one. Follow for a new list every week.",
        "playlist": "AI Tools You Should Know",
    },
    "sites": {
        "keywords": ["websites that feel illegal to know", "useful websites", "websites you should know",
                     "cool websites", "free websites", "internet tips", "tech tips"],
        "about": "Websites that feel illegal to know (all 100% legal). Follow for the next part.",
        "playlist": "Websites That Feel Illegal to Know",
    },
    "promo": {
        "keywords": ["PDF tools", "PDF online", "Privacy PDF Tools", "edit PDF", "PDF editor", "productivity",
                     "privacy"],
        "about": "Privacy PDF Tools: PDF tools that keep your files private. One tool explained every day.",
        "playlist": "Privacy PDF Tools Tutorials",
    },
    "news": {
        "keywords": ["AI news", "artificial intelligence", "tech news"],
        "about": "AI and open-source news in under a minute.",
        "playlist": "AI News",
    },
}

NUM_RE = re.compile(r"^\d+\. ")
AI_VOICE ="Narrated with an AI clone of the creator's own voice."


def kind_of(c):
    from .generate import kind
    return kind(c.get("tsource") or "")


def _raw(c):
    try:
        return json.loads(c.get("traw") or "{}")
    except ValueError:
        return {}


def _beats(c):
    try:
        return [b for b in json.loads(c.get("script") or "{}").get("beats", []) if b]
    except ValueError:
        return []


def _fit(options, limit=TITLE_MAX):
    for o in options:
        o = re.sub(r"\s+", " ", o or "").strip()
        if o and len(o) <= limit:
            return o
    o = re.sub(r"\s+", " ", options[-1] or "").strip()
    return o[:limit - 3].rsplit(" ", 1)[0] + "..." if len(o) > limit else o


def _clip(text, n):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[:n - 1].rsplit(" ", 1)[0] + "..."


def subject(c):
    """The searchable name of what the video shows."""
    k, raw, title = kind_of(c), _raw(c), c.get("title") or ""
    if k == "github":
        return title.split("/")[-1]
    if k == "sites":
        items = raw.get("tools") or []
        return items[0]["name"] if len(items) == 1 else title.split(": ")[-1]
    if k == "promo":
        return (raw.get("tool") or {}).get("name") or title.split(" - ")[0]
    if k == "tools":
        return f"AI tools for {raw.get('phrase', '')}".strip()
    return title


def title(c):
    """Keyword-first title: the subject's name is in it, so search can match it."""
    k, raw, hook = kind_of(c), _raw(c), (c.get("hook") or c.get("title") or "").strip()
    name = subject(c)
    if k == "github":
        opts = [f"{name} on GitHub: {hook}", f"{name}: {hook}", f"{name}: {_clip(hook, TITLE_MAX - len(name) - 2)}"]
    elif k == "tools":
        n = len(raw.get("tools") or [])
        phrase = raw.get("phrase", "").title()
        opts = [f"{n} AI Tools for {phrase} You Should Know", f"{n} AI Tools for {phrase}", hook]
    elif k == "sites":
        opts = [f"Websites That Feel Illegal to Know - Part {raw.get('part', 1)}: {name}", hook]
    elif k == "promo":
        private = any("never uploaded" in b.lower() for b in _beats(c))  # the site's own privacy line
        opts = [f"{hook} | {name} Online"]
        if private:
            opts.append(f"{name} Online Without Uploading Files | Privacy PDF Tools")
        opts += [f"{name} Online | Privacy PDF Tools", hook]
    else:
        opts = [hook]
    if k == "github" and name.lower() in hook.lower():
        opts.insert(0, hook)
    return _fit(opts)


def summary(c):
    """One keyword-rich sentence for the top of a description or caption (from verified text only)."""
    k, raw, beats = kind_of(c), _raw(c), _beats(c)
    if k == "tools":
        names = [t["name"] for t in raw.get("tools", [])]
        if names:
            more = " and more" if len(names) > 3 else ""
            return f"AI tools for {raw.get('phrase', '')}: {', '.join(names[:3])}{more}."
    if not beats:
        return ""
    first = beats[1] if k == "promo" and len(beats) > 1 else beats[0]  # promo beat 0 is "Meet X"
    if k == "sites":  # "Name. Name is a ..." -> "Name is a ..."
        name = subject(c)
        if first.startswith(name + ". "):
            first = first[len(name) + 2:]
    return _clip(first, 160)


def hashtags(c, limit=5):
    try:
        tags = json.loads(c.get("hashtags") or "[]")
    except ValueError:
        tags = []
    return tags[:limit]


def youtube_description(c, links=None):
    """Hook + summary up top (what search and the Shorts feed show), the points covered, links, series
    blurb, AI-voice disclosure, then hashtags (YouTube shows the first three above the title)."""
    k, beats = kind_of(c), _beats(c)
    q = (c.get("caption") or "").split("\n\n")
    parts = [c.get("hook") or c.get("title") or "", summary(c)]
    points = beats[1:5] if k != "tools" else [b for b in beats[:10]]
    if points:
        lines = ["- " + _clip(NUM_RE.sub("", b), 110) for b in points]
        parts.append("In this Short:\n" + "\n".join(lines))
    if len(q) > 1 and q[1] and not q[1].startswith("Source:"):
        parts.append(q[1])  # the engagement question
    if links:
        parts.append(links)
    s = SERIES.get(k, SERIES["news"])
    parts.append(s["about"])
    if k == "sites":
        parts.append("For educational purposes only.")
    parts.append(AI_VOICE)
    parts.append(" ".join(["#Shorts"] + hashtags(c, 7)))
    return "\n\n".join(p.strip() for p in parts if p and p.strip())[:4900]


def youtube_tags(c, budget=450):
    """snippet.tags: subject, series keywords, GitHub topics / tool names, hashtag words (<= ~450 chars)."""
    k, raw = kind_of(c), _raw(c)
    name = subject(c)
    cand = [name, c.get("title") or ""]
    if k == "github":
        cand += [f"{name} github", f"{name} open source"] + [t.replace("-", " ") for t in raw.get("topics", [])]
        if raw.get("language"):
            cand.append(str(raw["language"]))
    elif k == "tools":
        cand += [f"best AI tools for {raw.get('phrase', '')}", f"AI {raw.get('phrase', '')}"]
        cand += [t["name"] for t in raw.get("tools", [])]
    elif k == "sites":
        cand += [f"{name} website", f"websites that feel illegal to know part {raw.get('part', 1)}"]
    elif k == "promo":
        cand += [f"{name} online", f"{name} free", f"how to {name.lower()}"]
    cand += SERIES.get(k, SERIES["news"])["keywords"]
    cand += [h.lstrip("#") for h in hashtags(c, 10)]
    out, seen, used = [], set(), 0
    for t in cand:
        t = re.sub(r"[<>\",]", "", t or "").strip()
        key = t.lower()
        if not t or len(t) > 60 or key in seen:
            continue
        cost = len(t) + (2 if " " in t else 0) + 1
        if used + cost > budget:
            continue
        out.append(t)
        seen.add(key)
        used += cost
    return out


def social_caption(c, cta_line=""):
    """Instagram/Facebook/TikTok: search reads captions, so the subject and a keyword sentence come first;
    no links (they kill comments); 3-5 hashtags."""
    q = (c.get("caption") or "").split("\n\nSource:")[0].split("\n\n")
    hook, question = q[0].strip(), (q[1].strip() if len(q) > 1 else "")
    s = summary(c)
    body = [hook]
    if s and s.lower() not in hook.lower():
        body.append(s)
    if question:
        body.append(question)
    if cta_line:
        body.append(cta_line)
    body.append(" ".join(hashtags(c)))
    return "\n\n".join(b for b in body if b)


# --------------------------------------------------------------- playlists
def playlist_id(conn, token, k):
    """The series playlist (found by title or created once; id cached in kv). Playlists keep viewers
    watching the next video, which is a strong ranking signal."""
    from . import db
    from .http import request
    name = SERIES.get(k, {}).get("playlist")
    if not name:
        return None
    cached = db.kv_get(conn, f"yt_playlist:{k}")
    if cached:
        return cached
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    _, _, r = request("https://www.googleapis.com/youtube/v3/playlists?part=snippet&mine=true&maxResults=50",
                      headers=h)
    pid = next((p["id"] for p in json.loads(r).get("items", []) if p["snippet"]["title"] == name), None)
    if not pid:
        body = {"snippet": {"title": name, "description": SERIES[k]["about"], "defaultLanguage": "en"},
                "status": {"privacyStatus": "public"}}
        _, _, r = request("https://www.googleapis.com/youtube/v3/playlists?part=snippet,status",
                          data=json.dumps(body).encode(), method="POST", headers=h)
        pid = json.loads(r)["id"]
    db.kv_set(conn, f"yt_playlist:{k}", pid)
    return pid


def add_to_playlist(conn, token, c, video_id):
    from .http import request
    try:
        pid = playlist_id(conn, token, kind_of(c))
        if not pid:
            return False
        body = {"snippet": {"playlistId": pid, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}
        request("https://www.googleapis.com/youtube/v3/playlistItems?part=snippet", data=json.dumps(body).encode(),
                method="POST", headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        return True
    except Exception as e:  # never block a publish on a playlist
        log.warning("playlist add failed: %s", e)
        return False
