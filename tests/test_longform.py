import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import db, longform  # noqa: E402
from pipeline.longform import material, publish, render, writer  # noqa: E402

M = {"format": "github", "subject": "a/box", "key": "github:a/box", "links": [("a/box", "https://github.com/a/box")],
     "facts": "Repository: a/box\nStars: 3,075\nREADME:\nbox builds things fast. Install with pip.",
     "visuals": [{"id": 0, "kind": "overview", "page": 0, "label": "page"},
                 {"id": 1, "kind": "code", "label": "code", "text": "pip install box",
                  "code": {"lang": "bash", "lines": ["pip install box"]}}],
     "material_words": 2000}


def script(words_per_para=40, n=6, visual=0, extra=""):
    para = " ".join(["word"] * words_per_para) + extra
    return {"title": "box explained", "description": "d", "tags": ["box"],
            "chapters": [{"title": f"C{i}", "paragraphs": [{"text": para, "visual": visual}] * 5} for i in range(n)]}


class ScheduleTests(unittest.TestCase):
    def test_schedule_parsing(self):
        with mock.patch.object(longform.config, "LONG_SCHEDULE", "0:github, 2:tools,4:sites,9:bad,1:nope"):
            self.assertEqual(longform.schedule(), {0: "github", 2: "tools", 4: "sites"})
        with mock.patch.object(longform.config, "LONG_SCHEDULE", ""):
            self.assertEqual(longform.schedule(), {})

    def test_every_five_days_rotating_formats(self):
        import datetime as d
        with mock.patch.object(longform.config, "LONG_EVERY_DAYS", 5), \
             mock.patch.object(longform.config, "LONG_START", "2026-11-01"), \
             mock.patch.object(longform.config, "LONG_FORMATS", "github,tools,sites"),              mock.patch.object(longform.config, "CHANNELS_ENABLED", ""):  # main channel only (see test_channels)
            got = {d.date(2026, 11, 1) + d.timedelta(days=i): longform.format_today(d.date(2026, 11, 1) + d.timedelta(days=i))
                   for i in range(16)}
            self.assertEqual([v for v in got.values() if v], ["github", "tools", "sites", "github"])
            self.assertEqual([k.day for k, v in got.items() if v], [1, 6, 11, 16])
            self.assertIsNone(longform.format_today(d.date(2026, 10, 31)))

    def test_only_the_first_short_of_the_series_skips(self):
        conn = db.connect(":memory:")
        with mock.patch.object(longform.config, "LONG_EVERY_DAYS", 0), \
             mock.patch.object(longform, "format_today", return_value="github"):
            self.assertTrue(longform.skip_short(conn, {"id": 7, "tsource": "github"}))
            self.assertTrue(longform.skip_short(conn, {"id": 7, "tsource": "github"}))   # same Short, retried
            self.assertFalse(longform.skip_short(conn, {"id": 8, "tsource": "github"}))  # the second GitHub Short
            self.assertFalse(longform.skip_short(conn, {"id": 9, "tsource": "tools:video"}))
        with mock.patch.object(longform.config, "LONG_EVERY_DAYS", 0), \
             mock.patch.object(longform, "format_today", return_value=None):
            self.assertFalse(longform.skip_short(db.connect(":memory:"), {"id": 1, "tsource": "github"}))


class WeeklyLongTests(unittest.TestCase):
    def test_main_week_and_monthly_pdf_tour(self):
        import datetime as d
        with mock.patch.object(longform.config, "LONG_EVERY_DAYS", 0),              mock.patch.object(longform.config, "LONG_SCHEDULE", "0:github_top10,5:tools_top10"),              mock.patch.object(longform.config, "PDF_TOUR_WEEKDAY", 2),              mock.patch.object(longform.config, "CHANNELS_ENABLED", ""):
            got = {d.date(2026, 11, 30) + d.timedelta(days=i): longform.format_today(d.date(2026, 11, 30) + d.timedelta(days=i))
                   for i in range(14)}
            due = {k.isoformat(): v for k, v in got.items() if v}
            self.assertEqual(due, {"2026-11-30": "github_top10", "2026-12-02": "pdf_tour", "2026-12-05": "tools_top10",
                                   "2026-12-07": "github_top10", "2026-12-12": "tools_top10"})

    def test_main_and_sister_long_videos_on_one_day_are_both_started(self):
        import datetime as d
        with mock.patch.object(longform.config, "LONG_EVERY_DAYS", 0),              mock.patch.object(longform.config, "LONG_SCHEDULE", "0:github_top10,5:tools_top10"),              mock.patch.object(longform.config, "CHANNELS_ENABLED", "kids,everyday"),              mock.patch.object(longform.config, "CHANNELS_START", "2026-11-14"),              mock.patch.object(longform.config, "CHANNEL_LONG_EVERY_DAYS", 5):
            self.assertEqual(longform.formats_today(d.date(2026, 11, 23)), ["github_top10", "kids"])
            conn = db.connect(":memory:")
            fmts = []
            with mock.patch.object(publish, "dispatch", lambda f: fmts.append(f) or True),                  mock.patch.object(longform, "formats_today", return_value=["github_top10", "kids"]):
                self.assertEqual(longform.dispatch_if_due(conn), 2)
                self.assertEqual(longform.dispatch_if_due(conn), 0)  # once per format per day
            self.assertEqual(fmts, ["github_top10", "kids"])


class FakeRepoTests(unittest.TestCase):
    def test_fake_star_installers_and_cheats_are_never_shown(self):
        from pipeline import ranking
        fake = [{"full_name": "x/archicad-desktop-setup", "description": "Archicad 2026 complete standalone desktop setup",
                 "stargazers_count": 1207, "forks_count": 0},
                {"full_name": "x/tool", "description": "Delta Executor for PC: Full Working Installer",
                 "stargazers_count": 1198, "forks_count": 40},
                {"full_name": "x/thing", "description": "A neat library for parsing things", "stargazers_count": 1200,
                 "forks_count": 1}]
        real = {"full_name": "DIYgod/pinky", "description": "Let AI agents use your phone, with you in the loop.",
                "stargazers_count": 2040, "forks_count": 220}
        self.assertTrue(all(ranking.looks_fake(r) for r in fake))
        self.assertFalse(ranking.looks_fake(real))


class PdfTourTests(unittest.TestCase):
    def test_every_tour_covers_tools_no_earlier_tour_covered(self):
        slugs = [s for _, g in material.PDF_GROUPS for s in g]
        used, seen = {}, set()
        for _ in range(len(material.PDF_GROUPS)):
            group, tools = material.pdf_tour_plan(slugs, used)
            self.assertFalse(seen & set(tools), group)  # no tool twice
            seen |= set(tools)
            used.update({f"pdf_tour_group:{group}": "x", **{f"pdf_tool:{t}": "x" for t in tools}})
        self.assertEqual(seen, set(slugs))
        self.assertIsNone(material.pdf_tour_plan(slugs, used))  # nothing new: no copy of an old tour
        new = slugs + ["a-new", "b-new", "c-new", "d-new"]
        self.assertEqual(material.pdf_tour_plan(new, used), ("New tools", ["a-new", "b-new", "c-new", "d-new"]))


class WriterTests(unittest.TestCase):
    def test_target_is_adaptive_8_to_15_minutes(self):
        self.assertEqual(writer.target_words(500), writer.MIN_WORDS)
        self.assertLessEqual(writer.target_words(100000), writer.MAX_WORDS)
        self.assertGreater(writer.target_words(3000), writer.MIN_WORDS)

    def test_valid_script_passes(self):
        self.assertEqual(writer.validate(script(), M, 1200), [])

    def test_invented_numbers_unknown_visuals_and_length_are_rejected(self):
        self.assertTrue(any("numbers" in p for p in writer.validate(script(extra=" It has 99 plugins."), M, 1200)))
        self.assertTrue(any("visual ids" in p for p in writer.validate(script(visual=7), M, 1200)))
        self.assertTrue(any("words" in p for p in writer.validate(script(words_per_para=10), M, 1200)))
        self.assertTrue(any("URL" in p for p in writer.validate(script(extra=" see https://x.y"), M, 1200)))

    def test_no_api_key_means_no_video(self):
        with mock.patch.object(writer.config, "ANTHROPIC_API_KEY", ""):
            self.assertIsNone(writer.write(M))


class RenderTests(unittest.TestCase):
    def test_captions_are_balanced(self):
        chunks = render.caption_chunks("one two three four five six seven eight nine ten eleven twelve thirteen "
                                       "fourteen fifteen sixteen seventeen. Short.")
        self.assertEqual(len(chunks), 3)
        self.assertTrue(all(len(c.split()) <= 16 for c in chunks))

    def test_timeline_and_chapters(self):
        s = {"chapters": [{"title": "Intro", "paragraphs": [{"text": "a", "visual": 0}]},
                          {"title": "Use", "paragraphs": [{"text": "b", "visual": 0}, {"text": "c", "visual": 0}]}]}
        segs, total = render.timeline(s, [("x", 10.0), ("y", 5.0), ("z", 5.0)])
        self.assertEqual([k for k, *_ in segs], ["intro", "card", "para", "card", "para", "para", "end"])
        self.assertAlmostEqual(total, render.INTRO + 2 * render.CARD + 20 + 3 * render.PAD + render.END)

    def test_code_blocks_skip_secrets(self):
        md = "```bash\npip install box\n```\n```bash\nexport API_KEY=abc\n```"
        self.assertEqual([c["lines"] for c in material.code_blocks(md)], [["pip install box"]])


class PublishTests(unittest.TestCase):
    def test_description_has_youtube_chapters_from_zero(self):
        d = publish.description(M, {"description": "Summary."}, [(0.0, "Intro"), (65.4, "Install"), (3725, "End")])
        self.assertIn("0:00 Intro\n1:05 Install\n1:02:05 End", d)
        self.assertIn("https://github.com/a/box", d)


if __name__ == "__main__":
    unittest.main()


class PageTextTests(unittest.TestCase):
    def test_free_script_is_built_only_from_page_text(self):
        from pipeline.longform import pagetext
        sec = ("It turns a short idea into a video clip in a few minutes. You type what you want to see and it "
               "makes the scene. You can change the style, the camera and the length of each shot. ") * 2
        visuals, facts = [], []
        for p, name in enumerate(["Alpha", "Beta", "Gamma", "Delta"]):
            visuals.append({"id": len(visuals), "kind": "overview", "page": p, "label": f"{name} homepage",
                            "text": f"{name} makes videos from text. See https://x.y for more."})
            for h in ("Features", "How it works", "Pricing plans"):
                visuals.append({"id": len(visuals), "kind": "page", "page": p, "label": f"{name}: section '{h}'",
                                "text": sec})
            facts.append(f"TOOL: {name}")
        m = {"format": "tools_top10", "subject": "4 AI tools for video generation", "facts": "\n".join(facts),
             "visuals": visuals, "links": []}
        s = pagetext.write(m)
        self.assertIsNotNone(s)
        self.assertEqual([c["title"] for c in s["chapters"]], ["Introduction", "Alpha", "Beta", "Gamma", "Delta",
                                                                "Summary"])
        text = " ".join(p["text"] for c in s["chapters"] for p in c["paragraphs"])
        self.assertNotIn("http", text)
        self.assertEqual(s["cost_usd"], 0.0)
        self.assertIsNone(pagetext.write({**m, "visuals": visuals[:8]}))  # 2 items: not enough for a long video


class VoiceChunkTests(unittest.TestCase):
    def test_long_paragraphs_are_read_in_pieces_the_voice_model_can_take(self):
        from pipeline.render import CLONE_CHARS, clone_chunks
        text = ("One two three, four five six seven eight nine ten eleven twelve thirteen fourteen fifteen, " * 6) + "End."
        pieces = clone_chunks(text)
        self.assertGreater(len(pieces), 1)
        self.assertTrue(all(len(p) <= CLONE_CHARS for p in pieces))
        self.assertEqual(" ".join(pieces).split(), text.split())  # nothing lost or added
        self.assertEqual(clone_chunks("Short one."), ["Short one."])


class GeminiTests(unittest.TestCase):
    def test_gemini_script_must_pass_the_same_checks_or_pages_are_used(self):
        from pipeline import gemini
        good = script()
        with mock.patch.object(gemini, "available", return_value=True), \
             mock.patch.object(gemini, "generate_json", return_value=good), \
             mock.patch.dict(os.environ, {"LONG_WRITER": "auto"}):
            self.assertEqual(writer.write({**M, "material_words": 2400})["cost_usd"], 0.0)  # target 1,200 words
        bad = script(extra=" It has 99 plugins.")
        with mock.patch.object(gemini, "available", return_value=True), \
             mock.patch.object(gemini, "generate_json", return_value=bad), \
             mock.patch.dict(os.environ, {"LONG_WRITER": "auto"}):
            self.assertIsNone(writer.write_gemini({**M, "material_words": 2000}))

    def test_short_script_from_gemini_keeps_the_fact_checks(self):
        from pipeline import generate
        row = {"title": "a/box", "raw": "{}", "source": "github"}
        claims = [{"text": "box builds things fast with one command"}]
        ok = generate._checked(row, claims, {"hooks": ["Want faster builds?"], "best": 0, "beats": ["Meet box."]})
        self.assertEqual(ok["hook"], "Want faster builds?")
        self.assertIsNone(generate._checked(row, claims, {"hooks": ["Want faster builds?"], "beats": ["10x faster."]}))
        self.assertIsNone(generate._checked(row, claims, {"hooks": ["Faster builds."], "beats": ["Meet box."]}))
