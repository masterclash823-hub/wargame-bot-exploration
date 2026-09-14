# Companies

One company per nation, unlocked at **economy 4.0**. The Economy panel adds
the Company button only while the current nation meets this threshold.
Opening an old panel or calling /company cannot bypass the gate.
Formation and investment services also check technology and current ownership.

## Starting and budget

Use Economy → Company or /company. Choose one to three production building
types, a name and a description. Formation is free and specialties are fixed.
Then open Budget and automation, choose **Automatic** or **Manual**, and enter
one amount: **gold per game month**. New companies start in Manual with 0 gold.

The amount is a recurring construction and upgrade allowance drawn from the
nation's treasury when an investment actually succeeds. Materials come from
national stocks. Unspent gold stays in the treasury; unused allowances do not
accumulate. The allowance renews with the game calendar, not every real day.
The panel shows the amount spent and remaining this month.

Automatic and manual company construction share this allowance, including
manual foreign investments. Changing the amount, toggling the mode, reopening
the menu or pausing the company does not reset spending. Reducing the amount
below spending already incurred prevents further investment that month.
Ordinary national expenses, plant upkeep, foreign operating supplies and
explicitly accepted improvement projects are paid separately.

Existing buildings can be assigned through Plants. Company construction and
upgrades receive a 10% discount; managed ordinary production receives +5%.
Buildings still occupy the one-per-type provincial slot, need workers and
inputs, and have at most 3 levels. Technology, terrain, algae deposits and
available funds remain required. Ordinary /build remains available at its
ordinary price.

## Automatic operation

The company considers **all currently owned, active provinces**. Newly acquired
provinces are included automatically; lost or inactive provinces are excluded.
There is no province whitelist, selected supply resource, configurable reserve
or one-investment-per-month limit.

Each game month it chooses between new construction and upgrades in its
specialties. It prioritizes food production or a low food buffer, then missing
production/construction materials, then further output. Within a priority it
compares added base output per gold. After each success it reads the updated
state and chooses again, until the remaining allowance or available resources,
workers, technology or profitable options prevent further construction.
The report lists investments and the reason for the next wait.

The forecast includes construction payments and current manual worker
allocations. Automation rejects investments without a production gain, those
worsening food shortages, or those causing an unsustainable operating balance
or increased arrears. It does not buy resources from other players.

Automatic construction operates domestically. Foreign construction remains
an explicit manual action under an accepted concession. Existing foreign
plants continue their monthly production and deliveries.

## Existing companies

Old Supply, Expansion and Upgrades settings become Automatic; Manual stays
Manual, and paused companies remain paused. The previous gold amount becomes
the monthly allowance. A recorded investment in the current month counts
toward it. Old province/resource selections and reserves are removed.
Settings migrate when read and persist with the next company update/tick;
no database reset or new environment variable is needed.

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
maintenance from the company nation's stocks. If those funds are unavailable,
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
discounts, shared monthly budgets, forecast rollback, foreign
payments and production shares, unused escrow refunds, war suspension,
termination, narrated improvement approval and technology caps.
GitHub Actions runs the Python suite with dummy credentials and temporary
SQLite databases. A live Discord/Render/PostgreSQL rollout is not performed
by these tests.

The simplified automation has regression coverage for multiple investments,
automatic construction/upgrading, domestic scope despite foreign concessions,
budget renewal and edits, material/worker constraints, legacy settings and
the one-field Polish/English budget form.
