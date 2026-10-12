"""Series branding: one colour system per series, so a viewer knows the series from the first frame.

github  - terminal green on near-black, GitHub blue for numbers and names
tools   - electric purple
sites   - "secret files": black and red, amber highlights
promo   - Privacy PDF Tools: calm sky blue and green
ranking - weekly GitHub top 5: green with gold
recap   - weekly recap: blue with gold
news    - the original navy and gold
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    name: str
    bg_top: tuple
    bg_bot: tuple
    accent: tuple  # progress bar, grid, particles, chart line, pills
    now: tuple     # the word being spoken
    emph: tuple    # numbers, repo names, ALL-CAPS words
    edge: tuple    # outlines, wipe edges, labels
    tag: str       # series tag on the hook frame and end card
    music: str     # hacker-music variation (music.FLAVORS)


THEMES = {
    "github": Theme("github", (5, 12, 9), (14, 34, 26), (63, 185, 80), (126, 231, 135), (88, 166, 255),
                    (63, 185, 80), "OPEN SOURCE", "github"),
    "tools": Theme("tools", (13, 7, 28), (42, 22, 76), (168, 85, 247), (240, 171, 252), (125, 211, 252),
                   (192, 132, 252), "AI TOOLS", "tools"),
    "sites": Theme("sites", (8, 6, 8), (36, 14, 16), (239, 68, 68), (251, 191, 36), (248, 113, 113),
                   (239, 68, 68), "SECRET WEBSITES", "sites"),
    "promo": Theme("promo", (5, 15, 27), (12, 42, 64), (56, 189, 248), (125, 211, 252), (52, 211, 153),
                   (56, 189, 248), "PRIVACY PDF TOOLS", "promo"),
    "ranking": Theme("ranking", (5, 12, 9), (16, 32, 24), (63, 185, 80), (255, 214, 102), (88, 166, 255),
                     (255, 214, 102), "WEEKLY TOP 5", "ranking"),
    "recap": Theme("recap", (8, 12, 26), (24, 36, 70), (90, 160, 255), (255, 214, 102), (125, 211, 252),
                   (255, 214, 102), "THIS WEEK", "recap"),
    "news": Theme("news", (14, 16, 28), (44, 48, 72), (90, 160, 255), (255, 214, 102), (110, 180, 255),
                  (255, 214, 102), "AI NEWS", "github"),
}
DEFAULT = THEMES["news"]


def kind(source):
    """Series of a topic source (generate.kind plus the weekly formats)."""
    source = str(source or "")
    if source == "github":
        return "github"
    for k in ("tools", "promo", "sites", "ranking", "recap"):
        if source.startswith(k + ":"):
            return k
    return "news"


def for_source(source):
    return THEMES.get(kind(source), DEFAULT)
