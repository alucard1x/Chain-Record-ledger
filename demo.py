"""
demo.py - FOR DEMONSTRATION ONLY

These functions deliberately attack the ledger the way a dishonest insider
with direct database access might. They bypass ledger.add_block() on purpose,
so that the Verify Chain page can show how each attack is detected.

    tamper_marks    -> changes marks only             -> detected as TAMPERED
    hacker_rebuild  -> changes marks AND recomputes    -> every block looks VALID,
                       every hash in the chain            but ANCHOR MISMATCH
    delete_record   -> removes a row                   -> BROKEN LINK + COUNT MISMATCH
    reset_demo      -> wipes everything and reseeds 8 sample records
"""

import db
import init_db
import ledger


def _get_student_record(conn, record_id):
    row = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
    if row is None:
        raise ValueError(f"Record #{record_id} does not exist.")
    if row["id"] == ledger.GENESIS_ID:
        raise ValueError("The genesis block cannot be used for this demo.")
    return row


def tamper_marks(conn, record_id, new_marks):
    """Attack 1: edit marks straight in the database (hash left untouched)."""
    row = _get_student_record(conn, record_id)
    conn.execute("UPDATE records SET marks = ? WHERE id = ?", (int(new_marks), record_id))
    conn.commit()
    return row["marks"]


def hacker_rebuild(conn, record_id, new_marks):
    """Attack 2: edit marks, then recompute EVERY hash so all links look perfect.

    The attacker cannot touch the anchor (it lives outside the database), so the
    rebuilt chain ends in a different hash than the anchored one.
    """
    row = _get_student_record(conn, record_id)
    conn.execute("UPDATE records SET marks = ? WHERE id = ?", (int(new_marks), record_id))

    prev_hash = ledger.GENESIS_PREV_HASH
    for block in conn.execute("SELECT * FROM records ORDER BY id").fetchall():
        block = dict(block)
        block["prev_hash"] = prev_hash
        block["hash"] = ledger.compute_hash(block)
        conn.execute(
            "UPDATE records SET prev_hash = ?, hash = ? WHERE id = ?",
            (block["prev_hash"], block["hash"], block["id"]),
        )
        prev_hash = block["hash"]
    conn.commit()
    return row["marks"]


def delete_record(conn, record_id):
    """Attack 3: silently delete a record."""
    row = _get_student_record(conn, record_id)
    conn.execute("DELETE FROM records WHERE id = ?", (record_id,))
    conn.commit()
    return row


def reset_demo(conn):
    """Wipe the chain and audit log, then recreate genesis + 8 sample records."""
    init_db.wipe_chain(conn)
    init_db.seed_chain(conn)
    return db.ANCHOR_PATH
