import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")

from pipeline import db, engage, supa  # noqa: E402


class FakeSupa:
    """In-memory stand-in for the Supabase helpers."""
    def __init__(self):
        self.t = {"cta_links": [], "comment_events": []}
        self.n = 0

    def configured(self):
        return True

    def select(self, table, **f):
        rows = self.t[table]
        for k, v in f.items():
            if k == "select":
                continue
            op, _, val = v.partition(".")
            if op == "eq":
                rows = [r for r in rows if str(r.get(k)) == val]
            elif op == "gte":
                rows = [r for r in rows if (r.get(k) or "") >= val]
        return [dict(r) for r in rows]

    def upsert(self, table, row, on_conflict):
        keys = on_conflict.split(",")
        for r in self.t[table]:
            if all(r[k] == row[k] for k in keys):
                r.update(row)
                return
        self.t[table].append(dict(row))

    def insert_once(self, table, row, on_conflict):
        keys = on_conflict.split(",")
        if any(all(r[k] == row[k] for k in keys) for r in self.t[table]):
            return None
        self.n += 1
        r = {"id": self.n, "status": "pending", **row}
        self.t[table].append(r)
        return dict(r)

    def update(self, table, row, **f):
        for r in self.select(table, **f):
            for real in self.t[table]:
                if real["id"] == r["id"]:
                    real.update(row)

    def delete(self, table, **f):
        ids = {r["id"] for r in self.select(table, **f)}
        self.t[table] = [r for r in self.t[table] if r["id"] not in ids]


class EngageTests(unittest.TestCase):
    def test_prefilter(self):
        self.assertEqual(engage.prefilter({"body": "great", "is_owner": True}), "own comment")
        self.assertEqual(engage.prefilter({"body": "\U0001f525\U0001f525"}), "no text")
        self.assertEqual(engage.prefilter({"body": "check my channel for crypto"}), "spam")
        self.assertEqual(engage.prefilter({"body": "visit https://x.io now"}), "spam")
        self.assertIsNone(engage.prefilter({"body": "Does this run offline?"}))

    def test_valid_reply_blocks_links_mentions_hashtags(self):
        self.assertTrue(engage.valid_reply("Good question - details are in the description."))
        for bad in ("see https://x.io", "thanks @bob", "love it #ai", "", "x" * 300):
            self.assertFalse(engage.valid_reply(bad))

    def test_link_ask_detection(self):
        self.assertTrue(engage.LINK_ASK_RE.search("Can I get the link?"))
        self.assertTrue(engage.LINK_ASK_RE.search("where can I find the repo"))
        self.assertFalse(engage.LINK_ASK_RE.search("This is amazing"))

    def test_full_flow_youtube(self):
        fake = FakeSupa()
        conn = db.connect(os.path.join(tempfile.mkdtemp(), "e.db"))
        now = db.now_iso()
        conn.execute("INSERT INTO topics (id,title,source,url,claims,status) VALUES ('t1','Cool Model','rss:x','https://x/a',?, 'PUBLISHED')",
                     (json.dumps([{"text": "Cool Model is open-weight.", "source": "https://x/a"}]),))
        conn.execute("INSERT INTO contents (id,topic_id,status) VALUES (1,'t1','qc_passed')")
        conn.execute("INSERT INTO cta_map VALUES (1,'SOURCE','t1','Here''s the link: https://x/a')")
        conn.execute("INSERT INTO posts (platform,post_id,content_id,published_at,status,url) VALUES ('youtube','vid1',1,?,'PUBLISHED','')", (now,))
        comments = [
            {"comment_id": "c1", "author_id": "u1", "author_name": "A", "body": "SOURCE please", "is_owner": False},
            {"comment_id": "c2", "author_id": "u2", "author_name": "B", "body": "Is it really open-weight?", "is_owner": False},
            {"comment_id": "c3", "author_id": "u3", "author_name": "C", "body": "join my telegram", "is_owner": False},
            {"comment_id": "c4", "author_id": "u9", "author_name": "Me", "body": "thanks all", "is_owner": True},
            {"comment_id": "c5", "author_id": "u2", "author_name": "B", "body": "also nice video", "is_owner": False},
        ]
        sent, cache_flags = [], []

        def fake_llm(conn, prompt, max_tokens=0, cache=True):
            cache_flags.append(cache)
            return [{"id": "c2", "action": "reply", "reply": "Yes - it's open-weight, per the announcement."},
                    {"id": "c5", "action": "reply", "reply": "Thank you!"}]

        patches = {
            (supa, "configured"): fake.configured, (supa, "select"): fake.select, (supa, "upsert"): fake.upsert,
            (supa, "insert_once"): fake.insert_once, (supa, "update"): fake.update, (supa, "delete"): fake.delete,
            (engage, "youtube_token"): lambda: "tok", (engage, "yt_own_channel"): lambda t: "me",
            (engage, "yt_comments"): lambda *a: iter(comments),
            (engage, "yt_reply"): lambda tok, cid, text: sent.append((cid, text)),
            (engage.llm, "call_json"): fake_llm,
        }
        engage.REPLY_DELAY = (0, 0)
        olds = {k: getattr(*k) for k in patches}
        try:
            for k, v in patches.items():
                setattr(k[0], k[1], v)
            stats = engage.run(conn)
            again = engage.run(conn)  # second run must not reply twice
        finally:
            for k, v in olds.items():
                setattr(k[0], k[1], v)
        self.assertEqual(stats["replies"], 2, stats)
        self.assertEqual(sent[0], ("c1", "Thanks! The link is in the description."))
        self.assertEqual(sent[1][0], "c2")
        self.assertEqual(len(sent), 2)  # c3 spam, c4 own, c5 same author as c2 -> no reply
        self.assertEqual(again["replies"], 0)
        self.assertTrue(cache_flags and not any(cache_flags))  # comments must never be cached (public state DB)
        status = {e["comment_id"]: e["status"] for e in fake.t["comment_events"]}
        self.assertEqual(status, {"c1": "replied", "c2": "replied", "c3": "skipped", "c4": "skipped", "c5": "skipped"})


class DmFallbackTests(unittest.TestCase):
    def test_keyword_comment_gets_dm_unless_automation_already_sent_it(self):
        fake = FakeSupa()
        conn = db.connect(os.path.join(tempfile.mkdtemp(), "z.db"))
        conn.execute("INSERT INTO topics (id,title,source,url,claims,status) VALUES ('t1','Repo','github','https://g/r','[]','PUBLISHED')")
        conn.execute("INSERT INTO contents (id,topic_id,status) VALUES (1,'t1','qc_passed')")
        conn.execute("INSERT INTO cta_map VALUES (1,'GITHUB','t1','Here''s the link: https://g/r')")
        conn.execute("INSERT INTO posts (platform,post_id,content_id,published_at,status,url) VALUES ('instagram','zp1',1,?,'PUBLISHED','')",
                     (db.now_iso(),))
        comments = [
            {"comment_id": "k1", "author_id": "a1", "author_name": "A", "body": "github please", "is_owner": False},
            {"comment_id": "k2", "author_id": "a2", "author_name": "B", "body": "GITHUB", "is_owner": False},
        ]
        dms, public = [], []

        def fake_private(pid, plat, cid, text):
            if cid == "k2":
                return "already"  # the Zernio automation got there first
            dms.append((cid, text))
            return "sent"

        patches = {
            (supa, "configured"): fake.configured, (supa, "select"): fake.select, (supa, "upsert"): fake.upsert,
            (supa, "insert_once"): fake.insert_once, (supa, "update"): fake.update, (supa, "delete"): fake.delete,
            (engage.zernio, "available"): lambda plat: True, (engage.zernio, "enabled"): lambda: True,
            (engage.zernio, "list_comments"): lambda pid, plat: iter(comments),
            (engage.zernio, "private_reply"): fake_private,
            (engage.zernio, "reply_comment"): lambda pid, plat, cid, text: public.append((cid, text)),
        }
        engage.REPLY_DELAY = (0, 0)
        olds = {k: getattr(*k) for k in patches}
        try:
            for k, v in patches.items():
                setattr(k[0], k[1], v)
            stats = engage.run(conn)
        finally:
            for k, v in olds.items():
                setattr(k[0], k[1], v)
        self.assertEqual([d[0] for d in dms], ["k1"])
        self.assertIn("https://g/r", dms[0][1])
        self.assertIn(dms[0][1], engage.zernio.dm_messages("Repo", "Here's the link: https://g/r"))
        self.assertNotEqual(dms[0][1].strip(), "Here's the link: https://g/r")  # never a bare link (spam folder)
        self.assertEqual(public, [("k1", "Check your inbox, thank you!")])
        self.assertEqual(stats["replies"], 1)
        status = {e["comment_id"]: (e["status"], e.get("error")) for e in fake.t["comment_events"]}
        self.assertEqual(status["k1"][0], "replied")
        self.assertEqual(status["k2"], ("skipped", "DM already sent by automation"))


if __name__ == "__main__":
    unittest.main()
