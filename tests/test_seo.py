import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import publish, seo  # noqa: E402


def content(source, title, hook, beats, raw, tags, question="Would you use this?"):
    return {"tsource": source, "title": title, "hook": hook, "traw": json.dumps(raw),
            "script": json.dumps({"hook": hook, "beats": beats, "outro": ""}),
            "caption": f"{hook}\n\n{question}\n\nSource: https://x", "hashtags": json.dumps(tags),
            "_cta": {"keyword": "GITHUB", "response": "Here's the link: https://github.com/a/box"}}


GH = content("github", "a/box", "Want to run AI agents safely?",
             ["Meet Box, a sandbox for AI agents.", "Built in Rust."],
             {"topics": ["ai-agents", "sandbox"], "language": "Rust"}, ["#OpenSource", "#GitHub", "#AI"])
PROMO = content("promo:merge-pdf", "Merge PDF - Privacy PDF Tools",
                "Need to combine multiple PDFs into a single document, without uploading your files?",
                ["Meet Merge PDF, on Privacy PDF Tools.", "Merge PDF combines PDF files into one.",
                 "Runs 100% in your browser - files are never uploaded."],
                {"tool": {"name": "Merge PDF"}}, ["#PDFTools"], "Which PDF tool should I show next?")


class SeoTests(unittest.TestCase):
    def test_titles_name_the_subject_and_fit(self):
        for c in (GH, PROMO):
            t = publish.youtube_title(c)
            self.assertLessEqual(len(t), 100)
            self.assertTrue(t.endswith("#Shorts"))
        self.assertTrue(publish.youtube_title(GH).startswith("box on GitHub: "))
        self.assertIn("Merge PDF Online", publish.youtube_title(PROMO))

    def test_description_has_summary_links_disclosure_and_hashtags(self):
        d = seo.youtube_description(GH, "Link: https://github.com/a/box")
        self.assertTrue(d.startswith("Want to run AI agents safely?\n\nMeet Box"))
        for must in ("Link: https://github.com/a/box", seo.AI_VOICE, "#Shorts #OpenSource"):
            self.assertIn(must, d)
        self.assertLessEqual(len(d), 5000)

    def test_tags_unique_short_and_within_budget(self):
        tags = seo.youtube_tags(GH)
        self.assertIn("box", tags)
        self.assertIn("ai agents", tags)
        self.assertEqual(len({t.lower() for t in tags}), len(tags))
        self.assertTrue(all(len(t) <= 60 for t in tags))
        self.assertLessEqual(sum(len(t) + 3 for t in tags), 500)

    def test_social_caption_keyword_first_no_links(self):
        s = publish.social_caption(PROMO)
        self.assertTrue(s.startswith(PROMO["hook"]))
        self.assertIn("Merge PDF combines PDF files into one.", s)  # summary skips the "Meet X" beat
        self.assertIn("Comment GITHUB and I'll DM you the link", s)
        self.assertNotIn("http", s)


class StoryTests(unittest.TestCase):
    def test_story_body_and_platforms(self):
        import os
        from pipeline import zernio
        calls = []
        olds = (zernio._call, zernio.account, zernio.enabled, zernio.available)
        zernio._call = lambda path, body=None, **k: calls.append(body) or {
            "post": {"_id": "s1", "platforms": [{"platform": "instagram", "status": "published"}]}}
        zernio.account = lambda plat: {"_id": "acc"}
        zernio.enabled = lambda: True
        zernio.available = lambda plat: True
        try:
            self.assertEqual(zernio.publish_story("instagram", GH, "https://v/x.mp4"), ("s1", ""))
            os.environ["STORIES"] = "1"
            self.assertIn("instagram_story", publish.platforms())
            os.environ["STORIES"] = "0"
            self.assertNotIn("instagram_story", publish.platforms())
        finally:
            zernio._call, zernio.account, zernio.enabled, zernio.available = olds
            os.environ.pop("STORIES", None)
        body = calls[0]
        self.assertEqual(body["platforms"][0]["platformSpecificData"], {"contentType": "story"})
        self.assertNotIn("content", body)  # Stories show no caption
        self.assertEqual(publish.MAX_SECONDS["instagram_story"], 60)

    def test_story_waits_for_its_reel(self):
        self.assertTrue(publish.story_waits("instagram_story", {"youtube"}))
        self.assertFalse(publish.story_waits("instagram_story", {"instagram"}))
        self.assertFalse(publish.story_waits("facebook", set()))

    def test_reel_returns_only_once_live_on_the_platform(self):
        from pipeline import zernio
        polls = iter(["processing", "processing", "published"])
        olds = (zernio._call, zernio.account, zernio.create_automation, zernio.POLL_SECONDS)

        def fake_call(path, body=None, **k):
            if body is not None:  # create
                return {"post": {"_id": "r1", "platforms": [{"platform": "instagram", "status": "processing"}]}}
            return {"post": {"_id": "r1", "platforms": [{"platform": "instagram", "status": next(polls),
                                                         "platformPostUrl": "https://ig/r1"}]}}
        zernio._call, zernio.account = fake_call, lambda plat: {"_id": "acc"}
        zernio.create_automation, zernio.POLL_SECONDS = (lambda *a: None), 0
        try:
            self.assertEqual(zernio.publish_reel("instagram", GH, "https://v/x.mp4"), ("r1", "https://ig/r1"))
        finally:
            zernio._call, zernio.account, zernio.create_automation, zernio.POLL_SECONDS = olds

    def test_reel_rejected_by_meta_raises(self):
        from pipeline import zernio
        old = zernio._call
        zernio._call = lambda path, body=None, **k: {"post": {"platforms": [
            {"platform": "facebook", "status": "failed", "errorMessage": "Reel too long"}]}}
        try:
            with self.assertRaisesRegex(RuntimeError, "Reel too long"):
                zernio.wait_published("r1", "facebook")
        finally:
            zernio._call = old

    def test_video_seconds_unknown_when_no_file_or_url(self):
        self.assertEqual(publish.video_seconds("out/video/missing.mp4", None), 0.0)


if __name__ == "__main__":
    unittest.main()
