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
