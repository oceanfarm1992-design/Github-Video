"""CLI: python -m pipeline <stage|all>. Each stage is idempotent and resumable from DB state."""
import argparse
import logging
import sys

from . import analytics, db, engage, longform, preview, zernio_check, discover, tools, sites, promo, voicetest, filter as filt, generate, publish, qc, ranking, recap, render, research, score, trending

STAGES = {
    "collect": ["discover", "filter"],           # every 30-60 min: cheap metadata
    "research": ["filter", "research", "score"],  # every 3-6 h: only candidates
    "produce": ["trending", "tools", "sites", "promo", "weekly", "generate", "render", "qc", "publish"],  # daily
    "engage": ["engage"],                         # every 30 min: answer keyword comments
    "draft": ["trending", "tools", "sites", "promo", "weekly", "generate", "render", "qc"],  # make + check videos, never publishes
    "preview": ["preview"],                      # sample render, never publishes
    "voicetest": ["voicetest"],                   # cloned-voice A/B samples, never publishes
    "zernio": ["zernio"],                         # read-only: list Zernio accounts + automations
    "long": ["long"],                             # 3x a week: 8-15 min video for YouTube + Facebook
    "longpreview": ["longpreview"],               # long video render only (sources = github|tools|sites)
    "analyze": ["analytics"],                    # daily/weekly
    "all": ["discover", "filter", "research", "score", "tools", "sites", "promo", "generate", "render", "qc", "publish", "engage", "analytics"],
}


def run_stage(name, conn):
    if name == "discover":
        return discover.run(conn)
    if name == "filter":
        return filt.run(conn)
    if name == "voicetest":
        return voicetest.run(conn)
    if name == "trending":  # No. 1 GitHub repo of the day
        return trending.run(conn)
    if name == "tools":
        return tools.run(conn)
    if name == "promo":
        return promo.run(conn)
    if name == "sites":
        return sites.run(conn)
    if name == "weekly":  # weekly ranking, weekly recap, and starting today's long video (Mon/Wed/Fri)
        return {"ranking": ranking.run(conn), "recap": recap.run(conn), "long": longform.dispatch_if_due(conn)}
    if name == "long":
        return longform.run(conn)
    if name == "longpreview":
        import os
        return longform.run(conn, fmt=(os.environ.get("GENERATE_SOURCES") or "github").strip(), publish_it=False)
    if name == "research":
        return research.run(conn)
    if name == "score":
        return score.run(conn)
    if name == "generate":
        return generate.run(conn)
    if name == "render":
        return render.run(conn)
    if name == "qc":
        return qc.run(conn)
    if name == "publish":
        return publish.run(conn)
    if name == "engage":
        return engage.run(conn)
    if name == "preview":
        return preview.run(conn)
    if name == "zernio":
        return zernio_check.run(conn)
    if name == "analytics":
        return analytics.run(conn)
    raise SystemExit(f"unknown stage {name}")


def stats(conn):
    rows = conn.execute("SELECT status, COUNT(*) n FROM topics GROUP BY status ORDER BY n DESC").fetchall()
    return {r["status"]: r["n"] for r in rows}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pipeline")
    ap.add_argument("stage", choices=list(STAGES) + [s for v in STAGES.values() for s in v])
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format='{"t":"%(asctime)s","lvl":"%(levelname)s","mod":"%(name)s","msg":"%(message)s"}')
    log = logging.getLogger("main")
    with db.db() as conn:
        db.requeue_retries(conn)
        for s in STAGES.get(a.stage, [a.stage]):
            try:
                log.info("stage %s -> %s", s, run_stage(s, conn))
            except Exception as e:  # a stage failure must not prevent later stages from resuming
                log.error("stage %s failed: %s", s, e)
            conn.commit()
        try:  # keep the public state database small
            if db.prune(conn):
                conn.commit()
                conn.execute("VACUUM")
        except Exception as e:
            log.warning("prune failed: %s", e)
        log.info("status: %s", stats(conn))
    return 0


if __name__ == "__main__":
    sys.exit(main())
