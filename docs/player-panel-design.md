# Player panel design

## Goal

Daily play should start from one permanent Discord button. Slash commands remain
available as a fallback and for advanced GM administration, but a player should
not need to remember command names or database IDs.

## Entry points

- `/panel_publish` is run once by a Game Master in the chosen channel. It posts a
  persistent **Open player panel** button that continues working after a restart.
- `/panel` opens the same private panel directly and is useful before the launcher
  has been posted.
- Every panel is ephemeral and bound to its owner. Other users cannot operate it.

## Navigation

The home screen shows the player's nation, treasury, stability, resources and
small counters for provinces, forces, pending trades and available events. A
single category selector changes the action buttons below it.

| Category | Main actions |
| --- | --- |
| Overview | nation stats, resources, calendar, refresh |
| Economy | construct a building, building catalogue, province yield, trades, megaprojects |
| Military & technology | forces, blueprints, recruit/build, move, create blueprint, research |
| Territory | provinces, province details, colonies, trade routes |
| Diplomacy & battles | relations, war, peace, alliance, battle plan, battle reports |
| Events | available events and continue an event |
| Settings | Polish/English language, help and tutorial |

## Interaction rules

1. Existing game command callbacks remain the source of truth for costs,
   permissions and effects. The panel only gathers their arguments and invokes
   them, avoiding a second implementation of game rules.
2. Nations, provinces, units, blueprints, trades, projects and events are selected
   by name from Discord select menus. The database ID is kept inside the option.
3. Modals are used only for genuinely free text or numbers such as nation lore,
   battle orders, project descriptions and quantities.
4. Results appear as separate ephemeral messages. The original panel stays open,
   so the player can immediately choose another action.
5. Discord permits at most 25 options in one select. The first release shows the
   25 most relevant records and says when the list has been shortened.

## Safety and permissions

- The launcher verifies that the server is activated.
- The publish command uses the same `Game Master` role check as other GM tools.
- Component views validate the Discord user ID on every click.
- Existing callbacks re-check nation ownership, resources, diplomacy state and
  other game rules before changing data.

