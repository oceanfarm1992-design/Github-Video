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
   - `BUFFER_API_KEY` - TikTok and Pinterest through Buffer's API (https://developers.buffer.com). Connect both accounts in Buffer first. Pinterest pins go to the board named in the `BUFFER_PINTEREST_BOARD` variable (else the first board) and link to the post's resource; TikTok posts carry the AI-generated label.
   - `META_PAGE_ID`, `META_PAGE_ACCESS_TOKEN`, `META_IG_USER_ID` – Facebook Page Reels + Instagram Reels (Meta Graph API; the IG account must be a Business/Creator account linked to the Page)
   - `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN` â€“ YouTube Data API (Shorts upload)
4. Daily mix (5 videos, one per produce slot at 05:05, 08:05, 11:05, 14:05 and 17:05 UTC = 9 AM, 12 PM, 3 PM, 6 PM, 9 PM Dubai; `MAX_VIDEOS_PER_RUN` 1; series alternate GitHub, AI tools, GitHub, websites, PDF promo): `DAILY_GITHUB_VIDEOS` (2), `DAILY_TOOLS_VIDEOS` (1), `DAILY_SITES_VIDEOS` (1), `DAILY_NEWS_VIDEOS` (0, AI news is off); score bars `GENERATE_SCORE` (75, GitHub) and `GENERATE_SCORE_NEWS` (65, news).
   AI-tools videos ("10 AI tools for video generation"): one catalog category per video (`pipeline/tools.py`), each tool's homepage opened in Chromium and described only with its own site description; dead or empty sites are skipped. `TOOLS_PER_VIDEO` (10). Facebook is skipped for videos over 60 s (Reels limit); YouTube and Instagram get the full video.
   Websites series ("Websites that feel illegal to know - Part N", all legal): one site per video by default (`SITES_PER_VIDEO`=1) from the curated catalog in `pipeline/sites.py`, never repeated. The camera glides through the site's own sections (heading + text, captured in Chromium) with a gentle zoom and spotlight while the voice reads them; a small "For educational purposes only" note shows for the first 2 s (`DISCLAIMER_TEXT`). No piracy, paywall-bypass, people-search or open-camera sites. Comment keyword `LINK`.
   Promo series (the owner's site, Privacy PDF Tools): `DAILY_PROMO_VIDEOS` (1) video a day, one tool each, discovered from https://privacypdftools.com (new tools join automatically; Unlock PDF and Remove Password are kept out). Guided tour of the tool's own page (tagline, About, How to use, privacy line). Comment keyword `TOOL` -> DM with the tool link.
   Voice check: stage `voicetest` renders the same sentences with 4 cloned-voice settings (artifact MP4s) to pick from.
   Preview without publishing: Actions -> pipeline -> Run workflow, stage `preview`, sources `tools` or `sites`.
5. Optional variables: `DAILY_AI_BUDGET_USD`, `MAX_LLM_CALLS_PER_DAY`, `MAX_VIDEOS_PER_DAY`, `MAX_RENDERS_PER_TOPIC`, `MIN_TOPIC_SCORE`, `GENERATE_SCORE`.
6. Scheduling: runs are triggered by cron-job.org through `workflow_dispatch` (GitHub's own cron fired late and rarely, and was removed). Jobs (UTC): engage every 30 min, collect hourly at :17, research every 4 h, produce 05:05, 08:05, 11:05, 14:05 and 17:05, analyze 06:30. Each job POSTs `{"ref":"main","inputs":{"stage":"<stage>"}}` to `https://api.github.com/repos/<owner>/<repo>/actions/workflows/pipeline.yml/dispatches` with a fine-grained token (Actions: read & write on this repo only). Turn on cron-job.org failure e-mails.
   Automatic comment replies run on Facebook and Instagram only (`ENGAGE_PLATFORMS`, default `facebook,instagram`; YouTube is off). If YouTube is turned back on: quota is 10,000 units/day, uploads cost 1,600 each, checks are throttled (every 2 h, for 3 days) and replies capped at `YT_REPLIES_PER_DAY` (20). SEO (`pipeline/seo.py`): keyword-first titles, full descriptions, tags, English language metadata and one public playlist per series (created once, 50 units each add).

Notes for public repos:
- No secrets are in the code; `.env` is git-ignored. Never print or commit tokens.
- State (`pipeline.db`) is force-pushed as a single commit to the `data` branch each run, so history stays small. It contains only public data.
- Rendered videos are uploaded as workflow artifacts (7-day retention), not committed.
- Scheduled workflows are auto-disabled after 60 days without repo activity; re-enable them or push a commit.
- Public-repo Actions minutes are free; private repos have a monthly quota.

## Comment replies (Supabase)

The `engage` stage (every 30 min) answers follower comments automatically on Facebook/Instagram through Zernio (YouTube optional, off by default):
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
| Instagram / Facebook Stories | Every video also goes to Stories through Zernio (`STORIES`=1; Instagram Stories skip videos over 60 s, Facebook over 120 s) |
| CTA comment auto-replies | `cta_map` table stores keyword â†’ resource â†’ approved response; no reply bot yet |

## Known limits

- The default TTS is `edge-tts` (free, unofficial Microsoft endpoint); if it fails, videos get silent audio plus burned-in captions and an `.srt`.
- Videos are plain text slides; use Remotion only if you need richer motion.
- Analytics currently collect YouTube view/like/comment counts only; learning adjusts per-source score weights within 0.5â€“1.5x.
