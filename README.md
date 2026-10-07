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
4. Daily mix (4 videos, made in two produce slots at 09:05 and 14:05 UTC, max 2 per run): `DAILY_GITHUB_VIDEOS` (2), `DAILY_TOOLS_VIDEOS` (1), `DAILY_SITES_VIDEOS` (1), `DAILY_NEWS_VIDEOS` (0, AI news is off); score bars `GENERATE_SCORE` (75, GitHub) and `GENERATE_SCORE_NEWS` (65, news).
   AI-tools videos ("10 AI tools for video generation"): one catalog category per video (`pipeline/tools.py`), each tool's homepage opened in Chromium and described only with its own site description; dead or empty sites are skipped. `TOOLS_PER_VIDEO` (10). Facebook is skipped for videos over 60 s (Reels limit); YouTube and Instagram get the full video.
   Websites series ("Websites that feel illegal to know - Part N", all legal): `SITES_PER_VIDEO` (5) sites per part from the curated catalog in `pipeline/sites.py`, never repeated; no piracy, paywall-bypass, people-search or open-camera sites. Comment keyword `LINK`.
   Preview without publishing: Actions -> pipeline -> Run workflow, stage `preview`, sources `tools` or `sites`.
5. Optional variables: `DAILY_AI_BUDGET_USD`, `MAX_LLM_CALLS_PER_DAY`, `MAX_VIDEOS_PER_DAY`, `MAX_RENDERS_PER_TOPIC`, `MIN_TOPIC_SCORE`, `GENERATE_SCORE`.
6. Run **Actions â†’ pipeline â†’ Run workflow** once; schedules take over (hourly collect, 4-hourly research, daily produce/analyze).

Notes for public repos:
- No secrets are in the code; `.env` is git-ignored. Never print or commit tokens.
- State (`pipeline.db`) is force-pushed as a single commit to the `data` branch each run, so history stays small. It contains only public data.
- Rendered videos are uploaded as workflow artifacts (7-day retention), not committed.
- Scheduled workflows are auto-disabled after 60 days without repo activity; re-enable them or push a commit.
- Public-repo Actions minutes are free; private repos have a monthly quota.

## Comment replies (Supabase)

The `engage` stage (every 30 min) answers follower comments automatically, on YouTube, and on Facebook/Instagram through Zernio:
- Skipped: your own comments, emoji-only/empty, spam (links, "telegram", "crypto"...), and anything the model flags as insult/trolling/politics.
- "Link?" / "source?" questions get a fixed approved reply (YouTube: "link is in the description"; Facebook/Instagram: "comment KEYWORD and I'll DM you the link").
- The CTA keyword on Facebook/Instagram is answered by Zernio's comment-to-DM automation (created at publish time).
- All other comments are answered by the cheap model in one batched call per video, using only the video's sourced facts; replies are 4-25 words, no links/hashtags/@mentions.
- Safeguards: one reply per person per video, `MAX_REPLIES_PER_RUN` (40) and `MAX_REPLIES_PER_DAY` (150) caps, random 1.5-4 s spacing, LLM budget caps, idempotent reply log.
- Comment events (personal data) live only in Supabase (RLS locked), never in the public `data` branch. Run `supabase/schema.sql` (safe to re-run; it now also creates `published_posts`, the durable guard against re-posting) and add `SUPABASE_URL` + `SUPABASE_SECRET_KEY`.
- YouTube replies need the `youtube.force-ssl` scope: re-run `python tools/youtube_auth.py` once.
- Posts published before Zernio ids were stored (`manual-*`) cannot be answered on Facebook/Instagram.

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
