"""
Localization module. Loads string tables from i18n/strings_<lang>.json and resolves
which language to use for a given user (their saved preference, falling back to
Discord's reported locale, falling back to the configured default).

Adding a new language later = drop in i18n/strings_xx.json + add "xx" to
config.SUPPORTED_LANGUAGES. No command code needs to change.
"""
import json
import pathlib

import config
import db

_STRINGS_DIR = pathlib.Path(__file__).parent / "i18n"
_cache: dict[str, dict[str, str]] = {}


def _load(lang: str) -> dict[str, str]:
    if lang not in _cache:
        path = _STRINGS_DIR / f"strings_{lang}.json"
        if not path.exists():
            lang = config.DEFAULT_LANGUAGE
            path = _STRINGS_DIR / f"strings_{lang}.json"
        with open(path, encoding="utf-8") as f:
            _cache[lang] = json.load(f)
    return _cache[lang]


def t(lang: str, key: str, **kwargs) -> str:
    """Translate `key` into `lang`, formatting with any kwargs given."""
    strings = _load(lang)
    template = strings.get(key)
    if template is None:
        # Fall back to English so a missing key never crashes a command.
        template = _load(config.DEFAULT_LANGUAGE).get(key, key)
    return template.format(**kwargs) if kwargs else template


def get_user_language(user_id: int, discord_locale: str | None = None) -> str:
    """Saved preference > Discord client locale (if supported) > default."""
    with db.cursor() as cur:
        cur.execute("SELECT language FROM user_prefs WHERE user_id = ?", (str(user_id),))
        row = cur.fetchone()
    if row:
        return row["language"]

    if discord_locale:
        short = discord_locale.split("-")[0].lower()
        if short in config.SUPPORTED_LANGUAGES:
            return short

    return config.DEFAULT_LANGUAGE


def set_user_language(user_id: int, lang: str) -> None:
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO user_prefs (user_id, language) VALUES (?, ?)
               ON CONFLICT(user_id) DO UPDATE SET language = excluded.language""",
            (str(user_id), lang),
        )
