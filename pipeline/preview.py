"""PREVIEW: render a sample video (no DB, no publishing) to check voice, look and watermark."""
import json
import shutil
from pathlib import Path

from . import config, render

SAMPLE = {
    "hook": "Do you want a free, open-source tool to build with language models?",
    "beats": ["Meet answer-me-with-html.", "QingYunA/answer-me-with-html has 1.7k stars on GitHub.",
              "An agent skill that answers hard questions with one readable HTML page."],
    "outro": "Full links are in the description. Or comment GITHUB and I'll send it.",
}
TOPIC = {"source": "github", "github_url": "https://github.com/QingYunA/answer-me-with-html",
         "url": "https://github.com/QingYunA/answer-me-with-html"}


def run(conn=None):
    path = render.render({"script": json.dumps(SAMPLE), "title": "QingYunA/answer-me-with-html"}, TOPIC)
    dest = Path(config.OUT_DIR) / "video" / "preview.mp4"
    shutil.copy(path, dest)
    return {"video": str(dest), "voice": render.voice_id()}
