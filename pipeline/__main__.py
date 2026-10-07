"""CLI: python -m pipeline <stage|all>. Each stage is idempotent and resumable from DB state."""
import argparse
import logging
import sys

from . import analytics, db, engage, preview, zernio_check, discover, tools, sites, filter as filt, generate, publish, qc, render, research, score

STAGES = {
    "collect": ["discover", "filter"],           # every 30-60 min: cheap metadata
    "research": ["filter", "research", "score", "tools", "sites"],  # every 3-6 h: only candidates
    "produce": ["tools", "sites", "generate", "render", "qc", "publish"],  # daily
    "engage": ["engage"],                         # every 30 min: answer keyword comments
    "draft": ["tools", "sites", "generate", "render", "qc"],         # make + check videos, never publishes
    "preview": ["preview"],                      # sample render, never publishes
    "zernio": ["zernio"],                         # read-only: list Zernio accounts + automations
    "analyze": ["analytics"],                    # daily/weekly
    "all": ["discover", "filter", "research", "score", "tools", "sites", "generate", "render", "qc", "publish", "engage", "analytics"],
}


def run_stage(name, conn):
    if name == "discover":
        return discover.run(conn)
    if name == "filter":
        return filt.run(conn)
    if name == "tools":
        return tools.run(conn)
    if name == "sites":
        return sites.run(conn)
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
        log.info("status: %s", stats(conn))
    return 0


if __name__ == "__main__":
    sys.exit(main())
