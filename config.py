"""
Central config loader. All secrets come from environment variables (Replit "Secrets"
tab, or a local .env file when developing outside Replit) - never hardcode tokens here.
"""
import os

try:
    # Only needed for local dev; on Replit, Secrets are injected as env vars directly.
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Set it in Replit Secrets (or a local .env file)."
        )
    return value


# --- Discord ---
DISCORD_TOKEN = _require("DISCORD_TOKEN")

# GM role name that gates GM-only commands (e.g. /battle resolve, /admin ...).
# Kept as a name rather than a hardcoded ID so it works across servers without editing code.
GM_ROLE_NAME = os.getenv("GM_ROLE_NAME", "Game Master")
# Prefer an exact role ID when names differ between Discord and deployment config.
GM_ROLE_ID = os.getenv("GM_ROLE_ID", "").strip()
# Optional prefix replies; message context commands work without this intent.
MESSAGE_CONTENT_INTENT = os.getenv("MESSAGE_CONTENT_INTENT", "false").strip().lower() in ("1", "true", "yes")

# --- Google Gemini ---
GEMINI_API_KEY = _require("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
GEMINI_FALLBACK_MODELS = os.getenv("GEMINI_FALLBACK_MODELS", "gemini-2.5-flash-lite,gemini-2.5-flash")

# Optional independent event providers. Use free-tier accounts; never put keys in code.
EVENT_AI_PROVIDERS = os.getenv("EVENT_AI_PROVIDERS", "gemini,groq,mistral,openrouter")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_EVENT_MODEL = os.getenv("GROQ_EVENT_MODEL", "openai/gpt-oss-120b").strip()
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "").strip()
MISTRAL_EVENT_MODEL = os.getenv("MISTRAL_EVENT_MODEL", "mistral-small-latest").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_EVENT_MODEL = os.getenv("OPENROUTER_EVENT_MODEL", "openrouter/free").strip()

# --- Database ---
DB_PATH = os.getenv("DB_PATH", "/tmp/wargame.db")

# --- Localization ---
DEFAULT_LANGUAGE = os.getenv("DEFAULT_LANGUAGE", "pl").strip().lower()
SUPPORTED_LANGUAGES = ("en", "pl")
if DEFAULT_LANGUAGE not in SUPPORTED_LANGUAGES:
    DEFAULT_LANGUAGE = "pl"
