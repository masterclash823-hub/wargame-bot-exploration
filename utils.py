"""
Shared helpers used across multiple cogs.
Import from here, not from each other, to avoid circular imports.
"""
import discord
import db
import i18n
import config
from datetime import date, datetime


def short_date(value):
    """Format both PostgreSQL datetime objects and SQLite timestamp strings."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    return str(value)[:10] if value is not None else "—"


class EmbedPager(i18n.LocalizedView):
    """Private, owner-bound navigation for already size-limited embeds."""
    def __init__(self, pages, owner_id):
        super().__init__(timeout=180)
        self.pages, self.owner_id, self.index = pages, owner_id, 0
        for number, page in enumerate(pages, 1):
            page.set_footer(text=f"{number} / {len(pages)}")
        self._refresh()

    def _refresh(self):
        self.previous.disabled = self.index == 0
        self.next_page.disabled = self.index == len(self.pages) - 1

    @i18n.localized
    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(i18n.text('This is not your menu.'), ephemeral=True)
        return False

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    @i18n.localized
    async def previous(self, interaction, button):
        self.index = max(0, self.index - 1)
        self._refresh()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    @i18n.localized
    async def next_page(self, interaction, button):
        self.index = min(len(self.pages) - 1, self.index + 1)
        self._refresh()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)


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
