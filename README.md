# AI News & Open-Source Video Pipeline

Autonomous, low-cost pipeline: discover â†’ filter â†’ research â†’ verify â†’ score â†’ generate â†’ render â†’ QC â†’ publish â†’ analyze â†’ learn.
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
2. Settings â†’ Actions â†’ General â†’ Workflow permissions: **Read and write** (needed to save state).
3. Optional secrets (Settings â†’ Secrets and variables â†’ Actions). Without them the pipeline still runs, using template scripts and marking uploads `WAITING_FOR_API`:
   - `ANTHROPIC_API_KEY` â€“ optional cheap-model script rewrite (hard-capped by the budget variables)
   - `ZERNIO_API_KEY` - preferred for Facebook/Instagram: publishes Reels through [Zernio](https://docs.zernio.com) and creates a keyword -> DM comment automation per post (replaces the direct Meta credentials below and the built-in Meta DM replies; Zernio is a third-party service)
   - `META_PAGE_ID`, `META_PAGE_ACCESS_TOKEN`, `META_IG_USER_ID` – Facebook Page Reels + Instagram Reels (Meta Graph API; the IG account must be a Business/Creator account linked to the Page)
   - `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN` â€“ YouTube Data API (Shorts upload)
4. Optional variables: `DAILY_AI_BUDGET_USD`, `MAX_LLM_CALLS_PER_DAY`, `MAX_VIDEOS_PER_DAY`, `MAX_RENDERS_PER_TOPIC`, `MIN_TOPIC_SCORE`, `GENERATE_SCORE`.
5. Run **Actions â†’ pipeline â†’ Run workflow** once; schedules take over (hourly collect, 4-hourly research, daily produce/analyze).

Notes for public repos:
- No secrets are in the code; `.env` is git-ignored. Never print or commit tokens.
- State (`pipeline.db`) is force-pushed as a single commit to the `data` branch each run, so history stays small. It contains only public data.
- Rendered videos are uploaded as workflow artifacts (7-day retention), not committed.
- Scheduled workflows are auto-disabled after 60 days without repo activity; re-enable them or push a commit.
- Public-repo Actions minutes are free; private repos have a monthly quota.

## Comment -> link replies (Supabase)

Viewers comment the video's one keyword (`GITHUB`, `TOOL`, `CODE`, `DOCS`, `DEMO`, `SOURCE`); the `engage` stage (every 30 min) replies with the approved link.
- YouTube: the approved link is put in the video description and the outro says so; no comment bot by default (set `YOUTUBE_COMMENT_REPLIES=1` and add the `youtube.force-ssl` scope to enable public replies). Facebook/Instagram: private reply (DM) to the comment.
- Comment events hold personal data, so they live only in Supabase (RLS locked), never in the public `data` branch.
- One reply per comment (unique key) and one link per commenter per video; `MAX_REPLIES_PER_RUN` caps volume.
- Setup: run `supabase/schema.sql` in the Supabase SQL editor; add secrets `SUPABASE_URL` and `SUPABASE_SECRET_KEY` (the `sb_secret_...` key; the publishable key and JWKS URL are not needed); Instagram/Facebook DMs also need `instagram_manage_messages` / `pages_messaging` on the Meta token.
- Meta private replies only work within 7 days of the comment; YouTube may hold link comments for review.

## Platform status

| Platform | Status |
|---|---|
| YouTube Shorts | Implemented (resumable upload; needs OAuth credentials) |
| TikTok / Instagram / X | Not implemented â€“ add an adapter in `pipeline/publish.py`; until credentials exist jobs stay `WAITING_FOR_API` |
| CTA comment auto-replies | `cta_map` table stores keyword â†’ resource â†’ approved response; no reply bot yet |

## Known limits

- The default TTS is `edge-tts` (free, unofficial Microsoft endpoint); if it fails, videos get silent audio plus burned-in captions and an `.srt`.
- Videos are plain text slides; use Remotion only if you need richer motion.
- Analytics currently collect YouTube view/like/comment counts only; learning adjusts per-source score weights within 0.5â€“1.5x.
