import datetime
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import channels, config, generate, longform, publish, seo, storyboard, theme  # noqa: E402

D = datetime.date
ACCOUNTS = [
    {"_id": "kidsfb", "platform": "facebook", "username": "AI for Kids"},
    {"_id": "mainfb", "platform": "facebook", "username": "Ai & Open Source News"},
    {"_id": "mainig", "platform": "instagram", "username": "ainews987"},
    {"_id": "dayfb", "platform": "facebook", "username": "AI for Normal People"},
]
MAP = json.dumps({"main": {"facebook": "mainfb"}, "kids": {"facebook": "kidsfb", "tiktok": "kidstt"},
                  "everyday": {"facebook": "AI for Normal People"}})


class SourceTests(unittest.TestCase):
    def test_channel_of_a_source(self):
        self.assertEqual(channels.of_source("kids:drawing"), "kids")
        self.assertEqual(channels.of_source("everyday:photos"), "everyday")
        self.assertEqual(channels.of_source("long:kids"), "kids")
        self.assertEqual(channels.of_source("tools:video"), "main")
        self.assertEqual(channels.of_source("github"), "main")
        self.assertEqual(channels.of_source(None), "main")

    def test_series_kind_and_theme(self):
        self.assertEqual(theme.kind("kids:drawing"), "kids")
        self.assertEqual(theme.kind("everyday:photos"), "everyday")
        self.assertEqual(theme.for_source("kids:drawing").tag, "AI FOR KIDS")
        self.assertTrue(theme.is_list("kids") and theme.is_list("everyday") and theme.is_list("tools"))

    def test_watermark_is_the_channels_own_name(self):
        with mock.patch.dict(os.environ, {"WATERMARK_TEXT": "Main"}):
            self.assertEqual(channels.watermark("kids"), "AI for Kids")
            self.assertEqual(channels.watermark("everyday"), "AI for Normal People")
            self.assertEqual(channels.watermark("main"), "Main")


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        p = [mock.patch.object(config, "CHANNELS_START", "2026-11-14"),
             mock.patch.object(config, "CHANNELS_ENABLED", "kids,everyday"),
             mock.patch.object(config, "CHANNEL_SHORT_EVERY_DAYS", 2),
             mock.patch.object(config, "CHANNEL_LONG_EVERY_DAYS", 5),
             mock.patch.object(config, "LONG_EVERY_DAYS", 5),
             mock.patch.object(config, "LONG_START", "2026-11-01"),
             mock.patch.object(config, "LONG_FORMATS", "github,tools,sites")]
        for x in p:
            x.start()
            self.addCleanup(x.stop)

    def test_shorts_alternate_one_channel_a_day(self):
        for i in range(10):
            day = D(2026, 11, 14) + datetime.timedelta(days=i)
            due = [k for k in channels.CHANNELS if channels.short_due(k, day)]
            self.assertEqual(len(due), 1, day)
            self.assertEqual(due[0], "kids" if i % 2 == 0 else "everyday")

    def test_nothing_before_the_start_or_when_disabled(self):
        self.assertFalse(channels.short_due("kids", D(2026, 11, 12)))
        self.assertIsNone(channels.long_due(D(2026, 11, 13)))
        with mock.patch.object(config, "CHANNELS_ENABLED", ""):
            self.assertFalse(channels.short_due("kids", D(2026, 11, 14)))
            self.assertIsNone(channels.long_due(D(2026, 11, 18)))

    def test_long_videos_every_5_days_never_on_the_main_channels_day(self):
        seen = {"kids": [], "everyday": []}
        for i in range(30):
            day = D(2026, 11, 14) + datetime.timedelta(days=i)
            main = longform._main_format(day)
            sister = channels.long_due(day)
            self.assertFalse(main and sister, day)
            if sister:
                seen[sister].append(day)
            self.assertEqual(longform.format_today(day), main or sister)
        for days in seen.values():
            self.assertEqual({(b - a).days for a, b in zip(days, days[1:])}, {5})
        self.assertEqual(seen["everyday"][0], D(2026, 11, 15))
        self.assertEqual(seen["kids"][0], D(2026, 11, 18))


class AccountTests(unittest.TestCase):
    def test_each_channel_posts_only_to_its_own_accounts(self):
        with mock.patch.dict(os.environ, {"CHANNEL_ACCOUNTS": MAP}):
            self.assertEqual(channels.pick_account(ACCOUNTS, "facebook", "main")["_id"], "mainfb")
            self.assertEqual(channels.pick_account(ACCOUNTS, "facebook", "kids")["_id"], "kidsfb")
            self.assertEqual(channels.pick_account(ACCOUNTS, "facebook", "everyday")["_id"], "dayfb")  # by name
            self.assertEqual(channels.pick_account(ACCOUNTS, "instagram", "main")["_id"], "mainig")
            self.assertIsNone(channels.pick_account(ACCOUNTS, "instagram", "kids"))  # unmapped: never main's
            self.assertIsNone(channels.pick_account(ACCOUNTS, "tiktok", "kids"))      # mapped but not connected

    def test_main_skips_accounts_claimed_by_a_sister_channel(self):
        m = json.dumps({"kids": {"facebook": "kidsfb"}})
        with mock.patch.dict(os.environ, {"CHANNEL_ACCOUNTS": m}):
            self.assertEqual(channels.pick_account(ACCOUNTS, "facebook", "main")["_id"], "mainfb")
        with mock.patch.dict(os.environ, {"CHANNEL_ACCOUNTS": "not json"}):
            self.assertIsNone(channels.pick_account(ACCOUNTS, "facebook", "kids"))

    def test_youtube_tokens_are_per_channel(self):
        env = {"YOUTUBE_CLIENT_ID": "c", "YOUTUBE_CLIENT_SECRET": "s", "YOUTUBE_REFRESH_TOKEN": "main",
               "YOUTUBE_REFRESH_TOKEN_KIDS": "kids"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertTrue(publish.youtube_token_available("kids"))
            self.assertFalse(publish.youtube_token_available("everyday"))
            self.assertEqual(channels.youtube_refresh_token("kids"), "kids")
            self.assertEqual(channels.youtube_refresh_token("main"), "main")

    def test_sister_channel_without_accounts_has_no_platforms(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(publish.platforms("kids"), {})


class WorkspaceTests(unittest.TestCase):
    def test_sister_channels_use_the_second_zernio_workspace(self):
        from pipeline import zernio
        calls = []
        ws = {"ZERNIO_API_KEY": [{"_id": "mainfb", "platform": "facebook"}],
              "ZERNIO_TWO_API_KEY": [{"_id": "kidsfb", "platform": "facebook"}, {"_id": "dayfb", "platform": "facebook"}]}

        def fake(path, body=None, api="ZERNIO_API_KEY", **k):
            calls.append(api)
            return {"accounts": ws[api]}
        env = {"ZERNIO_API_KEY": "a", "ZERNIO_TWO_API_KEY": "b",
               "CHANNEL_ACCOUNTS": json.dumps({"kids": {"facebook": "kidsfb"}, "everyday": {"facebook": "dayfb"}})}
        with mock.patch.dict(os.environ, env), mock.patch.object(zernio, "_call", fake),                 mock.patch.object(zernio, "_accounts", {}):
            self.assertEqual(zernio.account("facebook", "main")["_id"], "mainfb")
            kids = zernio.account("facebook", "kids")
            self.assertEqual((kids["_id"], kids["_api"]), ("kidsfb", "ZERNIO_TWO_API_KEY"))
            self.assertEqual(zernio.account("facebook", "everyday")["_id"], "dayfb")
            self.assertEqual(sorted(set(calls)), ["ZERNIO_API_KEY", "ZERNIO_TWO_API_KEY"])
        with mock.patch.dict(os.environ, {"ZERNIO_API_KEY": "a"}, clear=True):
            self.assertEqual(zernio.api_for("kids"), "ZERNIO_API_KEY")  # no second key: same workspace


class ContentTests(unittest.TestCase):
    ROW = {"source": "kids:drawing", "raw": json.dumps({
        "phrase": "kids who love to draw", "hashtag": "#KidsArt",
        "tools": [{"name": f"T{i}", "url": f"https://t{i}.example", "description": "Draws."} for i in range(5)]})}

    def test_kids_script_speaks_to_parents_and_teachers(self):
        hook, beats, links = generate.tools_script(self.ROW)
        self.assertTrue(hook.startswith("Parents and teachers"))
        self.assertEqual(len(beats), 5)
        self.assertIn("5. T4 - https://t4.example", links)
        self.assertEqual(generate.hashtags(self.ROW)[0], "#AIForKids")

    def test_titles_and_cards(self):
        c = {"tsource": "everyday:photos", "traw": json.dumps({"phrase": "fixing your photos", "tools": [{"name": f"T{i}"} for i in range(5)]}),
             "hook": "h", "title": "5 AI tools for fixing your photos"}
        self.assertEqual(seo.title(c), "5 Easy AI Tools for Fixing your photos (No Tech Skills Needed)")
        self.assertIn("AI for Normal People", seo.youtube_description(c))
        self.assertEqual(storyboard.endcard("kids", None, 5)["options"], ["1", "2", "3", "4", "5"])

    def test_main_daily_count_ignores_sister_channel_videos(self):
        from pipeline import db
        conn = db.connect(":memory:")
        now = generate._day_start() + 10
        for i, src in enumerate(("github", "kids:drawing", "everyday:photos")):
            conn.execute("INSERT INTO topics (id,title,source,url,status,updated_at) VALUES (?,?,?,?,?,?)",
                         (f"t{i}", "x", src, "u", "RENDERING", now))
            conn.execute("INSERT INTO contents (topic_id,title,status,created_at) VALUES (?,?,?,?)",
                         (f"t{i}", "x", "ready", now))
        self.assertEqual(generate.videos_today(conn), 1)
        self.assertEqual(generate.videos_today(conn, "kids"), 1)

    def test_pick_gives_a_sister_channel_its_own_room(self):
        rows = [{"source": "kids:drawing", "score": 90}, {"source": "everyday:photos", "score": 90}]
        picked = generate.pick(rows, 0, 0, 0, 0, channel_room={"kids": 1, "everyday": 0})
        self.assertEqual([r["source"] for r in picked], ["kids:drawing"])


if __name__ == "__main__":
    unittest.main()


class UniqueToolTests(unittest.TestCase):
    def test_a_tool_is_never_shown_twice_in_a_series(self):
        from pipeline import catalog, db, tools
        conn = db.connect(":memory:")
        cat = {"art": ("drawing", "#Art", [(f"T{i}", f"https://t{i}.example") for i in range(12)])}
        shown = [{"name": f"T{i}", "url": f"https://www.t{i}.example/"} for i in range(5)]
        conn.execute("INSERT INTO topics (id,title,source,url,raw,status,updated_at) VALUES (?,?,?,?,?,?,?)",
                     ("a", "x", "kids:art", "u", json.dumps({"tools": shown}), "PUBLISHED", 1))
        used = catalog.used_urls(conn, "kids")
        left = catalog.unused(cat["art"][2], used)
        self.assertEqual([n for n, _ in left], [f"T{i}" for i in range(5, 12)])  # www and slash do not matter
        with mock.patch.object(tools, "url_ok", return_value=True), \
             mock.patch.object(tools, "describe", side_effect=lambda n, u: f"{n} makes art for you."), \
             mock.patch.object(tools, "shows_in_browser", return_value=True), \
             mock.patch.object(catalog, "merged", return_value=cat), \
             mock.patch.object(config, "CHANNEL_TOOLS_PER_VIDEO", 5):
            tid = tools.build(conn, "art", 5, None, "kids")
        row = conn.execute("SELECT title, raw FROM topics WHERE id=?", (tid,)).fetchone()
        self.assertEqual([t["name"] for t in json.loads(row["raw"])["tools"]], [f"T{i}" for i in range(5, 10)])
        self.assertEqual(row["title"], "5 AI tools for drawing (part 2)")

    def test_suggested_tool_must_match_its_site(self):
        from pipeline import catalog
        self.assertTrue(catalog.identity_ok("Khanmigo", "https://www.khanmigo.ai", "Khanmigo", ""))
        self.assertTrue(catalog.identity_ok("Read Along", "https://readalong.google", "", ""))
        self.assertFalse(catalog.identity_ok("Magic Tutor", "https://example.com", "Buy domains", "Parked page"))


class ToolSourceTests(unittest.TestCase):
    def test_tools_are_sorted_into_each_channels_topics(self):
        from pipeline import sources
        self.assertEqual(sources.topic_for("tools", "Turn any photo into a short video clip"), "video")
        self.assertEqual(sources.topic_for("tools", "Clone your voice for podcasts"), "voice")
        self.assertEqual(sources.topic_for("everyday", "Plan your next trip in seconds"), "travel")
        self.assertEqual(sources.topic_for("kids", "Math homework tutor for students"), "homework")
        self.assertIsNone(sources.topic_for("everyday", "A database for developers"))

    def test_new_tools_are_checked_stored_and_kept_one_year(self):
        from pipeline import catalog, db, sources
        conn = db.connect(":memory:")
        found = [("tools", "video", "Clipper", "https://clipper.example"),
                 ("tools", "video", "Fake", "https://fake.example")]
        with mock.patch.object(catalog, "verify", side_effect=lambda n, u: n == "Clipper"):
            self.assertEqual(sources.add(conn, found, "test"), 1)
            self.assertEqual(sources.add(conn, found, "test"), 0)  # already known: not checked again
        self.assertIn(("Clipper", "https://clipper.example"), catalog.merged(conn, "tools")["video"][2])
        conn.execute("UPDATE catalog_tools SET added_at=?", (time.time() - 400 * 86400,))
        with mock.patch.object(sources, "hugging_face", return_value=[]), mock.patch.dict(os.environ, {}, clear=True):
            sources.run(conn)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM catalog_tools").fetchone()[0], 0)


class HackerNewsTests(unittest.TestCase):
    def test_show_hn_products_only_not_repos(self):
        from pipeline import sources
        hits = {"hits": [
            {"title": "Show HN: Clipster – turn podcasts into video clips with AI", "url": "https://clipster.app/x?y=1",
             "points": 90},
            {"title": "Show HN: Repo – an AI agent", "url": "https://github.com/a/b", "points": 300},
            {"title": "Show HN: Bet – AI casino picks", "url": "https://bet.example", "points": 50}]}
        with mock.patch.object(sources, "request", return_value=(200, {}, json.dumps(hits).encode())):
            self.assertEqual(sources.hacker_news(), [("tools", "video", "Clipster", "https://clipster.app/")])


class AppStoreTests(unittest.TestCase):
    def test_only_established_on_topic_apps_with_their_own_site(self):
        from pipeline import sources
        def app(name, site, genre="Education", rating="4+", n=50000, desc="Learn coding for kids with games"):
            return {"trackName": name, "sellerUrl": site, "primaryGenreName": genre, "contentAdvisoryRating": rating,
                    "userRatingCount": n, "averageUserRating": 4.7, "description": desc}
        results = {"results": [app("Tynker - Coding for Kids", "https://www.tynker.com"),
                               app("Coding for Kids", "https://x.example"),             # generic name
                               app("Gamey", "https://g.example", genre="Games"),          # not Education
                               app("Teeny", "https://t.example", rating="9+"),            # not rated 4+
                               app("Tiny", "https://tiny.example", n=50),                 # too few ratings
                               app("Appy", "https://apps.apple.com/app/1"),               # not its own site
                               app("Math Solver Pro", "https://m.example")]}              # answer app
        with mock.patch.object(sources, "APPSTORE_SEARCH", {"kids": {"coding": "coding for kids"}}), \
             mock.patch.object(sources, "request", return_value=(200, {}, json.dumps(results).encode())), \
             mock.patch.object(sources.time, "sleep"):
            self.assertEqual(sources.app_store(), [("kids", "coding", "Tynker", "https://www.tynker.com/")])


class SharedToMainTests(unittest.TestCase):
    def test_sister_videos_also_go_to_main_instagram_and_linkedin(self):
        from pipeline import buffer, zernio
        own = {("facebook", "kids"), ("instagram", "main")}
        with mock.patch.object(zernio, "enabled", return_value=True), \
             mock.patch.object(zernio, "available", side_effect=lambda p, ch="main": (p, ch) in own), \
             mock.patch.object(buffer, "enabled", return_value=True), \
             mock.patch.object(buffer, "available", side_effect=lambda s: s == "linkedin"), \
             mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(set(publish.platforms("kids")), {"facebook", "facebook_story", "instagram", "linkedin"})
            with mock.patch.dict(os.environ, {"SISTER_TO_MAIN": ""}):
                self.assertEqual(set(publish.platforms("kids")), {"facebook", "facebook_story"})
