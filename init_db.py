"""
init_db.py - set up the ChainLedger database

Run once before starting the app:
    python init_db.py            # create tables, admin user, genesis block, sample data
    python init_db.py --reset    # WIPE everything and start again (users are kept)

It is safe to run more than once: existing data is left alone unless --reset
is given.

DEFAULT LOGIN:  admin / admin123
>>> Change this password after your first login (Account -> Change password). <<<
"""

import os
import sys

from werkzeug.security import generate_password_hash

import db
import ledger

DEFAULT_ADMIN_USER = "admin"
DEFAULT_ADMIN_PASSWORD = "admin123"
PASSWORD_METHOD = "pbkdf2:sha256"   # salted, slow hash; works on every Python build

# 8 sample student records used for the first run and for "Reset demo".
SAMPLE_RECORDS = [
    # roll_no,       student_name,       subject,                         marks, semester
    ("STU25-001", "Aarav Sharma",      "Cloud Computing",                 86, 3),
    ("STU25-002", "Priya Deshmukh",    "Cloud Computing",                 91, 3),
    ("STU25-003", "Rohan Kulkarni",    "Data Structures",                 74, 1),
    ("STU25-004", "Sneha Patil",       "Database Management Systems",     88, 2),
    ("STU25-005", "Vikram Joshi",      "Computer Networks",               67, 2),
    ("STU25-006", "Ananya Iyer",       "Machine Learning",                93, 3),
    ("STU25-007", "Karan Mehta",       "Software Engineering",            79, 1),
    ("STU25-008", "Neha Gupta",        "Web Technologies",                84, 2),
]


def create_admin(conn):
    """Create the default admin user if no user with that name exists."""
    exists = conn.execute(
        "SELECT 1 FROM users WHERE username = ?", (DEFAULT_ADMIN_USER,)
    ).fetchone()
    if exists:
        return False
    conn.execute(
        "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
        (
            DEFAULT_ADMIN_USER,
            generate_password_hash(DEFAULT_ADMIN_PASSWORD, method=PASSWORD_METHOD),
            ledger.now_utc(),
        ),
    )
    conn.commit()
    return True


def seed_chain(conn):
    """Create the genesis block followed by the 8 sample records."""
    ledger.create_genesis_block(conn, db.ANCHOR_PATH)
    for roll_no, name, subject, marks, semester in SAMPLE_RECORDS:
        ledger.add_block(conn, db.ANCHOR_PATH, roll_no, name, subject, marks, semester)


def wipe_chain(conn):
    """Delete every block and every audit entry, and remove the anchor file."""
    conn.execute("DELETE FROM records")
    conn.execute("DELETE FROM audit_log")
    conn.commit()
    if os.path.exists(db.ANCHOR_PATH):
        os.remove(db.ANCHOR_PATH)


def initialise(reset=False, verbose=True):
    say = print if verbose else (lambda *a, **k: None)
    conn = db.connect()
    try:
        db.create_tables(conn)
        say(f"Database: {db.DB_PATH}")

        if create_admin(conn):
            say(f"Created admin user '{DEFAULT_ADMIN_USER}' with password '{DEFAULT_ADMIN_PASSWORD}'.")
            say("  !! Please change this password after your first login.")

        if reset:
            wipe_chain(conn)
            say("Existing chain, audit log and anchor wiped.")

        has_blocks = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] > 0
        if has_blocks:
            say("Chain already exists - left unchanged (use --reset to start again).")
        else:
            seed_chain(conn)
            say(f"Created genesis block and {len(SAMPLE_RECORDS)} sample records.")
            say(f"Anchor written to: {db.ANCHOR_PATH}")
    finally:
        conn.close()


if __name__ == "__main__":
    initialise(reset="--reset" in sys.argv)
