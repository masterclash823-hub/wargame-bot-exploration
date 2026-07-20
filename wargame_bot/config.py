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

# --- Google Gemini ---
GEMINI_API_KEY = _require("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# --- Database ---
DB_PATH = os.getenv("DB_PATH", "wargame.db")

# --- Localization ---
DEFAULT_LANGUAGE = os.getenv("DEFAULT_LANGUAGE", "en")
SUPPORTED_LANGUAGES = ("en", "pl")
