"""
Thin SQLite access layer. One shared connection, WAL mode for safe concurrent
read/write from the bot's async command handlers, and a schema bootstrap that only
creates what's missing so it's safe to run on every startup.

SCHEMA grows with each build step - safe to add new CREATE TABLE IF NOT EXISTS blocks
here and they will be applied on next startup without touching existing data.
"""
import sqlite3
from contextlib import contextmanager

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_prefs (
    user_id     TEXT PRIMARY KEY,
    language    TEXT NOT NULL DEFAULT 'en'
);

CREATE TABLE IF NOT EXISTS nations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id            TEXT    NOT NULL UNIQUE,   -- Discord user ID; one nation per player
    name                TEXT    NOT NULL UNIQUE,
    flag                TEXT    NOT NULL DEFAULT '',
    government_type     TEXT    NOT NULL DEFAULT 'Monarchy',
    capital_province_id INTEGER,                   -- set later when provinces exist
    treasury            REAL    NOT NULL DEFAULT 0,
    stability           REAL    NOT NULL DEFAULT 50,
    population          INTEGER NOT NULL DEFAULT 0,
    resources_json      TEXT    NOT NULL DEFAULT '{}',
    tech_json           TEXT    NOT NULL DEFAULT '{"naval":3.0,"land":3.0,"economy":3.0,"colonial":3.0}',
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Append-only public history log per nation.
-- source: 'gm' | 'system' | 'ai' | 'player'
CREATE TABLE IF NOT EXISTS nation_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nation_id   INTEGER NOT NULL REFERENCES nations(id) ON DELETE CASCADE,
    timestamp   TEXT    NOT NULL DEFAULT (datetime('now')),
    source      TEXT    NOT NULL DEFAULT 'system',
    entry_text  TEXT    NOT NULL
);

-- Provinces imported from Azgaar. azgaar_cell_id matches Azgaar's cell i field.
-- active=0 means soft-deleted (removed from a map resync).
CREATE TABLE IF NOT EXISTS provinces (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    azgaar_cell_id      INTEGER NOT NULL UNIQUE,
    owner_nation_id     INTEGER REFERENCES nations(id) ON DELETE SET NULL,
    name                TEXT    NOT NULL DEFAULT '',
    biome               TEXT    NOT NULL DEFAULT 'unknown',
    terrain             TEXT    NOT NULL DEFAULT '',
    base_resources_json TEXT    NOT NULL DEFAULT '{}',
    population          INTEGER NOT NULL DEFAULT 0,
    buildings_json      TEXT    NOT NULL DEFAULT '[]',
    fortification_level INTEGER NOT NULL DEFAULT 0,
    active              INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_provinces_owner ON provinces(owner_nation_id);
CREATE INDEX IF NOT EXISTS idx_provinces_cell  ON provinces(azgaar_cell_id);

-- status: 'peace' | 'war' | 'alliance' | 'truce'
CREATE TABLE IF NOT EXISTS provinces (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    azgaar_cell_id      INTEGER NOT NULL UNIQUE,
    owner_nation_id     INTEGER REFERENCES nations(id) ON DELETE SET NULL,
    name                TEXT    NOT NULL DEFAULT '',
    biome               TEXT    NOT NULL DEFAULT 'unknown',
    terrain             TEXT    NOT NULL DEFAULT 'plains',
    base_resources_json TEXT    NOT NULL DEFAULT '{}',
    population          INTEGER NOT NULL DEFAULT 0,
    buildings_json      TEXT    NOT NULL DEFAULT '[]',
    fortification_level INTEGER NOT NULL DEFAULT 0,
    active              INTEGER NOT NULL DEFAULT 1   -- 0 = soft-deleted on map resync
);

CREATE INDEX IF NOT EXISTS idx_provinces_owner ON provinces(owner_nation_id);
CREATE INDEX IF NOT EXISTS idx_provinces_cell  ON provinces(azgaar_cell_id);

    nation_a_id INTEGER NOT NULL REFERENCES nations(id) ON DELETE CASCADE,
    nation_b_id INTEGER NOT NULL REFERENCES nations(id) ON DELETE CASCADE,
    status      TEXT    NOT NULL DEFAULT 'peace',
    PRIMARY KEY (nation_a_id, nation_b_id),
    CHECK (nation_a_id < nation_b_id)   -- enforce one row per pair, lower id first
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
    """Usage: with db.cursor() as cur: cur.execute(...); rows = cur.fetchall()"""
    conn = get_connection()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    finally:
        conn.close()
