"""
db.py - database helpers for ChainLedger

We use SQLite through Python's built-in sqlite3 module, so there is no
separate database server to install. The whole database is one file:
instance/chainledger.db. The anchor lives next to it in instance/anchor.json
(deliberately OUTSIDE the database - see ledger.py).
"""

import os
import sqlite3

from flask import g

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
DB_PATH = os.path.join(INSTANCE_DIR, "chainledger.db")
ANCHOR_PATH = os.path.join(INSTANCE_DIR, "anchor.json")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,          -- werkzeug salted hash, never the plain password
    created_at    TEXT    NOT NULL
);

-- Each row is one block of the chain.
CREATE TABLE IF NOT EXISTS records (
    id            INTEGER PRIMARY KEY,       -- block number (genesis = 1)
    roll_no       TEXT    NOT NULL,
    student_name  TEXT    NOT NULL,
    subject       TEXT    NOT NULL,
    marks         INTEGER NOT NULL,
    semester      INTEGER NOT NULL,
    created_at    TEXT    NOT NULL,
    prev_hash     TEXT    NOT NULL,          -- hash of the previous block
    hash          TEXT    NOT NULL           -- SHA-256 of this block's fields + prev_hash
);

CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at    TEXT NOT NULL,
    username  TEXT NOT NULL,
    source    TEXT NOT NULL,                 -- e.g. "Verify Chain" or "Public check #5"
    result    TEXT NOT NULL,                 -- VALID or COMPROMISED
    details   TEXT
);
"""


def connect():
    """Open a new connection to the SQLite database file."""
    os.makedirs(INSTANCE_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row   # rows behave like dicts: row["marks"]
    return conn


def create_tables(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def is_initialised():
    """True if the database file exists and already contains the chain."""
    if not os.path.exists(DB_PATH):
        return False
    conn = connect()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='records'"
        ).fetchone()
        if row is None:
            return False
        return conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] > 0
    finally:
        conn.close()


# --- Flask integration: one connection per request -------------------------

def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_app(app):
    app.teardown_appcontext(close_db)


# --- Audit log ---------------------------------------------------------------

def log_verification(conn, run_at, username, source, result, details):
    conn.execute(
        "INSERT INTO audit_log (run_at, username, source, result, details) VALUES (?, ?, ?, ?, ?)",
        (run_at, username, source, result, details),
    )
    conn.commit()


def last_verification(conn):
    return conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
