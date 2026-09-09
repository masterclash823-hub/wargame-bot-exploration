"""
Database layer — PostgreSQL via Supabase.
Reads DATABASE_URL from environment (set in Render Secrets).
Falls back to SQLite at /tmp/wargame.db if DATABASE_URL is not set,
so local dev still works without Supabase.
"""
import os
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar

_transaction = ContextVar('database_transaction', default=None)

try:
    import psycopg2
    import psycopg2.extras
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

DATABASE_URL = os.getenv("DATABASE_URL", "")
USE_POSTGRES  = bool(DATABASE_URL and HAS_PSYCOPG2)

# ---------------------------------------------------------------------------
# Schema — written in PostgreSQL syntax.
# SQLite differences handled by _sqlite_schema() below.
# ---------------------------------------------------------------------------
SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS economy_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS user_prefs (
    user_id  TEXT PRIMARY KEY,
    language TEXT NOT NULL DEFAULT 'en'
);

CREATE TABLE IF NOT EXISTS nations (
    id                  SERIAL PRIMARY KEY,
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
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS nation_history (
    id          SERIAL PRIMARY KEY,
    nation_id   INTEGER NOT NULL REFERENCES nations(id) ON DELETE CASCADE,
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source      TEXT NOT NULL DEFAULT 'system',
    entry_text  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provinces (
    id                  SERIAL PRIMARY KEY,
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

CREATE TABLE IF NOT EXISTS province_neighbors (
    cell_id          INTEGER NOT NULL REFERENCES provinces(azgaar_cell_id) ON DELETE CASCADE,
    neighbor_cell_id INTEGER NOT NULL REFERENCES provinces(azgaar_cell_id) ON DELETE CASCADE,
    PRIMARY KEY (cell_id, neighbor_cell_id)
);

CREATE INDEX IF NOT EXISTS idx_province_neighbors_neighbor
    ON province_neighbors(neighbor_cell_id);

CREATE TABLE IF NOT EXISTS building_defs (
    key              TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    tier             INTEGER NOT NULL DEFAULT 1,
    cost_json        TEXT NOT NULL DEFAULT '{}',
    effect_json      TEXT NOT NULL DEFAULT '{}',
    upkeep_json      TEXT NOT NULL DEFAULT '{}',
    requires_terrain TEXT NOT NULL DEFAULT '',
    requires_tech    REAL NOT NULL DEFAULT 0.0,
    description      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS megaprojects (
    id              SERIAL PRIMARY KEY,
    nation_id       INTEGER NOT NULL REFERENCES nations(id) ON DELETE CASCADE,
    province_id     INTEGER REFERENCES provinces(id),
    name            TEXT NOT NULL,
    proposed_effect TEXT NOT NULL DEFAULT '',
    cost_json       TEXT NOT NULL DEFAULT '{}',
    effect_json     TEXT NOT NULL DEFAULT '{}',
    duration_months INTEGER NOT NULL DEFAULT 0,
    months_spent    INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'proposed',
    gm_notes        TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS blueprints (
    id              SERIAL PRIMARY KEY,
    nation_id       INTEGER NOT NULL REFERENCES nations(id) ON DELETE CASCADE,
    type            TEXT NOT NULL DEFAULT 'ship',
    name            TEXT NOT NULL,
    hull            TEXT NOT NULL DEFAULT '',
    components_json TEXT NOT NULL DEFAULT '[]',
    stats_json      TEXT NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS military_units (
    id              SERIAL PRIMARY KEY,
    nation_id       INTEGER NOT NULL REFERENCES nations(id) ON DELETE CASCADE,
    blueprint_id    INTEGER REFERENCES blueprints(id) ON DELETE SET NULL,
    unit_type       TEXT NOT NULL DEFAULT '',
    quantity        INTEGER NOT NULL DEFAULT 1,
    province_id     INTEGER REFERENCES provinces(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS trade_routes (
    id           SERIAL PRIMARY KEY,
    nation_id    INTEGER NOT NULL REFERENCES nations(id) ON DELETE CASCADE,
    from_cell_id INTEGER NOT NULL,
    to_cell_id   INTEGER NOT NULL,
    name         TEXT NOT NULL DEFAULT '',
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS colonies (
    id               SERIAL PRIMARY KEY,
    nation_id        INTEGER NOT NULL REFERENCES nations(id) ON DELETE CASCADE,
    province_id      INTEGER NOT NULL REFERENCES provinces(id) ON DELETE CASCADE,
    name             TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'outpost',
    months_in_status INTEGER NOT NULL DEFAULT 0,
    investment_json  TEXT NOT NULL DEFAULT '{}',
    gm_notes         TEXT NOT NULL DEFAULT '',
    founded_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(province_id)
);

CREATE TABLE IF NOT EXISTS relations (
    nation_a_id INTEGER NOT NULL REFERENCES nations(id) ON DELETE CASCADE,
    nation_b_id INTEGER NOT NULL REFERENCES nations(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'peace',
    PRIMARY KEY (nation_a_id, nation_b_id)
);

CREATE TABLE IF NOT EXISTS trades (
    id                       SERIAL PRIMARY KEY,
    from_nation_id           INTEGER NOT NULL REFERENCES nations(id) ON DELETE CASCADE,
    to_nation_id             INTEGER NOT NULL REFERENCES nations(id) ON DELETE CASCADE,
    offer_resources_json     TEXT NOT NULL DEFAULT '{}',
    offer_gold               REAL NOT NULL DEFAULT 0,
    receive_resources_json   TEXT NOT NULL DEFAULT '{}',
    receive_gold             REAL NOT NULL DEFAULT 0,
    public_note              TEXT NOT NULL DEFAULT '',
    private_note             TEXT NOT NULL DEFAULT '',
    status                   TEXT NOT NULL DEFAULT 'pending',
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at              TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS battle_plans (
    id            SERIAL PRIMARY KEY,
    nation_id     INTEGER NOT NULL REFERENCES nations(id) ON DELETE CASCADE,
    forces_json   TEXT NOT NULL DEFAULT '[]',
    provinces_json TEXT NOT NULL DEFAULT '[]',
    orders_text   TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'unmatched',
    submitted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS battles (
    id                     SERIAL PRIMARY KEY,
    plan_a_id              INTEGER NOT NULL REFERENCES battle_plans(id),
    plan_b_id              INTEGER NOT NULL REFERENCES battle_plans(id),
    gm_note                TEXT NOT NULL DEFAULT '',
    status                 TEXT NOT NULL DEFAULT 'pending',
    ai_modifier_json       TEXT NOT NULL DEFAULT '{}',
    gm_final_modifier_json TEXT NOT NULL DEFAULT '{}',
    report_json            TEXT NOT NULL DEFAULT '{}',
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at            TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS events (
    id            SERIAL PRIMARY KEY,
    nation_id     INTEGER NOT NULL REFERENCES nations(id) ON DELETE CASCADE,
    ai_draft_text TEXT NOT NULL DEFAULT '',
    gm_final_text TEXT NOT NULL DEFAULT '',
    effects_json  TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'draft',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    posted_at     TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS event_publications (
    event_id INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
    visibility TEXT NOT NULL DEFAULT 'private',
    channel_id TEXT
);

CREATE TABLE IF NOT EXISTS event_runs (
    event_id INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
    version INTEGER NOT NULL DEFAULT 0,
    state_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS game_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tech (
    nation_id     INTEGER NOT NULL REFERENCES nations(id) ON DELETE CASCADE,
    category      TEXT NOT NULL,
    level         REAL NOT NULL DEFAULT 3.0,
    last_drift_ts TIMESTAMPTZ,
    PRIMARY KEY (nation_id, category)
);

CREATE TABLE IF NOT EXISTS economy_policy (
    nation_id INTEGER PRIMARY KEY REFERENCES nations(id) ON DELETE CASCADE,
    tax TEXT NOT NULL DEFAULT 'normal', priority TEXT NOT NULL DEFAULT 'balanced',
    luxury TEXT NOT NULL DEFAULT 'auto', unrest REAL NOT NULL DEFAULT 0,
    arrears REAL NOT NULL DEFAULT 0, unpaid_months INTEGER NOT NULL DEFAULT 0,
    hunger_months INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS province_development (
    province_id INTEGER PRIMARY KEY REFERENCES provinces(id) ON DELETE CASCADE,
    levels_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS military_posture (
    unit_id INTEGER PRIMARY KEY REFERENCES military_units(id) ON DELETE CASCADE,
    mode TEXT NOT NULL DEFAULT 'active', ready_month INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS economy_months (
    month_index INTEGER PRIMARY KEY, report_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS trade_contracts (
    trade_id INTEGER PRIMARY KEY REFERENCES trades(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'proposed', last_month INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS route_assignments (
    route_id INTEGER PRIMARY KEY REFERENCES trade_routes(id) ON DELETE CASCADE,
    ship_id INTEGER NOT NULL UNIQUE REFERENCES military_units(id) ON DELETE CASCADE
);
"""

# SQLite version — same structure, SQLite-compatible types
SCHEMA_SQLITE = SCHEMA_PG\
    .replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")\
    .replace("TIMESTAMPTZ NOT NULL DEFAULT NOW()", "TEXT NOT NULL DEFAULT (datetime('now'))")\
    .replace("TIMESTAMPTZ", "TEXT")\
    .replace("REAL NOT NULL DEFAULT NOW()", "TEXT NOT NULL DEFAULT (datetime('now'))")\
    .replace("NOW()", "(datetime('now'))")


# ---------------------------------------------------------------------------
# Row wrapper so both backends return dict-like rows
# ---------------------------------------------------------------------------
class _Row(dict):
    def __getitem__(self, key):
        return super().__getitem__(key)
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


def _wrap(rows):
    if rows is None:
        return None
    if isinstance(rows, list):
        return [_Row(r) for r in rows]
    return _Row(rows)


# ---------------------------------------------------------------------------
# Postgres connection
# ---------------------------------------------------------------------------
def _pg_conn():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    conn.autocommit = False
    return conn


# ---------------------------------------------------------------------------
# SQLite connection (fallback)
# ---------------------------------------------------------------------------
def _sqlite_conn():
    db_path = os.getenv("DB_PATH", "/tmp/wargame.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


# ---------------------------------------------------------------------------
# Unified cursor context manager
# ---------------------------------------------------------------------------
class _UnifiedCursor:
    """Wraps a DB cursor so both PG and SQLite look identical to callers."""

    def __init__(self, conn, is_pg):
        self._conn   = conn
        self._is_pg  = is_pg
        self._cur    = conn.cursor()
        self.lastrowid = None
        self.rowcount  = 0

    def execute(self, sql, params=()):
        # Convert ? placeholders to %s for Postgres
        if self._is_pg:
            sql = sql.replace("?", "%s")
            # Convert AUTOINCREMENT syntax (shouldn't appear at runtime, but safety)
            sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        self._cur.execute(sql, params)
        self.rowcount = self._cur.rowcount
        if self._is_pg:
            # Get last inserted id for INSERT statements
            try:
                if self._cur.description and self._cur.rowcount > 0:
                    pass  # SELECT — no lastrowid needed
            except Exception:
                pass
        else:
            self.lastrowid = self._cur.lastrowid

    def executescript(self, sql):
        """Only used for schema init."""
        if self._is_pg:
            # Split on ; and run each statement
            for stmt in sql.split(";"):
                stmt = stmt.strip()
                if stmt:
                    try:
                        self._cur.execute(stmt)
                    except Exception as e:
                        if "already exists" not in str(e).lower():
                            print(f"[DB] Schema warning: {e}", flush=True)
                        self._conn.rollback()
                        self._conn = _pg_conn()
                        self._cur  = self._conn.cursor()
        else:
            self._conn.executescript(sql)

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        if self._is_pg:
            return _Row(dict(row))
        # SQLite Row → _Row
        return _Row({k: row[k] for k in row.keys()})

    def fetchall(self):
        rows = self._cur.fetchall()
        if self._is_pg:
            return [_Row(dict(r)) for r in rows]
        return [_Row({k: r[k] for k in r.keys()}) for r in rows]


@contextmanager
def cursor():
    current = _transaction.get()
    if current is not None:
        yield current
        return
    if USE_POSTGRES:
        conn = _pg_conn()
        try:
            cur = _UnifiedCursor(conn, is_pg=True)
            yield cur
            # For INSERT RETURNING get lastrowid
            try:
                if cur._cur.description:
                    row = cur._cur.fetchone()
                    if row and cur.lastrowid is None:
                        # Try to get id from RETURNING clause if present
                        pass
            except Exception:
                pass
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        conn = _sqlite_conn()
        try:
            cur = _UnifiedCursor(conn, is_pg=False)
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


# ---------------------------------------------------------------------------
@contextmanager
def atomic():
    """Share one transaction across synchronous service calls, never across awaits."""
    if _transaction.get() is not None:
        yield _transaction.get()
        return
    with cursor() as cur:
        if not USE_POSTGRES:
            cur.execute('BEGIN IMMEDIATE')
        token = _transaction.set(cur)
        try:
            yield cur
        finally:
            _transaction.reset(token)


# INSERT helper that returns the new row's id reliably on both backends
# ---------------------------------------------------------------------------
def insert_returning_id(sql: str, params: tuple) -> int:
    """
    Execute an INSERT and return the new row's id.
    On Postgres: appends RETURNING id.
    On SQLite: uses lastrowid.
    """
    if _transaction.get() is not None:
        cur = _transaction.get()
        statement=sql.rstrip().rstrip(';')
        if not statement.upper().endswith('RETURNING ID'):statement+=' RETURNING id'
        cur.execute(statement, params)
        return cur.fetchone()['id']
    if USE_POSTGRES:
        if not sql.strip().upper().endswith("RETURNING id"):
            sql = sql.rstrip().rstrip(";") + " RETURNING id"
        sql = sql.replace("?", "%s")
        conn = _pg_conn()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql, params)
            row = cur.fetchone()
            conn.commit()
            return row["id"]
        finally:
            conn.close()
    else:
        conn = _sqlite_conn()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Schema init
# ---------------------------------------------------------------------------
def init_db() -> None:
    schema = SCHEMA_PG if USE_POSTGRES else SCHEMA_SQLITE
    if USE_POSTGRES:
        conn = _pg_conn()
        try:
            cur = conn.cursor()
            for stmt in schema.split(";"):
                stmt = stmt.strip()
                if stmt:
                    try:
                        cur.execute(stmt)
                    except Exception as e:
                        if "already exists" not in str(e).lower():
                            print(f"[DB] Schema warning: {e}", flush=True)
                        conn.rollback()
                        conn = _pg_conn()
                        cur  = conn.cursor()
            conn.commit()
        finally:
            conn.close()
    else:
        conn = _sqlite_conn()
        try:
            conn.executescript(schema)
            conn.commit()
        finally:
            conn.close()
    print(f"[DB] init_db complete ({'PostgreSQL/Supabase' if USE_POSTGRES else 'SQLite'})", flush=True)
