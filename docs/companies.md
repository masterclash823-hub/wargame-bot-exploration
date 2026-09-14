# Companies

One company per nation, unlocked at **economy 4.0**. The Economy panel adds
the Company button only while the current nation meets this threshold.
Opening an old panel or calling /company cannot bypass the gate.
Formation and investment services also check technology and current ownership.

## Starting

Use Economy → Company or /company. Choose one to three production building
types, a name and a description. Formation is free, specialties are fixed,
and automation starts disabled. Set domestic province IDs, a gold limit per
investment and resource reserves in Budget and automation.

Companies use the nation's treasury and stocks, with no separate currency.
The investment budget limits construction and upgrades, not ordinary national
expenses. Company purchases and foreign operating prepayments must leave the
configured reserves intact. Explicitly confirmed improvements cost 100 gold.

Existing buildings can be assigned through Plants. Construction and upgrades
through the company receive a 10% discount. Managed ordinary production
receives +5%. Buildings still occupy the normal one-per-type provincial slot,
need workers and inputs, and have a maximum level of 3. Company investment
does not bypass either party's technology requirements or algae deposits.
Ordinary /build remains available at its ordinary price.

## Automation

Supply: build or upgrade a specialty that produces the selected resource.
Expansion: build missing specialty buildings.
Upgrades: upgrade existing specialty buildings.
Manual: no automatic investment.

At most one successful company construction or upgrade per game month,
including manual company investment. Automation searches approved provinces,
checks terrain, technology, reserves, budget and available workers, and rejects
forecasts with a worsened food shortage or a negative host monthly balance.
It does not buy inputs on the player's behalf from other players.
The monthly company report records the investment or the reason for waiting.

## Concessions

Use Concessions or /company_offers. A host can receive and accept offers
without having a company or economy 4. Incoming offers appear separately in
Diplomacy as Investment offers; they do not unlock the Company button.

An offer specifies province IDs, specialty building types, company production
share (default 70%), duration (default 12 game months), and an early termination
fee (default zero). The host explicitly accepts the complete terms. Permission
covers construction, upgrading and assignment of existing buildings in this
scope. A building cannot be assigned to two companies.

The company pays construction, production inputs and maintenance. Workers and
their food demand remain part of the host nation. Actual positive production
is split, with the host retaining the remaining share. The host's other
buildings and base resource yields are not shared.

Before settlement, foreign plants reserve their maximum monthly inputs and
maintenance from the company nation's stocks. If reserves would be breached,
that plant is unstaffed and produces nothing for that month. Unused inputs
and unused maintenance prepayment are returned. Shipments and refunds enter
the company's closing stocks **after all nations settle**, ready for the next
month; the host can use its share during the current month. This avoids
nation-ID-dependent results or spending the same inputs twice.
The report distinguishes gross prepayments from deliveries/refunds.

War suspends company management and transfers. The host may use its own
buildings normally without company bonuses while cooperation is suspended.
Early termination fees remain payable during war. Expiry releases managed
buildings without a fee; buildings remain with the host. A province transfer
ends management of that province. Changing either nation's player withdraws
its outstanding concessions; a new agreement is needed. The company itself
remains attached to its nation. Pausing a company also returns its buildings
to ordinary host operation until resumed.

## Described improvements

Choose the building type and intended effect, then describe the concrete
change in 20–3000 characters. A proposal quotes 100 gold, 2 game months and
a 5-percentage-point improvement. The GM reviews it using /company_review,
including previous proposal descriptions, and approves or rejects it.
The player must then explicitly pay and start the approved project.

Effects can improve production, input savings, worker savings, construction
discount or upkeep discount for the selected building type. One proposal or
project at a time; exact duplicate descriptions are rejected. The GM checks
whether reworded proposals repeat an earlier idea. Merely writing more text
does not improve the numerical reward. Completed improvements are permanent,
subject to the company's current technology cap.

| Economy | Production bonus | Construction/upkeep discount | Input/worker savings |
|---|---:|---:|---:|
| 4–5 | 15% | 15% | 10% |
| 6–7 | 25% | 20% | 15% |
| 8–10 | 35% | 25% | 20% |

Bonuses within the company add together and are capped, then combine with
existing game modifiers. Algae permits construction/upkeep savings only;
company production, input and worker bonuses do not increase algae extraction.

## Deployment and validation

Additive tables: companies, company_concessions, company_plants.
No existing nation or building is enrolled automatically.
No new environment variables are needed. Slash commands synchronize on boot.
The short player tutorial remains unchanged; this feature is optional.

The PR adds regression cases for technology gating, stale actions, ownership,
discounts, reserves, monthly investment limits, forecast rollback, foreign
payments and production shares, unused escrow refunds, war suspension,
termination, narrated improvement approval and technology caps.
GitHub Actions runs the Python suite with dummy credentials and temporary
SQLite databases. A live Discord/Render/PostgreSQL rollout is not performed
by these tests.
