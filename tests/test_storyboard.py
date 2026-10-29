import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image  # noqa: E402

from pipeline import db, generate, ghdata, motion, music, ranking, recap, render, storyboard, theme, visuals  # noqa: E402

GH = {"name": "a/box", "stars": 3075, "forks": 211, "language": "Python", "license": "MIT",
      "pushed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "created_at": "2026-10-02T09:57:40Z",
      "install": ["pip install box"], "history": []}


class ThemeTests(unittest.TestCase):
    def test_series_of_every_source(self):
        self.assertEqual(theme.kind("github"), "github")
        self.assertEqual(theme.kind("tools:video"), "tools")
        self.assertEqual(theme.kind("ranking:2026-W41"), "ranking")
        self.assertEqual(theme.kind("recap:2026-W41"), "recap")
        self.assertEqual(theme.kind("rss:openai"), "news")
        self.assertEqual(generate.kind("ranking:2026-W41"), "ranking")

    def test_each_series_has_its_own_colours_and_music(self):
        accents = {t.accent for k, t in theme.THEMES.items() if k in ("github", "tools", "sites", "promo")}
        self.assertEqual(len(accents), 4)
        for t in theme.THEMES.values():
            self.assertIn(t.music, music.FLAVORS)


class StoryboardTests(unittest.TestCase):
    def test_github_intro_uses_real_stars_and_age(self):
        it = storyboard.intro("github", {"raw": "{}"}, "a/box", None, GH)
        self.assertTrue(it["title"].startswith("3.1k stars in "))
        self.assertIn("PYTHON", it["tag"])
        self.assertEqual(it["sub"], "a/box")

    def test_intro_without_data_falls_back_to_the_name(self):
        it = storyboard.intro("github", {"raw": "{}"}, "a/box", None, {})
        self.assertEqual(it["title"], "box")

    def test_endcards_promise_only_what_the_schedule_delivers(self):
        old = storyboard.config.DAILY_SITES_VIDEOS
        storyboard.config.DAILY_SITES_VIDEOS = 1
        try:
            sites = storyboard.endcard("sites", {"raw": json.dumps({"part": 3})})
            self.assertEqual(sites["line"], "Part 4 tomorrow")
        finally:
            storyboard.config.DAILY_SITES_VIDEOS = old
        tools = storyboard.endcard("tools", {"raw": "{}"}, 7)
        self.assertEqual(tools["options"], [str(i) for i in range(1, 8)])
        storyboard.config.DAILY_SITES_VIDEOS = 0
        try:
            self.assertIsNone(storyboard.endcard("sites", {"raw": "{}"})["line"])
        finally:
            storyboard.config.DAILY_SITES_VIDEOS = old

    def test_github_visuals_keep_the_page_first(self):
        vis = storyboard.github_visuals(5, GH)
        self.assertEqual([v and v[0] for v in vis], [None, None, "stats", "terminal", None])
        self.assertEqual(storyboard.github_visuals(3, GH), [None, None, None])  # one middle scene: the page


class DataTests(unittest.TestCase):
    def test_star_history_needs_real_samples(self):
        conn = db.connect(":memory:")
        self.assertEqual(ghdata.star_history(conn, "a/box", 100), [])
        now = time.time()
        for t, s in ((now - 3 * 86400, 20), (now - 86400, 60)):
            conn.execute("INSERT INTO star_snapshots VALUES (?,?,?)", ("a/box", t, s))
        h = ghdata.star_history(conn, "a/box", 100, "2026-10-01T00:00:00Z")
        self.assertEqual(h[0][1], 0)
        self.assertEqual(h[-1][1], 100)
        self.assertEqual([s for _, s in h], sorted(s for _, s in h))

    def test_record_stars_is_throttled(self):
        conn = db.connect(":memory:")
        ghdata.record_stars(conn, "a/box", 10)
        ghdata.record_stars(conn, "a/box", 11)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM star_snapshots").fetchone()[0], 1)

    def test_install_command_regex(self):
        for ok in ("pip install box", "npx skills add a/b", "git clone https://github.com/a/b", "uvx box"):
            self.assertTrue(ghdata.INSTALL_RE.match(ok), ok)
        for bad in ("python main.py", "export OPENAI_API_KEY=sk", "cd box"):
            self.assertFalse(ghdata.INSTALL_RE.match(bad) and not ghdata.SECRET_RE.search(bad), bad)

    def test_ranking_descriptions_are_readable_english(self):
        self.assertEqual(ranking.clean("A fast spreadsheet in pure Rust"), "A fast spreadsheet in pure Rust.")
        self.assertIsNone(ranking.clean("让智能体用网页回答问题"))
        self.assertIsNone(ranking.clean("tiny"))
        self.assertLessEqual(len(ranking.clean("word " * 60)), 104)

    def test_ranking_script_counts_down_with_real_stars(self):
        items = [{"name": "o/c", "url": "https://github.com/o/c", "description": "C.", "stars": 900, "rank": 3},
                 {"name": "o/b", "url": "https://github.com/o/b", "description": "B.", "stars": 2000, "rank": 2},
                 {"name": "o/a", "url": "https://github.com/o/a", "description": "A.", "stars": 5000, "rank": 1}]
        hook, beats, links = generate.ranking_script({"raw": json.dumps({"tools": items})})
        self.assertIn("Top 3", hook)
        self.assertTrue(beats[0].startswith("Number 3: c.") and beats[-1].endswith("5,000 stars."))
        self.assertTrue(links.splitlines()[1].startswith("1. o/a"))

    def test_recap_needs_enough_videos(self):
        conn = db.connect(":memory:")
        few = [{"title": "t", "asset": "a.mp4", "kind": "github", "url": "https://x"}] * 3
        self.assertIsNone(recap.build(conn, few))
        tid = recap.build(conn, few * 2)
        self.assertTrue(tid)
        c = conn.execute("SELECT * FROM contents WHERE topic_id=?", (tid,)).fetchone()
        self.assertEqual(c["cta_keyword"], "LINK")
        self.assertIsNone(recap.build(conn, few * 2))  # once per week


class AudioTests(unittest.TestCase):
    def test_music_follows_the_edit(self):
        expr = render.music_automation([(0.0, "impact"), (20.0, "cta"), (24.0, "end")])
        self.assertIn("0.12", expr)        # drop-out before the call to action
        self.assertIn("gte(t,24.000)", expr)  # carries the end card
        self.assertEqual(render.music_automation([]), "volume='1':eval=frame")

    def test_flavours_render(self):
        import tempfile
        d = tempfile.mkdtemp()
        self.assertNotEqual(Path(music.track("hacker", 5, d, "tools")).name, Path(music.track("hacker", 5, d)).name)


class FramesTests(unittest.TestCase):
    def test_intro_and_endcard_wrap_the_scenes(self):
        ev = []
        th = theme.THEMES["tools"]
        gen = motion.frames([("Here are 3 tools.", 1.0), ("Comment TOOL now.", 1.0)], events=ev, theme=th,
                            intro={"title": "3 AI tools for video", "sub": None},
                            endcard={"question": "Which one?", "options": ["1", "2", "3"], "line": None})
        n = sum(1 for _ in gen)
        self.assertEqual(n, round((2.0 + visuals.INTRO + visuals.ENDCARD) * motion.FPS))
        kinds = [k for _, k in ev]
        self.assertIn("cta", kinds)
        self.assertIn("end", kinds)

    def test_cover_frame_is_complete_at_frame_zero(self):
        th = theme.THEMES["github"]
        gen = motion.frames([("Hello.", 1.0), ("Bye.", 1.0)], theme=th, intro={"title": "3.1k stars in 10 days"})
        first = Image.frombytes("RGB", (motion.W, motion.H), next(gen))
        bright = sum(1 for p in first.crop((100, 800, 980, 1000)).getdata() if sum(p) > 600)
        self.assertGreater(bright, 2000)  # the headline is already on screen (it is the cover)

    def test_panels_render(self):
        th = theme.THEMES["github"]
        for p in (visuals.StatsPanel(GH, th, 800), visuals.TermPanel(["pip install box"], th, 800),
                  visuals.PodiumPanel([{"name": "o/a", "stars": 5}, {"name": "o/b", "stars": 3}], th, 800)):
            self.assertEqual(p.frame(2.5).size, (visuals.CARD_W, 800))


if __name__ == "__main__":
    unittest.main()
