"""PREVIEW: render a sample video (no state change, no publishing) to check voice, look and watermark.

`python -m pipeline preview` renders the fixed GitHub sample. With GENERATE_SOURCES=tools (workflow input
`sources: tools`) it builds a real AI-tools video for PREVIEW_TOOLS_CATEGORY (default: video) in an
in-memory database, so nothing is queued or published.
"""
import json
import os
import shutil
from pathlib import Path

from . import config, db, generate, render, tools

SAMPLE = {
    "hook": "Do you want a free, open-source tool to build with language models?",
    "beats": ["Meet answer-me-with-html.", "QingYunA/answer-me-with-html has 1.7k stars on GitHub.",
              "An agent skill that answers hard questions with one readable HTML page."],
    "outro": "Comment GITHUB and I'll send you the link.",
}
TOPIC = {"source": "github", "github_url": "https://github.com/QingYunA/answer-me-with-html",
         "url": "https://github.com/QingYunA/answer-me-with-html"}


def run(conn=None):
    if os.environ.get("GENERATE_SOURCES", "").strip() == "tools":
        mem = db.connect(":memory:")
        tid = tools.build(mem, os.environ.get("PREVIEW_TOOLS_CATEGORY", "video"))
        if not tid:
            return {"error": "not enough verified tools"}
        row = mem.execute("SELECT * FROM topics WHERE id=?", (tid,)).fetchone()
        hook, beats, _ = generate.tools_script(row)
        script = {"hook": hook, "beats": beats, "outro": f"Comment TOOL and I'll send you all {len(beats)} links."}
        path = render.render({"script": json.dumps(script), "title": row["title"]}, row)
    else:
        path = render.render({"script": json.dumps(SAMPLE), "title": "QingYunA/answer-me-with-html"}, TOPIC)
    dest = Path(config.OUT_DIR) / "video" / "preview.mp4"
    shutil.copy(path, dest)
    return {"video": str(dest), "voice": render.voice_id()}
