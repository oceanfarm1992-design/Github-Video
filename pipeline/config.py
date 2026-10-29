import os


def _f(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def _i(name, default):
    return int(_f(name, default))


DB_PATH = os.environ.get("DB_PATH", "data/pipeline.db")
OUT_DIR = os.environ.get("OUT_DIR", "out")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001")  # comment replies etc.
SCRIPT_MODEL = os.environ.get("SCRIPT_MODEL", "claude-sonnet-5-5")  # Short scripts: one a day, so the better model fits the budget
DAILY_AI_BUDGET_USD = _f("DAILY_AI_BUDGET_USD", 1.0)
MAX_LLM_CALLS_PER_DAY = _i("MAX_LLM_CALLS_PER_DAY", 100)
MAX_VIDEOS_PER_DAY = _i("MAX_VIDEOS_PER_DAY", 1)
# one Short a day, the series taking turns day by day (empty = every series with a quota runs each day)
DAILY_ROTATION = os.environ.get("DAILY_ROTATION", "github,tools,github,sites,promo")
MAX_RENDERS_PER_TOPIC = _i("MAX_RENDERS_PER_TOPIC", 2)
MIN_TOPIC_SCORE = _i("MIN_TOPIC_SCORE", 65)
GENERATE_SCORE = _i("GENERATE_SCORE", 75)  # GitHub bar; real scores top out near 80
PRIORITY_SCORE = _i("PRIORITY_SCORE", 90)
# Daily mix: N GitHub-project videos + N AI-news videos (news items score lower, so a lower bar).
DAILY_GITHUB_VIDEOS = _i("DAILY_GITHUB_VIDEOS", 1)
DAILY_NEWS_VIDEOS = _i("DAILY_NEWS_VIDEOS", 0)   # AI news replaced by the websites series (set 1 to bring it back)
DAILY_SITES_VIDEOS = _i("DAILY_SITES_VIDEOS", 1)   # "Websites that feel illegal to know - Part N"
SITES_PER_VIDEO = _i("SITES_PER_VIDEO", 1)  # 1 = one site per video, explored section by section
DAILY_TOOLS_VIDEOS = _i("DAILY_TOOLS_VIDEOS", 1)   # "N AI tools for <category>" videos
DAILY_PROMO_VIDEOS = _i("DAILY_PROMO_VIDEOS", 1)   # one Privacy PDF Tools tool per day
TOOLS_PER_VIDEO = _i("TOOLS_PER_VIDEO", 10)
# weekly formats (0 = Monday ... 6 = Sunday; -1 turns the format off)
RANKING_WEEKDAY = _i("RANKING_WEEKDAY", 6)  # "Top 5 new GitHub repos this week", ranked by stars
RANKING_SIZE = _i("RANKING_SIZE", 5)
RECAP_WEEKDAY = _i("RECAP_WEEKDAY", 5)      # 30-second recap of the week's videos
YT_FIRST_COMMENT = _i("YT_FIRST_COMMENT", 1)  # post the link as the first YouTube comment
# long videos (YouTube + Facebook): one every LONG_EVERY_DAYS days, counted from LONG_START, rotating through
# LONG_FORMATS. LONG_EVERY_DAYS=0 falls back to LONG_SCHEDULE (weekday:format pairs; empty turns them off).
LONG_EVERY_DAYS = _i("LONG_EVERY_DAYS", 5)
LONG_START = os.environ.get("LONG_START", "2026-11-01")
LONG_FORMATS = os.environ.get("LONG_FORMATS", "github,tools,sites")
LONG_SCHEDULE = os.environ.get("LONG_SCHEDULE", "0:github,2:tools,4:sites")
# at most this many videos per produce run; two daily produce slots catch up if GitHub drops one schedule
MAX_VIDEOS_PER_RUN = _i("MAX_VIDEOS_PER_RUN", 1)
GENERATE_SCORE_NEWS = _i("GENERATE_SCORE_NEWS", 65)
HTTP_TIMEOUT = _i("HTTP_TIMEOUT", 20)
MAX_ATTEMPTS = _i("MAX_ATTEMPTS", 3)

CTA_KEYWORDS = ("GITHUB", "TOOL", "CODE", "DOCS", "DEMO", "SOURCE", "LINK")

# Strategy weights the learning stage may adjust, always clamped to these safe limits.
WEIGHT_LIMITS = (0.5, 1.5)

AI_KEYWORDS = (
    "llm", "gpt", "agent", "ai ", " ai", "machine learning", "diffusion", "transformer",
    "neural", "rag", "inference", "mcp", "openai", "anthropic", "claude", "gemini",
    "llama", "mistral", "hugging face", "embedding", "fine-tun", "copilot", "model",
)
