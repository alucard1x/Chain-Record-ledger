"""
ledger.py - the heart of ChainLedger
=====================================

This file contains ALL of the hashing and verification logic. Nothing in here
depends on Flask, so it can be read and tested on its own.

THE IDEA IN ONE PARAGRAPH
-------------------------
Every student record is stored as a "block". Each block keeps:
    * its own data (roll no, name, subject, marks, semester, time),
    * prev_hash  -> the hash of the block before it,
    * hash       -> SHA-256 of its own data + prev_hash.
Because every hash includes the previous block's hash, the blocks form a
chain. Change one mark anywhere and that block's hash no longer matches its
data (TAMPERED). Try to fix it by storing a new hash and the NEXT block's
prev_hash no longer matches (BROKEN LINK). The only way to hide an edit is to
recompute EVERY hash after it - which is why we also keep an ANCHOR.

THE ANCHOR
----------
After every new record we save two numbers OUTSIDE the database, in
instance/anchor.json:
    * latest_hash   - the hash of the newest block (the "tip" of the chain)
    * record_count  - how many student records the chain should contain
A hacker who edits the database and recomputes every hash produces a chain
that looks internally perfect, but its final hash is now different from the
anchored one - so verification still catches it. Deleting a record changes
the count, which is also caught.

In a real deployment the anchor would be kept somewhere the database attacker
cannot reach: e.g. emailed to an administrator after every update, printed on
each certificate, or published somewhere public. Here we keep it in a
separate file outside the database.

VERIFICATION RESULTS PER BLOCK
------------------------------
    VALID        - hash recomputes correctly AND prev_hash links correctly
    TAMPERED     - recomputed hash != stored hash  (data was changed)
    BROKEN LINK  - prev_hash != previous block's stored hash
                   (a block was removed/re-ordered, or a hash was forged)
Plus two whole-chain checks against the anchor:
    ANCHOR MISMATCH        - chain tip != anchored hash (chain was rebuilt)
    RECORD COUNT MISMATCH  - number of records != anchored count (deletion)
"""

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone

# The genesis block is the first block of every chain. It has no previous
# block, so its prev_hash is 64 zeros (the same length as a SHA-256 hex hash).
GENESIS_ID = 1
GENESIS_PREV_HASH = "0" * 64

# The exact fields (and order) that go into a block's hash.
# hash = SHA256("id|roll_no|student_name|subject|marks|semester|created_at|prev_hash")
HASH_FIELDS = (
    "id",
    "roll_no",
    "student_name",
    "subject",
    "marks",
    "semester",
    "created_at",
    "prev_hash",
)

STATUS_VALID = "VALID"
STATUS_TAMPERED = "TAMPERED"
STATUS_BROKEN = "BROKEN LINK"


class LedgerError(Exception):
    """Raised when the ledger cannot perform an operation safely."""


# ---------------------------------------------------------------------------
# 1. HASHING
# ---------------------------------------------------------------------------

def now_utc():
    """Current time as text. Stored with the block and included in its hash."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def build_hash_input(block):
    """Join the block's fields with '|' - this exact string is what we hash.

    Example:
        "2|STU25-001|Aarav Sharma|Cloud Computing|86|1|2026-09-24 10:15:00|9f86d0..."
    """
    return "|".join(str(block[field]) for field in HASH_FIELDS)


def compute_hash(block):
    """Return the SHA-256 hash (64 hex characters) of a block.

    `block` can be a dict or a database row - anything that supports
    block["field_name"].

    Why SHA-256?
      * Same input  -> always the same output (deterministic)
      * Tiny change -> completely different output (avalanche effect)
      * Cannot be reversed, and finding two inputs with the same hash
        (a collision) is computationally infeasible.
    """
    return hashlib.sha256(build_hash_input(block).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 2. THE ANCHOR (stored outside the database)
# ---------------------------------------------------------------------------

def write_anchor(anchor_path, latest_hash, record_count):
    """Save the chain tip and record count to anchor.json.

    We write to a temporary file first and then rename it, so the anchor is
    never left half-written if the server crashes mid-write.
    """
    anchor = {
        "latest_hash": latest_hash,
        "record_count": record_count,
        "updated_at": now_utc(),
        "note": (
            "Tamper-evidence anchor. In production this value would be emailed "
            "to an administrator or printed on certificates, outside the database."
        ),
    }
    folder = os.path.dirname(anchor_path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=folder, prefix=".anchor-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(anchor, f, indent=2)
    os.replace(tmp_path, anchor_path)
    return anchor


def read_anchor(anchor_path):
    """Load anchor.json. Returns None if it is missing or unreadable."""
    try:
        with open(anchor_path, encoding="utf-8") as f:
            anchor = json.load(f)
        if "latest_hash" in anchor and "record_count" in anchor:
            return anchor
    except (OSError, ValueError):
        pass
    return None


# ---------------------------------------------------------------------------
# 3. CREATING BLOCKS
# ---------------------------------------------------------------------------

def _count_records(conn):
    """Number of student records (every block except the genesis block)."""
    return conn.execute(
        "SELECT COUNT(*) FROM records WHERE id != ?", (GENESIS_ID,)
    ).fetchone()[0]


def _begin_write(conn):
    """Start a write transaction that locks the database.

    BEGIN IMMEDIATE stops two requests from reading the same "last block" at
    the same moment and both appending after it.
    """
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")


def create_genesis_block(conn, anchor_path):
    """Create block #1 - the fixed starting point of the chain."""
    _begin_write(conn)
    try:
        block = {
            "id": GENESIS_ID,
            "roll_no": "GENESIS",
            "student_name": "Genesis Block",
            "subject": "-",
            "marks": 0,
            "semester": 0,
            "created_at": now_utc(),
            "prev_hash": GENESIS_PREV_HASH,
        }
        block["hash"] = compute_hash(block)
        _insert_block(conn, block)
        write_anchor(anchor_path, block["hash"], 0)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return block


def add_block(conn, anchor_path, roll_no, student_name, subject, marks, semester):
    """Append a new student record to the end of the chain.

    Steps:
      1. Read the last block and take its hash  -> this becomes prev_hash.
      2. Give the new block the next id and the current time.
      3. hash = SHA256(id|roll_no|name|subject|marks|semester|created_at|prev_hash)
      4. Insert the block.
      5. Update the anchor with the new tip hash and record count.
    """
    _begin_write(conn)
    try:
        last = conn.execute(
            "SELECT id, hash FROM records ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if last is None:
            raise LedgerError("The ledger has no genesis block. Run init_db.py first.")

        block = {
            "id": last["id"] + 1,
            "roll_no": roll_no,
            "student_name": student_name,
            "subject": subject,
            "marks": int(marks),
            "semester": int(semester),
            "created_at": now_utc(),
            "prev_hash": last["hash"],
        }
        block["hash"] = compute_hash(block)
        _insert_block(conn, block)

        # The anchor is written while we still hold the database lock, so two
        # simultaneous requests can never leave an out-of-date anchor behind.
        write_anchor(anchor_path, block["hash"], _count_records(conn))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return block


def _insert_block(conn, block):
    conn.execute(
        """INSERT INTO records
               (id, roll_no, student_name, subject, marks, semester,
                created_at, prev_hash, hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            block["id"], block["roll_no"], block["student_name"], block["subject"],
            block["marks"], block["semester"], block["created_at"],
            block["prev_hash"], block["hash"],
        ),
    )


# ---------------------------------------------------------------------------
# 4. VERIFICATION
# ---------------------------------------------------------------------------

def verify_chain(conn, anchor_path):
    """Re-check every block and compare the chain with the anchor.

    Returns a dictionary:
        ok        - True only if every check passed
        blocks    - one entry per block with its status (VALID / TAMPERED /
                    BROKEN LINK) and the hashes that were compared
        issues    - human-readable list of every problem found
        anchor    - the anchor that was read (or None)
        ...plus counts and the chain tip for display
    """
    rows = conn.execute("SELECT * FROM records ORDER BY id").fetchall()
    blocks = []
    issues = []

    # --- Check 0: the chain must start with the genesis block --------------
    if not rows or rows[0]["id"] != GENESIS_ID:
        issues.append("GENESIS MISSING: the chain does not start with the genesis block.")

    # --- Check 1 & 2: walk the chain block by block ------------------------
    expected_prev = GENESIS_PREV_HASH   # what the first block's prev_hash must be
    prev_id = None
    for row in rows:
        recomputed = compute_hash(row)
        data_ok = recomputed == row["hash"]        # has the data been changed?
        link_ok = row["prev_hash"] == expected_prev  # does it point to the block before?

        if not data_ok:
            status = STATUS_TAMPERED
            issues.append(
                f"Block #{row['id']} is TAMPERED: its data no longer produces its stored hash."
            )
        elif not link_ok:
            status = STATUS_BROKEN
        else:
            status = STATUS_VALID

        if not link_ok:
            where = f"block #{prev_id}" if prev_id is not None else "the genesis value (64 zeros)"
            issues.append(
                f"Block #{row['id']} has a BROKEN LINK: its prev_hash does not match {where}."
            )

        blocks.append({
            "record": dict(row),
            "status": status,
            "data_ok": data_ok,
            "link_ok": link_ok,
            "recomputed_hash": recomputed,
            "expected_prev": expected_prev,
            "prev_id": prev_id,
            "is_genesis": row["id"] == GENESIS_ID,
        })

        # The next block must point to THIS block's stored hash.
        expected_prev = row["hash"]
        prev_id = row["id"]

    # --- Check 3 & 4: compare with the anchor kept outside the database ----
    anchor = read_anchor(anchor_path)
    chain_tip = rows[-1]["hash"] if rows else None
    record_count = sum(1 for r in rows if r["id"] != GENESIS_ID)
    anchor_hash_ok = count_ok = False

    if anchor is None:
        issues.append("ANCHOR MISSING: instance/anchor.json could not be read, so the chain cannot be trusted.")
    else:
        anchor_hash_ok = chain_tip == anchor["latest_hash"]
        count_ok = record_count == anchor["record_count"]
        if not anchor_hash_ok:
            issues.append(
                "ANCHOR MISMATCH: the chain's final hash differs from the anchored hash - "
                "the chain was rebuilt or altered after the last genuine update."
            )
        if not count_ok:
            diff = anchor["record_count"] - record_count
            if diff > 0:
                issues.append(
                    f"RECORD COUNT MISMATCH: the ledger holds {record_count} records but the anchor "
                    f"expects {anchor['record_count']} - {diff} record(s) have been deleted."
                )
            else:
                issues.append(
                    f"RECORD COUNT MISMATCH: the ledger holds {record_count} records but the anchor "
                    f"expects {anchor['record_count']} - {-diff} record(s) were inserted outside the application."
                )

    return {
        "ok": not issues,
        "blocks": blocks,
        "issues": issues,
        "anchor": anchor,
        "chain_tip": chain_tip,
        "record_count": record_count,
        "anchor_hash_ok": anchor_hash_ok,
        "count_ok": count_ok,
        "valid_blocks": sum(1 for b in blocks if b["status"] == STATUS_VALID),
        "bad_blocks": sum(1 for b in blocks if b["status"] != STATUS_VALID),
        "checked_at": now_utc(),
    }


def find_block(result, record_id):
    """Pick one block's verification entry out of a verify_chain() result."""
    for block in result["blocks"]:
        if block["record"]["id"] == record_id:
            return block
    return None
