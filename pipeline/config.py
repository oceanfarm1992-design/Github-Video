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
LLM_MODEL = os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001")
DAILY_AI_BUDGET_USD = _f("DAILY_AI_BUDGET_USD", 1.0)
MAX_LLM_CALLS_PER_DAY = _i("MAX_LLM_CALLS_PER_DAY", 100)
MAX_VIDEOS_PER_DAY = _i("MAX_VIDEOS_PER_DAY", 10)
MAX_RENDERS_PER_TOPIC = _i("MAX_RENDERS_PER_TOPIC", 2)
MIN_TOPIC_SCORE = _i("MIN_TOPIC_SCORE", 65)
GENERATE_SCORE = _i("GENERATE_SCORE", 80)
PRIORITY_SCORE = _i("PRIORITY_SCORE", 90)
HTTP_TIMEOUT = _i("HTTP_TIMEOUT", 20)
MAX_ATTEMPTS = _i("MAX_ATTEMPTS", 3)

CTA_KEYWORDS = ("GITHUB", "TOOL", "CODE", "DOCS", "DEMO", "SOURCE")

# Strategy weights the learning stage may adjust, always clamped to these safe limits.
WEIGHT_LIMITS = (0.5, 1.5)

AI_KEYWORDS = (
    "llm", "gpt", "agent", "ai ", " ai", "machine learning", "diffusion", "transformer",
    "neural", "rag", "inference", "mcp", "openai", "anthropic", "claude", "gemini",
    "llama", "mistral", "hugging face", "embedding", "fine-tun", "copilot", "model",
)
