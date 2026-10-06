# Autonomous AI News & Open-Source Video Pipeline

## Goal

Build and maintain a fully automated, cloud-based pipeline that discovers AI tools, open-source GitHub projects, models, research and important AI news; verifies and ranks them; generates short-form videos; renders them; publishes through official APIs; and records analytics.

**Primary objective:** maximum useful content with minimum operating cost.

**Human intervention:** none during normal operation.

---

## Core Rules

1. **Cost first.**

   * Prefer free/public APIs, RSS, GitHub API and cached data.
   * Never call an LLM when deterministic code can solve the task.
   * Never generate a video unless the topic passes the content score threshold.
   * Cache every API/LLM result that can be reused.
   * Batch LLM requests whenever possible.
   * Use small/cheap models for classification, extraction and rewriting.
   * Use stronger models only for high-value final content.
   * Never repeatedly regenerate the same asset.

2. **Official APIs only for publishing and messaging.**

   * Do not use browser automation to bypass platform restrictions.
   * Respect API limits, rate limits and platform policies.
   * If an API is unavailable, mark the job `WAITING_FOR_API` rather than creating an unreliable workaround.

3. **Automation must be fault tolerant.**

   * Every job must be resumable.
   * Failed jobs must retry with exponential backoff.
   * One failed source must not stop the pipeline.
   * Store state in the database.

4. **Never invent facts, URLs, GitHub repositories, statistics or product capabilities.**

   * Every important claim must have a source.
   * Validate URLs before publication.

5. **Do not repeatedly publish the same topic.**

   * Deduplicate using URL, repository ID, title similarity and content hash.

---

# Pipeline

```text
DISCOVER
   ↓
FILTER
   ↓
RESEARCH
   ↓
VERIFY
   ↓
SCORE
   ↓
GENERATE
   ↓
RENDER
   ↓
QUALITY CHECK
   ↓
PUBLISH
   ↓
ANALYZE
   ↓
LEARN
   ↓
REPEAT
```

---

# Preferred Low-Cost Stack

Use the simplest tool that solves the problem.

* Python
* SQLite initially; PostgreSQL when required
* FastAPI only when an API service is needed
* GitHub API
* RSS/Atom feeds
* arXiv API
* Hugging Face public endpoints where appropriate
* Redis only when queue concurrency requires it
* FFmpeg
* Remotion only when advanced motion graphics are required
* Pillow for simple graphics
* Playwright only when screenshots are genuinely required
* Object storage for generated media
* Docker
* GitHub Actions for lightweight scheduled jobs
* One inexpensive cloud VM for persistent workers if required

Do not add Kubernetes, Kafka, Elasticsearch, Airflow or other infrastructure unless there is a demonstrated need.

---

# Discovery Sources

Prioritize:

1. GitHub trending/new repositories
2. GitHub releases
3. GitHub API
4. RSS/Atom feeds
5. Official AI company blogs
6. Hugging Face
7. arXiv
8. Hacker News
9. Other reputable technical sources

Prefer first-party sources.

---

# Discovery Strategy

Run discovery on a schedule rather than continuously.

Example:

```text
Every 30–60 minutes:
    collect cheap metadata

Every 3–6 hours:
    research only high-scoring candidates

Daily:
    generate selected videos

Daily/weekly:
    analyze performance
```

Do not repeatedly fetch unchanged pages.

Use:

```text
ETag
Last-Modified
content hash
repository updated_at
release ID
URL cache
```

whenever available.

---

# Topic Scoring

Before expensive processing, calculate:

```text
score =
    novelty
  + usefulness
  + trend
  + source_quality
  + video_potential
  + audience_relevance
  - duplication
```

Normalize to 0–100.

Default:

```text
< 65   → ignore
65–79  → queue
80–89  → generate
90+    → priority
```

Make thresholds configurable through environment variables.

---

# LLM Usage Policy

LLM calls are expensive.

Use this order:

```text
Rules/code
   ↓
Cached result
   ↓
Small/cheap model
   ↓
Larger model only if necessary
```

Use LLMs for:

* summarization
* classification
* script generation
* headline generation
* content transformation
* strategic analysis

Do NOT use LLMs for:

* URL validation
* duplicate detection
* date comparison
* file conversion
* JSON validation
* video encoding
* image resizing
* hashing
* simple scoring
* API polling
* database operations

---

# Research Object

Every candidate should become a structured object:

```json
{
  "id": "",
  "title": "",
  "source_urls": [],
  "github_url": null,
  "published_at": null,
  "discovered_at": "",
  "claims": [],
  "summary": "",
  "score": 0,
  "confidence": 0,
  "status": "discovered"
}
```

Do not generate content from unverified raw discovery data.

---

# Content Object

```json
{
  "topic_id": "",
  "hook": "",
  "script": "",
  "title": "",
  "caption": "",
  "hashtags": [],
  "cta_keyword": "",
  "sources": [],
  "duration_target": 45,
  "status": "ready"
}
```

---

# CTA / Comment Automation

Use a small controlled keyword set:

```text
GITHUB
TOOL
CODE
DOCS
DEMO
SOURCE
```

Each published video must have exactly one primary CTA keyword.

Store the mapping:

```text
video_id
    ↓
cta_keyword
    ↓
resource_id
    ↓
approved response
```

Never guess which link should be sent.

Use official platform messaging/comment APIs where available.

---

# Video Generation

Default target:

```text
9:16
1080x1920
30–60 seconds
H.264
AAC
```

Use reusable templates.

Do not regenerate unchanged components.

Cache:

```text
TTS audio
images
screenshots
logos
backgrounds
music
subtitles
```

Use deterministic filenames/hashes.

Example:

```text
assets/{sha256}.png
audio/{sha256}.mp3
video/{content_hash}.mp4
```

---

# Video Rendering

Use FFmpeg for simple videos.

Use Remotion only when the template requires:

* animated UI
* advanced typography
* charts
* transitions
* code animation
* reusable React components

Rendering should happen only after content passes quality control.

---

# Quality Control

Automatically check:

```text
✓ video exists
✓ correct dimensions
✓ valid codec
✓ audio exists
✓ duration valid
✓ subtitles exist
✓ no missing assets
✓ source URLs valid
✓ GitHub URL valid
✓ claims have sources
✓ no duplicate topic
✓ CTA exists
✓ caption exists
```

If QC fails:

```text
retry → regenerate only failed component
```

Do not regenerate the entire pipeline unnecessarily.

---

# Publishing

Publish only through official APIs.

Track:

```text
platform
post_id
content_id
published_at
status
url
```

Possible status values:

```text
DISCOVERED
RESEARCHING
VERIFIED
QUEUED
GENERATING
RENDERING
QC
READY
PUBLISHED
FAILED
RETRY
SKIPPED
```

---

# Analytics

Collect where APIs permit:

```text
views
likes
comments
shares
saves
followers
profile visits
link clicks
CTA comments
DM requests
```

Do not use an LLM to calculate basic metrics.

Use Python/SQL.

Use AI only for higher-level strategy analysis.

---

# Autonomous Learning

Periodically calculate:

```text
topic performance
hook performance
duration performance
CTA performance
source performance
template performance
posting-time performance
```

Then update configurable strategy weights.

Never allow the AI to modify production code automatically.

AI may modify:

```text
content mix
topic weights
score thresholds
template selection
posting schedule
```

within predefined safe limits.

---

# Cost Controls

The system must maintain a daily cost budget.

Example environment variables:

```env
DAILY_AI_BUDGET_USD=1.00
MAX_LLM_CALLS_PER_DAY=100
MAX_VIDEOS_PER_DAY=10
MAX_RENDERS_PER_TOPIC=2
MIN_TOPIC_SCORE=65
```

Before an expensive operation:

```python
if estimated_cost > remaining_budget:
    queue_for_later()
```

Never exceed the configured budget automatically.

---

# Failure Handling

Every external operation must have:

```text
timeout
retry
exponential backoff
rate-limit handling
structured logging
```

Example:

```text
Attempt 1
   ↓
wait
   ↓
Attempt 2
   ↓
wait
   ↓
Attempt 3
   ↓
FAILED
```

Do not endlessly retry.

---

# Database

Start with SQLite if the deployment is single-worker.

Move to PostgreSQL when:

* multiple workers are required
* concurrent jobs increase
* analytics become large
* multiple services need shared state

Do not introduce PostgreSQL merely for architecture aesthetics.

---

# Security

Never commit:

```text
API keys
tokens
passwords
cookies
session data
private credentials
```

Use environment variables or a cloud secret manager.

Validate all external URLs and downloaded files.

---

# Development Rules

Before adding a dependency ask:

```text
Can Python standard library solve this?
Can an existing dependency solve this?
Can an API solve this more cheaply?
Does this dependency materially improve the system?
```

Keep the deployment small.

Prefer one service/container initially.

Split services only when there is a measurable reason.

---

# Autonomous Maintenance

The system may automatically:

* discover new sources
* retry failures
* clean temporary files
* remove expired cache entries
* update statistics
* re-rank content
* schedule posts
* generate videos
* publish
* collect analytics

The system must NOT automatically:

* change credentials
* change security settings
* bypass platform restrictions
* deploy untested production code
* spend beyond configured limits
* create uncontrolled infrastructure

---

# Codex Operating Principle

When modifying this project:

1. Inspect the existing implementation first.
2. Reuse existing components.
3. Make the smallest change that solves the problem.
4. Avoid unnecessary dependencies.
5. Prefer deterministic code over AI calls.
6. Add caching before adding compute.
7. Test locally.
8. Run relevant tests.
9. Check estimated operating cost.
10. Document only meaningful changes.

**Optimize for:**

```text
Reliability
   >
Cost
   >
Quality
   >
Scale
   >
Complexity
```

The target is a **small, autonomous, inexpensive AI content factory**, not an unnecessarily complex enterprise platform.
