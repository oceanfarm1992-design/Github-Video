import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")

from PIL import Image  # noqa: E402

from pipeline import generate, motion, publish, tools  # noqa: E402

TOOLS_ROW = {"title": "3 AI tools for video generation", "source": "tools:video", "url": "https://a.example",
             "raw": json.dumps({"phrase": "video generation", "hashtag": "#AIVideo", "tools": [
                 {"name": "Alpha", "url": "https://a.example", "description": "Make videos from text."},
                 {"name": "Beta", "url": "https://b.example", "description": "Edit clips with AI."},
                 {"name": "Gamma", "url": "https://c.example", "description": "Animate photos."}]})}


class ToolsTests(unittest.TestCase):
    def test_shorten_cuts_at_natural_break_and_never_ends_on_filler(self):
        s = tools.shorten("Plan, generate, iterate, and refine, keeping full context across every stage of creation")
        self.assertTrue(s.endswith("stage."), s)
        self.assertLessEqual(len(s), tools.SPOKEN_LIMIT + 1)
        self.assertEqual(tools.shorten("Short and sweet"), "Short and sweet.")

    def test_dead_and_low_information_descriptions_are_rejected(self):
        self.assertTrue(tools.DEAD_RE.search("Understand the Sora discontinuation, including content exports"))
        self.assertTrue(tools.DEAD_RE.search("This app is shutting down on June 1"))
        self.assertFalse(tools.DEAD_RE.search("Create videos from text in minutes"))
        self.assertTrue(tools.LOW_INFO_RE.match("Official Synthesia site."))
        self.assertFalse(tools.LOW_INFO_RE.match("Generate AI videos from your ideas using HeyGen."))

    def test_catalog_is_well_formed(self):
        for key, (phrase, tag, entries) in tools.CATALOG.items():
            self.assertTrue(tag.startswith("#"), key)
            self.assertGreaterEqual(len(entries), 10, key)  # spare tools so dead sites can be skipped
            for name, url in entries:
                self.assertTrue(url.startswith("https://"), (key, name))

    def test_daily_mix_two_github_one_news_one_tools(self):
        mk = lambda src, sc: {"source": src, "score": sc}
        rows = [mk("github", 90), mk("github", 85), mk("github", 84), mk("rss:aws-ml", 70), mk("hn", 68),
                mk("tools:video", 90)]
        got = [(r["source"], r["score"]) for r in generate.pick(rows, 2, 1, 80, 65, 1)]
        self.assertEqual(got, [("github", 90), ("tools:video", 90), ("github", 85), ("rss:aws-ml", 70)])

    def test_tools_script_and_links(self):
        hook, beats, links = generate.tools_script(TOOLS_ROW)
        self.assertTrue(hook.startswith("Looking for AI tools for video generation?"))
        self.assertIn("Here are 3", hook)
        self.assertEqual(beats[1], "2. Beta: Edit clips with AI.")
        self.assertIn("3. Gamma - https://c.example", links)
        self.assertEqual(generate.hashtags(TOOLS_ROW), ["#AI", "#AITools", "#AIVideo"])

    def test_github_hashtags_from_repo_topics(self):
        row = {"source": "github", "raw": json.dumps({"topics": ["llm", "ai-agents", "x"]})}
        self.assertEqual(generate.hashtags(row), ["#AI", "#OpenSource", "#GitHub", "#Llm", "#AiAgents"])

    def test_youtube_title_uses_hook_and_link_block(self):
        c = {"hook": "Want a free, open-source tool for AI agents?", "title": "owner/repo"}
        self.assertEqual(publish.youtube_title(c), "Want a free, open-source tool for AI agents? #Shorts")
        self.assertLessEqual(len(publish.youtube_title({"hook": "x " * 80, "title": "t"})), 100)
        self.assertEqual(publish.link_block("Here's the link: https://x.io"), "Link: https://x.io")
        self.assertTrue(publish.link_block("Here are the links:\n1. A - https://a").startswith("Here are the links"))

    def test_social_caption_for_tools_asks_for_all_links(self):
        c = {"caption": "Looking for AI tools?\n\nWhich one would you try first?\n\nSource: https://a.example",
             "hashtags": '["#AI"]', "_cta": {"keyword": "TOOL", "response": "Here are the links:\n1. A - https://a"}}
        cap = publish.social_caption(c)
        self.assertIn("Comment TOOL and I'll DM you all the links", cap)
        self.assertIn("Which one would you try first?", cap)
        self.assertNotIn("http", cap)

    def test_publish_guard_without_supabase_falls_back_to_local(self):
        for k in ("SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_KEY"):
            os.environ.pop(k, None)
        self.assertIsNone(publish.already_published("youtube", "t1"))
        publish.record_published("youtube", "t1", "v1", "u")  # must not raise

    def test_sites_catalog_safety_screen(self):
        from pipeline import sites
        for bad in ("Shodan [Cybersecurity]", "Sherlock [OSINT]", "Insecam [Live Cameras]", "Exploit Database",
                    "Pirate Bay", "Bitcoin.org [Blockchain]", "CoinGecko [Crypto Data]", "PimEyes [Face Search]",
                    "Tor Browser [Privacy Browser]", "ROMhacking.net [Game Modding]", "12ft Paywall Bypass"):
            self.assertTrue(sites.DENY.search(bad), bad)
        for good in ("Designspiration [Design]", "This Person Does Not Exist [AI Demo]", "DSPy [AI Development]",
                     "Photopea [Design]", "Radio Garden [Entertainment]", "ClearlyDefined [Software Licensing]"):
            self.assertFalse(sites.DENY.search(good), good)
        groups = {"TradingView [Finance & Data]": "financial", "Stripe [Payments]": "financial",
                  "Robinhood [Investing]": "financial", "NordVPN [VPN]": "proxy & anonymity",
                  "Temp Mail [Temporary Email]": "proxy & anonymity", "Scrapy [Web Scraping]": "legal & copyright",
                  "YouTube Downloader": "legal & copyright", "Hack The Box [Cybersecurity Learning]": "privacy & security",
                  "Shodan [Cybersecurity]": "privacy & security", "SVG Porn [SVG]": "adult & restricted"}
        for entry, group in groups.items():
            self.assertEqual(sites.deny_reason(entry), group, entry)
        for fine in ("Pexels Videos [Stock Video]", "World Bank Data [Data]", "Hacker News [Tech Community]",
                     "Hackaday [Hardware]", "Big-O Cheat Sheet [Algorithms]", "DrugBank [Drug Research]"):
            self.assertIsNone(sites.deny_reason(fine), fine)
        cat = sites.load_catalog()
        self.assertGreater(len(cat), 1000)
        self.assertFalse([u for _, u in cat if "temp-mail" in u])
        self.assertEqual(cat[0][0], "Radio Garden")  # hand-picked list first
        self.assertFalse([n for n, _ in cat if sites.DENY.search(n)])

    def test_outro_counts_links_not_narrated_lines(self):
        one = generate.series_outro("LINK", "Here's the link: https://www.photopea.com", legal_note=True)
        self.assertEqual(one, "All 100% legal. Comment LINK and I'll send you the link.")
        many = generate.series_outro("TOOL", "Here are the links:\n1. A - https://a\n2. B - https://b")
        self.assertEqual(many, "Comment TOOL and I'll send you all 2 links.")
        # a one-site video narrates several lines but must still promise ONE link
        row = {"source": "sites:part-1", "raw": json.dumps({"part": 1, "tools": [
            {"name": "Photopea", "url": "https://www.photopea.com", "description": "Edit photos online."}],
            "sections": [{"heading": "Fully Local", "text": "No uploads.", "box": [0, 0, 1, 1]},
                         {"heading": "Free", "text": "No cost.", "box": [0, 0, 1, 1]}]})}
        hook, beats, links = generate.sites_script(row)
        self.assertEqual(len(beats), 3)
        self.assertIn("the link", generate.series_outro("LINK", links, legal_note=True))

    def test_sites_series_script_quota_and_no_repeats(self):
        from pipeline import db, sites
        row = {"source": "sites:part-3", "raw": json.dumps({"part": 3, "tools": [
            {"name": "Radio Garden", "url": "https://radio.garden", "description": "Explore live radio."}]})}
        hook, beats, links = generate.sites_script(row)
        self.assertEqual(hook, "Websites that feel illegal to know. Part 3.")
        self.assertEqual(beats, ["1. Radio Garden: Explore live radio."])
        self.assertIn("https://radio.garden", links)
        self.assertEqual(generate.kind("sites:part-3"), "sites")
        self.assertEqual(generate.caption_question(row), "Which one did you not know about?")
        mk = lambda src, sc: {"source": src, "score": sc}
        got = generate.pick([mk("github", 80), mk("rss:x", 70), mk("sites:part-1", 90)], 1, 0, 75, 65, 0, 1)
        self.assertEqual([r["source"] for r in got], ["sites:part-1", "github"])  # news quota 0 now
        conn = db.connect(":memory:")
        conn.execute("INSERT INTO topics (id,title,source,url,raw,status) VALUES ('p1','t','sites:part-1','u',?, 'PUBLISHED')",
                     (json.dumps({"tools": [{"url": "https://radio.garden"}]}),))
        self.assertIn("https://radio.garden", sites.used_urls(conn))
        self.assertEqual(sites.next_part(conn), 2)
        for name, url in sites.CATALOG:  # catalog hygiene
            self.assertTrue(url.startswith("https://"), name)
        self.assertEqual(len({u for _, u in sites.CATALOG}), len(sites.CATALOG))  # no duplicates

    def test_dm_greets_by_first_name_only_for_real_names(self):
        from pipeline import zernio
        self.assertEqual(zernio.first_name("Irshan Sareef"), "Irshan")
        self.assertEqual(zernio.first_name("Sara"), "Sara")
        for username in ("ai_fan_92", "ainews987", "sara", "", None, "\U0001F525\U0001F525"):
            self.assertIsNone(zernio.first_name(username), username)
        self.assertTrue(zernio.dm_messages("o/r", "Here's the link: https://x", "Irshan")[0].startswith("Hi Irshan!"))
        self.assertTrue(zernio.dm_messages("o/r", "Here's the link: https://x")[0].startswith("Hi! Thanks"))

    def test_zernio_duplicate_409_is_recorded_as_published(self):
        from pipeline import zernio
        err = ('request failed https://zernio.com/api/v1/posts: HTTP Error 409: Conflict - {"error":"This exact '
               'content is already scheduled, publishing, or was posted","details":{"existingPostId":"abc123"}}')

        def boom(*a, **k):
            raise RuntimeError(err)

        made = []
        olds = (zernio._call, zernio.account, zernio.create_automation)
        zernio._call = boom
        zernio.account = lambda p: {"_id": "acct1", "profileId": {"_id": "prof1"}}
        zernio.create_automation = lambda *a: made.append(a[3])
        try:
            c = {"caption": "Hook?\n\nSource: https://x", "hashtags": '["#AI"]', "title": "t",
                 "_cta": {"keyword": "GITHUB", "response": "Here's the link: https://x"}}
            self.assertEqual(zernio.publish_reel("facebook", c, "https://v/x.mp4"), ("abc123", ""))
            self.assertEqual(made, ["abc123"])  # the DM automation is still set up for the existing post
        finally:
            zernio._call, zernio.account, zernio.create_automation = olds

    def test_guided_tour_spotlight_and_disclaimer(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "page.png")
        Image.new("RGB", (1080, 3000), (200, 200, 200)).save(p)
        scenes = [("Hook.", 2.5), ("Section one.", 2.0)]
        box = [20, 600, 500, 80]  # CSS px
        out = list(motion.frames(scenes, scene_pages=[(p, "https://s.example")] * 2, scene_focus=[None, box],
                                 disclaimer="For educational purposes only"))
        img = lambda fi: Image.frombytes("RGB", (motion.W, motion.H), out[fi])
        early, late = img(int(0.5 * motion.FPS)), img(int(2.4 * motion.FPS))
        # disclaimer is drawn only at the start, in the bottom area
        region = (200, motion.H - 195, 880, motion.H - 150)
        self.assertNotEqual(early.crop(region).tobytes(), late.crop(region).tobytes())
        # by the end of scene 2 the page outside the spotlight is dimmed (grey 200 -> darker)
        self.assertLess(img(len(out) - 1).getpixel((motion.W // 2, 400))[0], 150)
        panel = motion.Panel(p, "https://s.example", 1000)
        self.assertEqual(panel.target(None)[2:], (1.0, None))
        self.assertGreaterEqual(panel.target(box)[2], 1.12)

    def test_frames_show_a_different_page_per_scene_with_labels(self):
        d = tempfile.mkdtemp()
        pages = []
        for i, col in enumerate(((200, 30, 30), (30, 200, 30))):
            p = os.path.join(d, f"p{i}.png")
            Image.new("RGB", (1080, 3000), col).save(p)
            pages.append((p, f"https://site{i}.example"))
        scenes = [("Hook?", 1.0), ("1. A: one.", 1.0), ("2. B: two.", 1.0), ("Bye.", 1.0)]
        out = list(motion.frames(scenes, scene_pages=[None, pages[0], pages[1], None],
                                 scene_labels=[None, "#1/2  A", "#2/2  B", None]))
        self.assertEqual(len(out), 4 * motion.FPS)
        mid = lambda fi: Image.frombytes("RGB", (motion.W, motion.H), out[fi]).getpixel((motion.W // 2, 700))
        self.assertGreater(mid(int(1.9 * motion.FPS))[0], 150)   # scene 2 shows the red page
        self.assertGreater(mid(int(2.9 * motion.FPS))[1], 150)   # scene 3 shows the green page


if __name__ == "__main__":
    unittest.main()
