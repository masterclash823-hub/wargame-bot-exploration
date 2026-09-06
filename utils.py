"""
Shared helpers used across multiple cogs.
Import from here, not from each other, to avoid circular imports.
"""
import discord
import db
import i18n
import config


def gm_only(interaction: discord.Interaction) -> bool:
    """Recognize only the configured GM role; an explicit ID takes precedence."""
    if not interaction.guild:
        return False
    roles = getattr(interaction.user, "roles", ())
    if config.GM_ROLE_ID:
        return any(str(r.id) == config.GM_ROLE_ID for r in roles)
    name = config.GM_ROLE_NAME.strip().casefold()
    return bool(name) and any(r.name.strip().casefold() == name for r in roles)


def t_interaction(interaction: discord.Interaction) -> str:
    """Resolve the language string for a given interaction's user."""
    locale = interaction.locale.value if interaction.locale else None
    return i18n.get_user_language(interaction.user.id, locale)


def get_nation_by_name(name: str):
    """Return a nation row by name (case-insensitive), or None."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM nations WHERE LOWER(name) = LOWER(?)",
            (name,),
        )
        return cur.fetchone()


def get_nation_by_owner(owner_id: str):
    """Return a nation row by Discord owner user ID, or None."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM nations WHERE owner_id = ?",
            (owner_id,),
        )
        return cur.fetchone()


def log_history(nation_id: int, source: str, text: str) -> None:
    """Append an entry to a nation's history log."""
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO nation_history (nation_id, source, entry_text) VALUES (?, ?, ?)",
            (nation_id, source, text),
        )
