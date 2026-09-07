# Wargame Bot

## Panel gracza

Po wdrożeniu GM uruchamia raz `/panel_publish` na wybranym kanale. Bot publikuje
stały przycisk, z którego każdy gracz otwiera własny prywatny panel. Panel dzieli
działania na gospodarkę, wojsko i technologię, terytorium, dyplomację i bitwy,
wydarzenia oraz ustawienia. Państwa, prowincje, jednostki, oferty i eventy wybiera
się z list; formularze służą jedynie do nazw, liczb i własnych rozkazów.

Alternatywnie gracz może użyć `/panel`. Publiczny przycisk działa także po
restarcie bota. Szczegółowy projekt znajduje się w
[docs/player-panel-design.md](docs/player-panel-design.md).

## Polska wersja interfejsu

Użyj `/translate` bez parametrów, aby zapisać polski język odpowiedzi.
Komendy, formularze, przyciski, zasoby i nowe komunikaty mają polską wersję.
Instrukcje dla Rendera i ustawień języka Discorda:
[Obsługa po polsku](docs/polish-interface.md).

## Interactive events and GM help

GM help now has pages: the old GM tab exceeded Discord's 25-field limit
(26 English / 27 Polish fields). `GM_ROLE_NAME=Game Master` is sufficient for
that role; `GM_ROLE_ID` is optional and does not fix embed-size errors.

New event workflow:

1. GM uses `/event generate`, optionally `/event edit` and `/event effects`.
2. `/event post` starts an interactive event without applying any effects yet.
3. The nation owner uses buttons 1/2/3 or **Custom response**. Each custom answer
   counts as one decision, just like a button. `/event play <id>` restores the
   latest saved state after a timeout, missed DM or a Render restart.
4. After exactly three accepted decisions, the event ends and applies effects
   once. Old buttons/repeated submissions cannot pay out again. GM can inspect
   with `/event play`, but only the nation owner can make choices.

The three mechanical strategies scale the GM-approved base gains **and losses**
by 0.5, 1, or 1.5. The final multiplier is their average, not three full payouts.
Choices display accumulated pending effects. Custom text shapes the story and
AI classifies it into the same bounded strategies; it cannot introduce arbitrary
rewards. AI failure uses a visible balanced fallback and three default choices.
The stored event language follows the owner's `/language` setting when posted.
Existing posted events are left unchanged and are not paid out again.

Deployment: merge and deploy, then startup `db.init_db()` adds `event_runs`
without deleting existing tables. Run the test suite and try a disposable event
first; verify no balances change on post or decisions 1/2, and exactly one change
after decision 3. The GM/owner can inspect the final result through `/event play`.
Decision views expire after 10 minutes, but saved progress does not expire.
Resuming after a restart requires retaining the same database (use the configured
PostgreSQL database on the hosted bot, not a disposable test SQLite file).

See [the economy review](docs/economy-review.md) for confirmed economic issues
and [the offline diagnostic](scripts/audit_economy.py) to reproduce them.
**Important:** the old non-atomic monthly tick can overwrite concurrent balance
updates. This PR protects event settlement itself but does not repair that
separate economy issue. No production PostgreSQL or Discord tests were run.
## Battle resolution

`/battle match` now returns the real PostgreSQL battle ID. `/battle resolve`
without an ID lists pending battles, including records created by older versions
that displayed `Battle #None`. Resolve one with `/battle resolve <id>`.

When resolving with an ID, the GM also supplies the final battlefield. If it is\nomitted, Discord opens a location form. A province name or Azgaar cell ID loads\nits terrain, biome and fortification; descriptive locations remain available for\nsea and off-map battles. Gemini receives the exact committed units, both plans\nand battlefield data. The saved/public report explains the opening engagement,\nturning point and outcome, and `/battle view` shows the same narrative later.\n\nResolution uses `GEMINI_MODEL` and falls back to neutral modifiers when AI is
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
