"""
Localization module. Loads string tables from i18n/strings_<lang>.json and resolves
which language to use for a given user (their saved preference, falling back to
Discord's reported locale, falling back to the configured default).

Adding a new language later = drop in i18n/strings_xx.json + add "xx" to
config.SUPPORTED_LANGUAGES. No command code needs to change.
"""
import json
import pathlib
import inspect
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

import discord

import config
import db

_STRINGS_DIR = pathlib.Path(__file__).parent / "i18n"
_cache: dict[str, dict[str, str]] = {}
_language = ContextVar("interaction_language", default=None)
_ui = json.loads((_STRINGS_DIR / "ui_pl.json").read_text(encoding="utf-8"))
_terms = json.loads((_STRINGS_DIR / "terms_pl.json").read_text(encoding="utf-8"))


def current_language():
    return _language.get() or config.DEFAULT_LANGUAGE


@contextmanager
def using_language(lang):
    token = _language.set(lang if lang in config.SUPPORTED_LANGUAGES else config.DEFAULT_LANGUAGE)
    try:
        yield
    finally:
        _language.reset(token)


def text(source, *, lang=None, **values):
    """Translate a source template BEFORE inserting opaque player-provided values."""
    template = _ui.get(source, source) if (lang or current_language()) == "pl" else source
    return template.format(**values) if values else template


def term(key, lang=None):
    """Display a game identifier. Never use this for arbitrary names or player prose."""
    key = str(key)
    if (lang or current_language()) == "pl":
        return _terms.get(key.lower(), _ui.get(key, key))
    return key.replace("_", " ")


def resource_list(resources, lang=None):
    return ", ".join(f"{amount:g} {term(key, lang)}" for key, amount in resources.items()) or "—"


def normalize_key(key):
    """Accept Polish display names without changing the stored schema."""
    import unicodedata
    def fold(value):
        return ''.join(c for c in unicodedata.normalize('NFKD', value.lower().replace('ł', 'l'))
                       if not unicodedata.combining(c)).strip()
    aliases = {fold(value): name for name, value in _terms.items()}
    return aliases.get(fold(key), key.strip().lower())


def game_json(raw):
    """Normalize resource/effect keys, preserving scalar values and custom prose."""
    data = json.loads(raw)
    def normalize(value):
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            canonical = normalize_key(key)
            if canonical in result:
                raise ValueError(text("Duplicate resource aliases: {p0}", p0=key))
            result[canonical] = normalize(item)
        return result
    return normalize(data)


def localized(callback):
    """Bind each interaction independently, including component callbacks after restart."""
    names = list(inspect.signature(callback).parameters)
    name = next(n for n in names if n in ("interaction", "inter", "btn_interaction", "i"))
    index = names.index(name)
    @wraps(callback)
    async def wrapper(*args, **kwargs):
        interaction = kwargs.get(name) if name in kwargs else args[index]
        locale = getattr(interaction, "locale", None)
        locale = getattr(locale, "value", locale)
        lang = get_user_language(interaction.user.id, locale)
        with using_language(lang):
            return await callback(*args, **kwargs)
    return wrapper


class LocalizedView(discord.ui.View):
    """Localize declarative buttons when the view is created, not at module import."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.label:
                child.label = text(child.label)


def _load(lang: str) -> dict[str, str]:
    if lang not in config.SUPPORTED_LANGUAGES:
        lang = config.DEFAULT_LANGUAGE
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
        template = _load("en").get(key, key)
    return template.format(**kwargs) if kwargs else template


def get_user_language(user_id: int, discord_locale: str | None = None) -> str:
    """Saved preference > Discord client locale (if supported) > default."""
    with db.cursor() as cur:
        cur.execute("SELECT language FROM user_prefs WHERE user_id = ?", (str(user_id),))
        row = cur.fetchone()
    if row and row["language"] in config.SUPPORTED_LANGUAGES:
        return row["language"]

    if discord_locale:
        short = discord_locale.split("-")[0].lower()
        if short in config.SUPPORTED_LANGUAGES:
            return short

    return config.DEFAULT_LANGUAGE


def set_user_language(user_id: int, lang: str) -> None:
    if lang not in config.SUPPORTED_LANGUAGES:
        raise ValueError("Unsupported language")
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO user_prefs (user_id, language) VALUES (?, ?)
               ON CONFLICT(user_id) DO UPDATE SET language = excluded.language""",
            (str(user_id), lang),
        )
