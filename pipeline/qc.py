"""QC: deterministic checks only. On failure, only the failed component is redone."""
import json
import logging
import shutil
import subprocess
from pathlib import Path

from . import db
from .http import url_ok

log = logging.getLogger("qc")


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height:format=duration",
         "-of", "json", path], capture_output=True, text=True, timeout=60, check=True).stdout
    return json.loads(out)


def check(conn, c, topic):
    errs = []
    p = c["video_path"]
    if not p or not Path(p).exists():
        return ["video missing"], "render"
    try:
        info = probe(p)
        vs = [s for s in info["streams"] if s["codec_type"] == "video"]
        au = [s for s in info["streams"] if s["codec_type"] == "audio"]
        dur = float(info["format"]["duration"])
        if not vs or (vs[0]["width"], vs[0]["height"]) != (1080, 1920):
            errs.append("bad dimensions")
        if not vs or vs[0]["codec_name"] != "h264":
            errs.append("video codec not h264")
        if not au or au[0]["codec_name"] != "aac":
            errs.append("audio missing/not aac")
        if not 25 <= dur <= (90 if str(topic["source"]).startswith("tools:") else 60):  # Reels limits
            errs.append(f"duration {dur:.0f}s out of range")
    except Exception as e:
        errs.append(f"probe failed: {e}")
    if not Path(p).with_suffix(".srt").exists():
        errs.append("subtitles missing")
    if errs:
        return errs, "render"
    claims = json.loads(topic["claims"] or "[]")
    if not claims or not all(x.get("source") for x in claims):
        errs.append("claims without sources")
    if not c["caption"]:
        errs.append("caption missing")
    if not c["cta_keyword"]:
        errs.append("cta missing")
    for u in json.loads(c["sources"] or "[]") + ([topic["github_url"]] if topic["github_url"] else []):
        if not url_ok(u):
            errs.append(f"url invalid: {u}")
    return errs, "content"


def run(conn):
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe not found")
    ok = 0
    rows = conn.execute(
        "SELECT c.*, t.id AS tid FROM contents c JOIN topics t ON t.id=c.topic_id "
        "WHERE t.status='QC' AND c.status='ready'").fetchall()
    for c in rows:
        topic = conn.execute("SELECT * FROM topics WHERE id=?", (c["tid"],)).fetchone()
        errs, stage = check(conn, c, topic)
        if not errs:
            conn.execute("UPDATE contents SET status='qc_passed' WHERE id=?", (c["id"],))
            db.set_status(conn, c["tid"], "READY")
            ok += 1
        else:
            log.warning("QC failed for %s: %s", c["title"], errs)
            if stage == "render":  # redo only the render, keep the generated content
                conn.execute("UPDATE contents SET video_path=NULL WHERE id=?", (c["id"],))
                if c["video_path"]:
                    Path(c["video_path"]).unlink(missing_ok=True)
                db.fail(conn, c["tid"], "; ".join(errs), resume_status="RENDERING")
            else:
                db.fail(conn, c["tid"], "; ".join(errs), resume_status="SKIPPED")
        conn.commit()
    return ok
