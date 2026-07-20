"""
Thin SQLite access layer. One shared connection, WAL mode for safe concurrent
read/write from the bot's async command handlers, and a schema bootstrap that only
creates what's missing so it's safe to run on every startup.

Only step-1 tables are here (what /help and /language need): user_prefs.
Later steps will add nations, provinces, resources, etc. to SCHEMA below -
this file itself won't need to change, just the schema string.
"""
import sqlite3
from contextlib import contextmanager

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_prefs (
    user_id     TEXT PRIMARY KEY,
    language    TEXT NOT NULL DEFAULT 'en'
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
