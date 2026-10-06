import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")

from pipeline import config, db, discover, filter as filt, engage, generate, motion, score  # noqa: E402


def topic(conn, title, url, source="github", **kw):
    discover.add(conn, title=title, source=source, url=url, github_url=kw.get("gh"),
                 repo_id=kw.get("repo_id"), published_at=db.now_iso(), raw=kw.get("raw", {"description": "an llm agent"}))


class Tests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(os.path.join(tempfile.mkdtemp(), "x.db"))

    def test_add_dedupes_by_url_and_repo_id(self):
        topic(self.conn, "a/b", "https://github.com/a/b", repo_id="gh:1")
        topic(self.conn, "a/b-renamed", "https://github.com/a/b2", repo_id="gh:1")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0], 1)

    def test_stale_non_github_items_are_dropped(self):
        n = discover.add(self.conn, title="old", source="rss:x", url="https://x/1", published_at="2020-01-01T00:00:00Z")
        self.assertEqual(n, 0)

    def test_filter_skips_similar_titles_and_irrelevant(self):
        topic(self.conn, "Llama agent toolkit released", "https://x/1", source="hn")
        filt.run(self.conn)
        topic(self.conn, "Llama agent toolkit released!", "https://x/2", source="hn")
        topic(self.conn, "Best pasta recipes", "https://x/3", source="hn", raw={})
        kept, skipped = filt.run(self.conn)
        self.assertEqual((kept, skipped), (0, 2))

    def test_curated_rss_items_are_relevant_and_reopened(self):
        discover.add(self.conn, title="Introducing our newest thing", source="rss:openai", url="https://x/n1",
                     published_at=db.now_iso(), raw={"description": "a post"})
        tid = self.conn.execute("SELECT id FROM topics").fetchone()[0]
        db.set_status(self.conn, tid, "SKIPPED", "irrelevant")
        filt.run(self.conn)
        self.assertEqual(self.conn.execute("SELECT status FROM topics").fetchone()[0], "FILTERED")

    def test_daily_quota_picks_two_github_and_two_news(self):
        mk = lambda src, sc: {"source": src, "score": sc}
        rows = [mk("github", 90), mk("github", 85), mk("github", 82), mk("rss:aws-ml", 70), mk("hn", 68),
                mk("arxiv", 66), mk("rss:openai", 40)]
        got = generate.pick(rows, 2, 2, 80, 65)
        self.assertEqual([(r["source"], r["score"]) for r in got],
                         [("github", 90), ("github", 85), ("rss:aws-ml", 70), ("hn", 68)])
        self.assertEqual(generate.pick(rows, 0, 1, 80, 65)[0]["source"], "rss:aws-ml")
        self.assertEqual(generate.pick(rows, 2, 2, 95, 99), [])

    def test_backoff_then_failed(self):
        topic(self.conn, "x/y", "https://github.com/x/y", repo_id="gh:2")
        tid = self.conn.execute("SELECT id FROM topics").fetchone()[0]
        for _ in range(config.MAX_ATTEMPTS - 1):
            db.fail(self.conn, tid, "boom", "FILTERED")
        self.assertEqual(self.conn.execute("SELECT status FROM topics").fetchone()[0], "RETRY")
        db.fail(self.conn, tid, "boom", "FILTERED")
        self.assertEqual(self.conn.execute("SELECT status FROM topics").fetchone()[0], "FAILED")

    def test_retry_requeues_only_when_due(self):
        topic(self.conn, "x/y", "https://github.com/x/y", repo_id="gh:2")
        tid = self.conn.execute("SELECT id FROM topics").fetchone()[0]
        db.fail(self.conn, tid, "boom", "FILTERED")
        db.requeue_retries(self.conn)
        self.assertEqual(self.conn.execute("SELECT status FROM topics").fetchone()[0], "RETRY")
        self.conn.execute("UPDATE topics SET next_retry_at=?", (time.time() - 1,))
        db.requeue_retries(self.conn)
        self.assertEqual(self.conn.execute("SELECT status FROM topics").fetchone()[0], "FILTERED")

    def test_score_bounds_and_weight_clamp(self):
        topic(self.conn, "x/y", "https://github.com/x/y", repo_id="gh:3",
              raw={"stars": 5000, "license": "MIT", "description": "open llm agent"})
        self.conn.execute("UPDATE topics SET summary='open llm agent', confidence=100")
        self.conn.execute("INSERT INTO weights VALUES ('src:github', 99)")  # must be clamped to 1.5
        row = self.conn.execute("SELECT * FROM topics").fetchone()
        s = score.score_row(row, score.weights(self.conn))
        self.assertTrue(0 <= s <= 100)

    def test_cta_choice(self):
        row = {"github_url": "https://github.com/a/b", "source": "github", "url": "u"}
        self.assertEqual(generate.pick_cta(row)[0], "GITHUB")
        self.assertEqual(generate.pick_cta({"github_url": None, "source": "arxiv", "url": "u"})[0], "DOCS")

    def test_clean_claim_strips_cjk_and_emoji(self):
        c = generate.clean_claim("Description: Answer me with HTML — an agent skill. 🐱 AI Agent 让智能体")
        self.assertNotIn("让", c)
        self.assertNotIn("Description", c)
        self.assertIsNone(generate.clean_claim("让智能体用网页回答问题"))
        self.assertLessEqual(len(generate.clean_claim("word " * 80)), 145)

    def test_easing_bounds_and_monotonic(self):
        xs = [i / 10 for i in range(11)]
        for f in (motion.ease_out, motion.ease_in_out):
            ys = [f(x) for x in xs]
            self.assertEqual((ys[0], ys[-1]), (0.0, 1.0))
            self.assertEqual(ys, sorted(ys))
            self.assertEqual((f(-1), f(2)), (0.0, 1.0))

    def test_word_starts_increase_within_duration(self):
        s = motion.word_starts("one two three four five", 4.0)
        self.assertEqual(s[0], 0.0)
        self.assertEqual(s, sorted(s))
        self.assertLess(s[-1], 4.0)

    def test_long_word_shrinks_font_to_fit(self):
        words, fnt, lines, size, lh = motion.text_layout("x" * 60 + " ok")
        self.assertLessEqual(max(fnt.getlength(w) for w in words), motion.W - 2 * motion.MARGIN)

    def test_template_hook_is_a_question_and_claims_free_only_with_license(self):
        row = {"title": "a/b", "source": "github", "raw": json.dumps({"topics": ["llm"], "license": "MIT"})}
        hook, beats = generate.template_script(row, [{"text": "a/b has 1k stars on GitHub."}])
        self.assertTrue(hook.endswith("?"))
        self.assertIn("free", hook)
        self.assertEqual(beats[0], "Meet b.")
        row["raw"] = json.dumps({"topics": ["llm"]})
        hook, _ = generate.template_script(row, [])
        self.assertTrue(hook.endswith("?"))
        self.assertNotIn("free", hook)

    def test_llm_skipped_without_key(self):
        config.ANTHROPIC_API_KEY = ""
        self.assertIsNone(generate.llm_rewrite(self.conn, {"title": "t"}, []))

    def test_llm_blocked_when_budget_exhausted(self):
        config.ANTHROPIC_API_KEY = "k"
        old = config.DAILY_AI_BUDGET_USD
        config.DAILY_AI_BUDGET_USD = 0.0
        try:
            self.assertIsNone(generate.llm_rewrite(self.conn, {"title": "t"}, [{"text": "f"}]))
        finally:
            config.ANTHROPIC_API_KEY, config.DAILY_AI_BUDGET_USD = "", old

    def test_keyword_match_is_whole_word_and_case_insensitive(self):
        self.assertTrue(engage.matches("github please!", "GITHUB"))
        self.assertTrue(engage.matches("Link? GITHUB", "GITHUB"))
        self.assertFalse(engage.matches("githubs are cool", "GITHUB"))
        self.assertFalse(engage.matches("send CODE", "GITHUB"))
        self.assertFalse(engage.matches(None, "GITHUB"))


if __name__ == "__main__":
    unittest.main()
