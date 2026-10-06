# AI News & Open-Source Video Pipeline

Autonomous, low-cost pipeline: discover → filter → research → verify → score → generate → render → QC → publish → analyze → learn.
Design rules live in [Skill.md](Skill.md). Stack: Python stdlib + Pillow, SQLite, FFmpeg, GitHub Actions.

## Run locally

```bash
pip install -r requirements.txt        # needs ffmpeg/ffprobe on PATH
python -m pipeline collect             # discover + filter (cheap metadata)
python -m pipeline research            # research + verify + score
python -m pipeline produce             # generate + render + QC + publish
python -m pipeline all                 # everything
python -m unittest discover -s tests
```

Single stages (`discover`, `filter`, `research`, `score`, `generate`, `render`, `qc`, `publish`, `analytics`) also work.
Every stage resumes from DB state; failures retry with exponential backoff (10 min x 2^n) and end `FAILED` after `MAX_ATTEMPTS`.

## Run on GitHub (public repo)

1. Push this folder to a **public** GitHub repo.
2. Settings → Actions → General → Workflow permissions: **Read and write** (needed to save state).
3. Optional secrets (Settings → Secrets and variables → Actions). Without them the pipeline still runs, using template scripts and marking uploads `WAITING_FOR_API`:
   - `ANTHROPIC_API_KEY` – optional cheap-model script rewrite (hard-capped by the budget variables)
   - `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN` – YouTube Data API (Shorts upload)
4. Optional variables: `DAILY_AI_BUDGET_USD`, `MAX_LLM_CALLS_PER_DAY`, `MAX_VIDEOS_PER_DAY`, `MAX_RENDERS_PER_TOPIC`, `MIN_TOPIC_SCORE`, `GENERATE_SCORE`.
5. Run **Actions → pipeline → Run workflow** once; schedules take over (hourly collect, 4-hourly research, daily produce/analyze).

Notes for public repos:
- No secrets are in the code; `.env` is git-ignored. Never print or commit tokens.
- State (`pipeline.db`) is force-pushed as a single commit to the `data` branch each run, so history stays small. It contains only public data.
- Rendered videos are uploaded as workflow artifacts (7-day retention), not committed.
- Scheduled workflows are auto-disabled after 60 days without repo activity; re-enable them or push a commit.
- Public-repo Actions minutes are free; private repos have a monthly quota.

## Platform status

| Platform | Status |
|---|---|
| YouTube Shorts | Implemented (resumable upload; needs OAuth credentials) |
| TikTok / Instagram / X | Not implemented – add an adapter in `pipeline/publish.py`; until credentials exist jobs stay `WAITING_FOR_API` |
| CTA comment auto-replies | `cta_map` table stores keyword → resource → approved response; no reply bot yet |

## Known limits

- The default TTS is `edge-tts` (free, unofficial Microsoft endpoint); if it fails, videos get silent audio plus burned-in captions and an `.srt`.
- Videos are plain text slides; use Remotion only if you need richer motion.
- Analytics currently collect YouTube view/like/comment counts only; learning adjusts per-source score weights within 0.5–1.5x.
