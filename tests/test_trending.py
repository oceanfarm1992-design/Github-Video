import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import db, seo, storyboard, trending  # noqa: E402

PAGE = """
<article class="Box-row"><h2 class="h3 lh-condensed">
  <a data-x="{}" href="/old/one" class="Link">old / one</a></h2> <span>1,204 stars today</span></article>
<article class="Box-row"><h2 class="h3 lh-condensed">
  <a href="/new/two" class="Link">new / two</a></h2> <span>307 stars today</span></article>
"""


def api(full):
    return {"id": hash(full) % 10**6, "html_url": f"https://github.com/{full}", "stargazers_count": 5400,
            "description": "A fast tool for running AI agents.", "language": "Rust", "topics": ["ai"],
            "license": {"spdx_id": "MIT"}, "created_at": "2026-10-01T00:00:00Z",
            "pushed_at": "2026-10-16T00:00:00Z", "homepage": "", "fork": False, "archived": False}


class TrendingTests(unittest.TestCase):
    def test_parse_keeps_github_order_and_stars_today(self):
        self.assertEqual(trending.parse(PAGE), [(1, "old/one", 1204), (2, "new/two", 307)])

    def test_build_skips_covered_repo_and_keeps_true_rank(self):
        conn = db.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
        conn.execute("INSERT INTO topics (id,title,source,url,status) VALUES ('x','old/one','github','u','PUBLISHED')")
        old = trending.get_json
        trending.get_json = lambda conn, url, h=None, ttl=0: (api(url.split("/repos/")[1]), True)
        try:
            tid = trending.build(conn, trending.parse(PAGE))
        finally:
            trending.get_json = old
        row = conn.execute("SELECT * FROM topics WHERE id=?", (tid,)).fetchone()
        self.assertEqual(row["title"], "new/two")
        self.assertEqual((row["status"], row["score"]), ("QUEUED", 100))
        raw = json.loads(row["raw"])
        self.assertEqual(raw["trending"]["rank"], 2)  # never called "No. 1" when it was No. 2
        c = {"tsource": "github", "title": "new/two", "hook": "h", "traw": row["raw"], "hashtags": "[]",
             "script": json.dumps({"beats": []})}
        self.assertTrue(seo.title(c).startswith("No. 2 on GitHub Trending Today: two"))
        self.assertEqual(storyboard.intro("github", dict(row), "new/two")["tag"], "No. 2 ON GITHUB TRENDING")

    def test_number_one_label(self):
        raw = {"trending": {"rank": 1, "stars_today": 1204}, "description": "A fast tool."}
        c = {"tsource": "github", "title": "old/one", "hook": "h", "traw": json.dumps(raw), "hashtags": "[]",
             "script": json.dumps({"beats": []})}
        self.assertEqual(seo.title(c), "No. 1 GitHub Repo of the Day: one | A fast tool")
        self.assertIn("GitHub repo of the day", seo.youtube_tags(c))


if __name__ == "__main__":
    unittest.main()
