"""Real GitHub data for the visuals: repo stats, star history and the README's install command.

Everything shown on screen comes from GitHub's API or from our own hourly star counts; the only
estimate is the straight line drawn between two real samples (the same method as star-history.com).
"""
import calendar
import json
import logging
import re
import time
import urllib.parse
from pathlib import Path

from .http import gh_headers, request

log = logging.getLogger("ghdata")
API = "https://api.github.com"


def repo_name(url):
    m = re.match(r"https?://github\.com/([^/\s]+)/([^/\s#?]+)", url or "")
    return f"{m.group(1)}/{m.group(2).removesuffix('.git')}" if m else None


def _get(path, accept=None):
    h = gh_headers()
    if accept:
        h = {**h, "Accept": accept}
    _, _, body = request(API + path, headers=h, attempts=2, timeout=20)
    return body


def repo(full):
    r = json.loads(_get(f"/repos/{full}"))
    return {"name": r["full_name"], "stars": r["stargazers_count"], "forks": r["forks_count"],
            "language": r.get("language"), "license": (r.get("license") or {}).get("spdx_id"),
            "pushed_at": r.get("pushed_at"), "created_at": r.get("created_at"),
            "owner": r["owner"]["login"], "avatar": r["owner"].get("avatar_url")}


def _ts(iso):
    return calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))


def record_stars(conn, full, stars, every=3 * 3600):
    """Stores a star-count sample for a repo (at most one per `every` seconds). GitHub no longer lists
    who starred a repo or when, so the growth curve is built from our own hourly observations."""
    last = conn.execute("SELECT MAX(t) FROM star_snapshots WHERE repo=?", (full,)).fetchone()[0]
    now = time.time()
    if last is None or now - last >= every:
        conn.execute("INSERT OR IGNORE INTO star_snapshots (repo, t, stars) VALUES (?,?,?)", (full, now, stars))


def star_history(conn, full, total, created_at=None):
    """[(unix_time, stars)] from creation (0 stars) through our samples to now; [] unless at least two
    real samples lie between, so the curve is never just an invented straight line."""
    rows = conn.execute("SELECT t, stars FROM star_snapshots WHERE repo=? ORDER BY t", (full,)).fetchall()         if conn else []
    samples = [(r[0], r[1]) for r in rows if r[1] < total or r[0] < time.time() - 3600]
    if len(samples) < 2:
        return []
    points = ([(_ts(created_at), 0)] if created_at else []) + samples + [(time.time(), total)]
    out = []
    for t, s in sorted(points):
        if not out or (s >= out[-1][1] and t > out[-1][0]):
            out.append((t, s))
    return out


INSTALL_RE = re.compile(r"^(pip3? install|pipx install|uv (pip|tool|add)|uvx |npm (i|install)\b|npx |pnpm (add|i|dlx)|"
                        r"yarn (add|dlx)|bun (add|x)|git clone|docker (run|pull|compose)|brew install|"
                        r"cargo install|go install|curl .*\| *(ba)?sh|winget install|conda install)")
SECRET_RE = re.compile(r"(key|token|secret|password)\s*=", re.I)


def install_commands(full, max_lines=3, max_len=60):
    """Up to `max_lines` install commands from the README's code blocks, exactly as written there."""
    try:
        text = _get(f"/repos/{full}/readme", "application/vnd.github.raw").decode("utf-8", "replace")
    except RuntimeError as e:
        log.info("no README for %s: %s", full, e)
        return []
    out = []
    for block in re.findall(r"```[\w+-]*\n(.*?)```", text, re.S):
        for line in block.splitlines():
            line = line.strip().removeprefix("$ ").removeprefix("> ").strip()
            if INSTALL_RE.match(line) and not SECRET_RE.search(line) and len(line) <= max_len and line not in out:
                out.append(line)
        if out:  # one block: the commands belong together
            break
    return out[:max_lines]


def fetch_image(url, dest, min_size=48):
    """Downloads an image (logo, avatar) to `dest`; None if missing or too small to look good."""
    from PIL import Image
    try:
        _, _, body = request(url, attempts=2, timeout=15)
        Path(dest).write_bytes(body)
        with Image.open(dest) as im:
            if min(im.size) < min_size:
                return None
        return str(dest)
    except Exception as e:
        log.info("image %s unavailable: %s", url, e)
        return None


def logo(site_url, out_dir):
    """The site's own icon (via Google's favicon service), or None when it has none worth showing."""
    host = urllib.parse.urlparse(site_url).netloc
    if not host:
        return None
    dest = Path(out_dir) / f"logo_{re.sub(r'[^a-z0-9]', '_', host.lower())}.png"
    if dest.exists():
        return str(dest)
    return fetch_image(f"https://www.google.com/s2/favicons?domain={host}&sz=256", dest)


def for_video(url, out_dir, conn=None):
    """Everything the GitHub visuals need, or {} when the API is unreachable."""
    full = repo_name(url)
    if not full:
        return {}
    try:
        info = repo(full)
    except Exception as e:
        log.warning("repo stats for %s unavailable: %s", full, e)
        return {}
    try:
        info["history"] = star_history(conn, full, info["stars"], info.get("created_at"))
    except Exception as e:
        log.warning("star history for %s unavailable: %s", full, e)
        info["history"] = []
    info["install"] = install_commands(full)
    return info
