"""
Thin SQLite access layer. WAL mode for safe concurrent read/write.
SCHEMA is the single source of truth for all tables - safe to run on every startup.
"""
import sqlite3
from contextlib import contextmanager

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_prefs (
    user_id  TEXT PRIMARY KEY,
    language TEXT NOT NULL DEFAULT 'en'
);

CREATE TABLE IF NOT EXISTS nations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id            TEXT NOT NULL UNIQUE,
    name                TEXT NOT NULL UNIQUE,
    flag                TEXT NOT NULL DEFAULT '',
    government_type     TEXT NOT NULL DEFAULT 'Monarchy',
    capital_province_id INTEGER,
    treasury            REAL NOT NULL DEFAULT 0,
    stability           REAL NOT NULL DEFAULT 50,
    population          INTEGER NOT NULL DEFAULT 0,
    resources_json      TEXT NOT NULL DEFAULT '{}',
    tech_json           TEXT NOT NULL DEFAULT '{"naval":3.0,"land":3.0,"economy":3.0,"colonial":3.0}',
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS nation_history (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    nation_id INTEGER NOT NULL REFERENCES nations(id) ON DELETE CASCADE,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    source    TEXT NOT NULL DEFAULT 'system',
    entry_text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provinces (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    azgaar_cell_id      INTEGER NOT NULL UNIQUE,
    owner_nation_id     INTEGER REFERENCES nations(id) ON DELETE SET NULL,
    name                TEXT NOT NULL DEFAULT '',
    biome               TEXT NOT NULL DEFAULT 'unknown',
    terrain             TEXT NOT NULL DEFAULT 'plains',
    base_resources_json TEXT NOT NULL DEFAULT '{}',
    population          INTEGER NOT NULL DEFAULT 0,
    buildings_json      TEXT NOT NULL DEFAULT '[]',
    fortification_level INTEGER NOT NULL DEFAULT 0,
    active              INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_provinces_owner ON provinces(owner_nation_id);
CREATE INDEX IF NOT EXISTS idx_provinces_cell  ON provinces(azgaar_cell_id);

CREATE TABLE IF NOT EXISTS building_defs (
    key             TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    tier            INTEGER NOT NULL DEFAULT 1,
    cost_json       TEXT NOT NULL DEFAULT '{}',
    effect_json     TEXT NOT NULL DEFAULT '{}',
    upkeep_json     TEXT NOT NULL DEFAULT '{}',
    requires_terrain TEXT NOT NULL DEFAULT '',
    requires_tech   REAL NOT NULL DEFAULT 0.0,
    description     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS game_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS megaprojects (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    nation_id           INTEGER NOT NULL REFERENCES nations(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    proposed_effect     TEXT NOT NULL DEFAULT '',
    effect_json         TEXT NOT NULL DEFAULT '{}',
    cost_json           TEXT NOT NULL DEFAULT '{}',
    duration_months     INTEGER NOT NULL DEFAULT 0,
    months_spent        INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'proposed',
    gm_notes            TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at        TEXT
);

CREATE TABLE IF NOT EXISTS trades (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    from_nation_id      INTEGER NOT NULL REFERENCES nations(id) ON DELETE CASCADE,
    to_nation_id        INTEGER NOT NULL REFERENCES nations(id) ON DELETE CASCADE,
    offer_resources_json    TEXT NOT NULL DEFAULT '{}',
    offer_gold              REAL NOT NULL DEFAULT 0,
    receive_resources_json  TEXT NOT NULL DEFAULT '{}',
    receive_gold            REAL NOT NULL DEFAULT 0,
    public_note         TEXT NOT NULL DEFAULT '',
    private_note        TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'pending',
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at         TEXT
);

CREATE TABLE IF NOT EXISTS blueprints (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nation_id       INTEGER NOT NULL REFERENCES nations(id) ON DELETE CASCADE,
    type            TEXT NOT NULL DEFAULT 'ship',
    name            TEXT NOT NULL,
    hull            TEXT NOT NULL DEFAULT '',
    components_json TEXT NOT NULL DEFAULT '[]',
    stats_json      TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS military_units (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nation_id       INTEGER NOT NULL REFERENCES nations(id) ON DELETE CASCADE,
    blueprint_id    INTEGER REFERENCES blueprints(id) ON DELETE SET NULL,
    unit_type       TEXT NOT NULL DEFAULT '',
    quantity        INTEGER NOT NULL DEFAULT 1,
    province_id     INTEGER REFERENCES provinces(id) ON DELETE SET NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nation_id       INTEGER NOT NULL REFERENCES nations(id) ON DELETE CASCADE,
    ai_draft_text   TEXT NOT NULL DEFAULT '',
    gm_final_text   TEXT NOT NULL DEFAULT '',
    effects_json    TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'draft',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    posted_at       TEXT
);

CREATE TABLE IF NOT EXISTS battle_plans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nation_id       INTEGER NOT NULL REFERENCES nations(id) ON DELETE CASCADE,
    forces_json     TEXT NOT NULL DEFAULT '[]',
    provinces_json  TEXT NOT NULL DEFAULT '[]',
    orders_text     TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'unmatched',
    submitted_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS battles (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_a_id             INTEGER NOT NULL REFERENCES battle_plans(id),
    plan_b_id             INTEGER NOT NULL REFERENCES battle_plans(id),
    gm_note               TEXT NOT NULL DEFAULT '',
    status                TEXT NOT NULL DEFAULT 'pending',
    ai_modifier_json      TEXT NOT NULL DEFAULT '{}',
    gm_final_modifier_json TEXT NOT NULL DEFAULT '{}',
    report_json           TEXT NOT NULL DEFAULT '{}',
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at           TEXT
);

CREATE TABLE IF NOT EXISTS relations (
    nation_a_id INTEGER NOT NULL REFERENCES nations(id) ON DELETE CASCADE,
    nation_b_id INTEGER NOT NULL REFERENCES nations(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'peace',
    PRIMARY KEY (nation_a_id, nation_b_id)
);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def cursor():
    conn = get_connection()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    finally:
        conn.close()
