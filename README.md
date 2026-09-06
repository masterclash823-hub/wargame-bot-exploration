# Wargame Bot - Step 1: Skeleton

## Battle resolution

`/battle match` now returns the real PostgreSQL battle ID. `/battle resolve`
without an ID lists pending battles, including records created by older versions
that displayed `Battle #None`. Resolve one with `/battle resolve <id>`.

Resolution uses `GEMINI_MODEL` and falls back to neutral modifiers when AI is
unavailable. AI modifiers are constrained to 0.7–1.4; optional GM overrides must
be 0.1–3.0 (use 0/blank for AI). The battle row, both plan statuses, logs and
optional casualties commit atomically. Repeated or concurrent resolution cannot
apply losses twice. Casualties affect only quantities committed in the two plans,
not every military unit owned by the nations. The result is saved before Discord
announcement; a channel delivery failure does not undo the completed battle.

After merging, deploy/restart Render so Discord command parameters synchronize.
Test first with `apply_casualties: false`, then inspect `/battle view <id>`.
Offline tests do not call production PostgreSQL, Discord or Gemini.

## GM access and regression checks

`/battle plans_pending` accepts PostgreSQL timestamps and paginates large queues.
New AI events use the nation owner's saved `/language` preference (`pl` or `en`),
falling back to `DEFAULT_LANGUAGE` when no preference is saved. The invoking GM's
language does not control the narrative. Public/DM labels follow the owner;
`/event list` labels follow its viewer. Existing event text and GM edits are
preserved, not automatically translated. AI output language is requested in the
prompt; the GM should still review the draft before posting.

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
