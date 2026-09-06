# Wargame Bot - Step 1: Skeleton

## GM access and regression checks

The `/help` GM tab and GM commands use the same role check. Set `GM_ROLE_ID`
to the Discord role ID (recommended; it survives role renaming). If unset,
`GM_ROLE_NAME` defaults to `Game Master` and ignores outer whitespace and case.
For a role named `GM`, set `GM_ROLE_NAME=GM`. An explicit ID overrides the name.
Restart the bot after changing environment variables. Administrator permission
alone does not grant GM access.

Run offline regression tests after installing `requirements.txt`:

```sh
python -m unittest discover -s tests -v
```

Tests use temporary SQLite databases and mocked Discord interactions, never a
live bot or production game database. PostgreSQL settlement uses row locks and
`CURRENT_TIMESTAMP`; live PostgreSQL verification remains a deployment check.

What's here so far: bot connects, syncs slash commands, and has `/help` + `/language`
working end-to-end with the English/Polish localization system. Everything later
(nations, provinces, economy, combat) builds on this same pattern - a slash command in
`bot.py` (or a new cog file once we have more), a table in `db.py`'s `SCHEMA`, strings in
`i18n/strings_en.json` / `strings_pl.json`.

## Setup

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
2. Create a Discord application + bot at https://discord.com/developers/applications,
   enable it, copy the bot token.
3. Get a free Gemini API key at https://aistudio.google.com/apikey (not used by any
   command yet, but config.py requires it now so later steps don't need new setup).
4. On Replit: put `DISCORD_TOKEN` and `GEMINI_API_KEY` (and optionally `GM_ROLE_NAME`,
   `DB_PATH`, `DEFAULT_LANGUAGE`) in the **Secrets** tab.
   Locally: copy `.env.example` to `.env` and fill it in.
5. Invite the bot to your server with the `applications.commands` and `bot` scopes.
6. Run it:
   ```
   python bot.py
   ```
7. In Discord, try `/help` and `/language`.

## What's next
Step 2 will add the nations/provinces/economy tables to `db.py` and the first real
gameplay commands (`/nation found`, `/nation stats`).
