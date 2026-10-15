"""
app.py - ChainLedger web application (Flask)

Pages
-----
Public (no login):
    /verify                  Public verification - enter a record ID
    /verify/<id>             Result for one record (this is what the QR code opens)
    /qr/<id>.png             QR code image for a record
    /login

Admin (login required):
    /dashboard               Totals, last verification, latest hash
    /records/add             Add a new record (creates a new block)
    /ledger                  The chain drawn as connected blocks
    /ledger/<id>             One block in full, with its QR code
    /verify-chain            Re-check the whole chain
    /audit                   Every verification run
    /demo                    Demo tools (tamper / rebuild / delete / reset)
    /account/password        Change password

Run locally:   python app.py        (debug mode, http://127.0.0.1:5050)
Production:    gunicorn app:app     (debug is OFF - see deploy/)
"""

import functools
import io
import math
import os
import secrets
from datetime import datetime, timezone

import qrcode
from flask import (
    Flask, abort, flash, g, redirect, render_template, request, send_file,
    session, url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import db
import demo
import init_db
import ledger

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
app = Flask(__name__)

# SECRET_KEY signs the login session cookie. On the server it comes from an
# environment variable (deploy/setup.sh generates a random one). The fallback
# below is only for local development.
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or "dev-only-insecure-key-change-me"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# Behind nginx: trust the X-Forwarded-For / X-Forwarded-Proto headers nginx
# adds, so generated URLs (e.g. inside QR codes) use the right scheme.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

db.init_app(app)

# First start with no database? Create it automatically (same as init_db.py).
if not db.is_initialised():
    init_db.initialise(verbose=True)


# ---------------------------------------------------------------------------
# Helpers: login, CSRF protection, template filters
# ---------------------------------------------------------------------------
def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Please log in to continue.", "info")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.before_request
def load_user():
    g.user = None
    user_id = session.get("user_id")
    if user_id is not None:
        g.user = db.get_db().execute(
            "SELECT id, username FROM users WHERE id = ?", (user_id,)
        ).fetchone()


def csrf_token():
    """A random token stored in the session and echoed in every form.

    A form submitted from another website will not know the token, so the
    request is rejected (protection against Cross-Site Request Forgery).
    """
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


@app.before_request
def csrf_protect():
    if request.method == "POST":
        sent = request.form.get("_csrf", "")
        if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
            abort(400, description="Your form session expired. Please go back, refresh the page and try again.")


app.jinja_env.globals["csrf_token"] = csrf_token


@app.template_filter("short")
def short_hash(value, head=10, tail=6):
    """9f86d081884c7d65...b0f00a08 -> easier to read on the ledger cards."""
    if not value:
        return "-"
    value = str(value)
    return value if len(value) <= head + tail + 1 else f"{value[:head]}…{value[-tail:]}"


@app.template_filter("age")
def age(timestamp):
    """'2026-09-24 19:35:27' (UTC) -> '5 mins ago', like a block explorer."""
    try:
        then = datetime.strptime(str(timestamp), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return timestamp
    secs = max(0, int((datetime.now(timezone.utc) - then).total_seconds()))
    for unit, size in (("day", 86400), ("hr", 3600), ("min", 60)):
        if secs >= size:
            n = secs // size
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return f"{secs} sec{'s' if secs != 1 else ''} ago"


def block_url(record_id):
    """Admins open the full block page; visitors open the public verification page."""
    if g.get("user"):
        return url_for("record_detail", record_id=record_id)
    if record_id == ledger.GENESIS_ID:
        return None
    return url_for("public_verify_record", record_id=record_id)


def chain_summary():
    """Live chain status for the header strip (computed once per request)."""
    if "chain_summary" not in g:
        r = ledger.verify_chain(db.get_db(), db.ANCHOR_PATH)
        g.chain_summary = {
            "ok": r["ok"], "blocks": len(r["blocks"]), "records": r["record_count"],
            "tip": r["chain_tip"], "anchor": r["anchor"], "issues": len(r["issues"]),
            "status": {b["record"]["id"]: b["status"] for b in r["blocks"]},
            "last_run": db.last_verification(db.get_db()),
        }
    return g.chain_summary


app.jinja_env.globals.update(block_url=block_url, chain_summary=chain_summary)

PER_PAGE = 25


def paginate(items, page):
    pages = max(1, math.ceil(len(items) / PER_PAGE))
    page = min(max(1, page), pages)
    return items[(page - 1) * PER_PAGE: page * PER_PAGE], page, pages


def page_arg():
    try:
        return int(request.args.get("page", 1))
    except ValueError:
        return 1


MARK_BANDS = [("0–39", 0, 39), ("40–59", 40, 59), ("60–74", 60, 74), ("75–89", 75, 89), ("90–100", 90, 100)]


def explorer_stats(conn):
    """Numbers and chart data for the home pages (public home + admin dashboard)."""
    rows = conn.execute(
        "SELECT id, student_name, subject, marks, semester FROM records WHERE id != ? ORDER BY id",
        (ledger.GENESIS_ID,),
    ).fetchall()
    marks = [r["marks"] for r in rows]
    bands = [
        {"label": label, "count": sum(1 for m in marks if lo <= m <= hi)}
        for label, lo, hi in MARK_BANDS
    ]
    peak = max([b["count"] for b in bands] + [1])
    for b in bands:
        b["pct"] = round(b["count"] * 100 / peak)
    latest = conn.execute("SELECT * FROM records ORDER BY id DESC LIMIT 1").fetchone()
    runs = conn.execute(
        "SELECT COUNT(*) AS total, SUM(result = 'VALID') AS passed FROM audit_log"
    ).fetchone()
    return {
        "total": len(rows),
        "subjects": len({r["subject"] for r in rows}),
        "avg_marks": round(sum(marks) / len(marks), 1) if marks else 0,
        "latest": latest,
        "last_run": db.last_verification(conn),
        "runs": runs["total"] or 0,
        "runs_passed": runs["passed"] or 0,
        "bands": bands,
        # Area chart: marks of the latest 24 blocks (names only for admins).
        "series": [
            {"id": r["id"], "y": r["marks"], "sem": r["semester"],
             "label": r["student_name"] if g.get("user") else r["subject"]}
            for r in rows[-24:]
        ],
        # Donut: records per semester - the three largest, the rest folded into "Other".
        "semesters": semester_mix(rows),
    }


def semester_mix(rows):
    counts = {}
    for r in rows:
        counts[r["semester"]] = counts.get(r["semester"], 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    mix = [{"label": f"Semester {sem}", "count": n} for sem, n in ranked[:3]]
    other = sum(n for _, n in ranked[3:])
    if other:
        mix.append({"label": "Other", "count": other})
    total = sum(m["count"] for m in mix) or 1
    for m in mix:
        m["pct"] = round(m["count"] * 100 / total)
    return mix


def run_verification(source, log=True):
    """Verify the whole chain and (optionally) record the run in the audit log."""
    conn = db.get_db()
    result = ledger.verify_chain(conn, db.ANCHOR_PATH)
    if log:
        username = g.user["username"] if g.user else "public"
        db.log_verification(
            conn,
            run_at=result["checked_at"],
            username=username,
            source=source,
            result="VALID" if result["ok"] else "COMPROMISED",
            details="All checks passed." if result["ok"] else " | ".join(result["issues"]),
        )
    return result


def student_records(conn):
    return conn.execute(
        "SELECT id, roll_no, student_name, subject, marks FROM records WHERE id != ? ORDER BY id",
        (ledger.GENESIS_ID,),
    ).fetchall()


def public_url_for(record_id):
    # _external=True builds the full URL from the host the visitor used
    # (127.0.0.1:5050 locally, the EC2 public IP in the cloud).
    return url_for("public_verify_record", record_id=record_id, _external=True)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return redirect(url_for("dashboard" if g.user else "public_verify"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = db.get_db().execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["default_password"] = password == init_db.DEFAULT_ADMIN_PASSWORD
            flash(f"Welcome, {user['username']}.", "success")
            next_url = request.args.get("next", "")
            # Only follow local redirects (never to another website).
            if next_url.startswith("/") and not next_url.startswith("//"):
                return redirect(next_url)
            return redirect(url_for("dashboard"))
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("public_verify"))


@app.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        conn = db.get_db()
        user = conn.execute("SELECT * FROM users WHERE id = ?", (g.user["id"],)).fetchone()
        if not check_password_hash(user["password_hash"], current):
            flash("Your current password is incorrect.", "error")
        elif len(new) < 8:
            flash("The new password must be at least 8 characters.", "error")
        elif new != confirm:
            flash("The new passwords do not match.", "error")
        else:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (generate_password_hash(new, method=init_db.PASSWORD_METHOD), user["id"]),
            )
            conn.commit()
            session["default_password"] = False
            flash("Password changed successfully.", "success")
            return redirect(url_for("dashboard"))
    return render_template("change_password.html")


# ---------------------------------------------------------------------------
# Admin pages
# ---------------------------------------------------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    conn = db.get_db()
    latest_blocks = conn.execute("SELECT * FROM records ORDER BY id DESC LIMIT 6").fetchall()
    latest_runs = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 6").fetchall()
    return render_template(
        "dashboard.html",
        stats=explorer_stats(conn),
        latest_blocks=latest_blocks,
        latest_runs=latest_runs,
    )


@app.route("/records/add", methods=["GET", "POST"])
@login_required
def add_record():
    form = {"roll_no": "", "student_name": "", "subject": "", "marks": "", "semester": ""}
    if request.method == "POST":
        form = {k: request.form.get(k, "").strip() for k in form}
        errors = []
        if not form["roll_no"]:
            errors.append("Roll number is required.")
        if not form["student_name"]:
            errors.append("Student name is required.")
        if not form["subject"]:
            errors.append("Subject is required.")
        if any("|" in form[k] for k in ("roll_no", "student_name", "subject")):
            errors.append("The '|' character is not allowed (it separates fields in the hash).")
        if any(len(form[k]) > 100 for k in ("roll_no", "student_name", "subject")):
            errors.append("Text fields must be 100 characters or fewer.")
        try:
            marks = int(form["marks"])
            if not 0 <= marks <= 100:
                raise ValueError
        except ValueError:
            errors.append("Marks must be a whole number between 0 and 100.")
        try:
            semester = int(form["semester"])
            if not 1 <= semester <= 8:
                raise ValueError
        except ValueError:
            errors.append("Semester must be a number between 1 and 8.")

        if not errors:
            conn = db.get_db()
            # Never append to a chain that has already been tampered with -
            # doing so would write a fresh anchor and hide the evidence.
            check = ledger.verify_chain(conn, db.ANCHOR_PATH)
            if not check["ok"]:
                flash(
                    "The ledger failed its integrity check, so new records cannot be added. "
                    "Run Verify Chain to see the problem (or Reset demo on the Demo Tools page).",
                    "error",
                )
                return redirect(url_for("verify_chain"))
            block = ledger.add_block(
                conn, db.ANCHOR_PATH,
                form["roll_no"], form["student_name"], form["subject"], marks, semester,
            )
            flash(f"Record added as block #{block['id']}. Hash: {block['hash'][:16]}…", "success")
            return redirect(url_for("record_detail", record_id=block["id"]))
        for e in errors:
            flash(e, "error")
    return render_template("add_record.html", form=form)


@app.route("/ledger")
@login_required
def ledger_view():
    # Verify silently (not logged) so every row/card can show its live status.
    result = ledger.verify_chain(db.get_db(), db.ANCHOR_PATH)
    view = "chain" if request.args.get("view") == "chain" else "table"
    # Table: newest first (like a block explorer). Chain: genesis first, so the
    # arrows read top-to-bottom.
    ordered = result["blocks"] if view == "chain" else list(reversed(result["blocks"]))
    blocks, page, pages = paginate(ordered, page_arg())
    return render_template(
        "ledger.html", result=result, blocks=blocks, view=view, page=page, pages=pages,
    )


def _block_page(record_id, public, verdict=None, result=None):
    """Shared by the admin block page and the public verification page."""
    conn = db.get_db()
    if result is None:
        result = ledger.verify_chain(conn, db.ANCHOR_PATH)
    block = ledger.find_block(result, record_id)
    lowest = ledger.GENESIS_ID if public else 0
    prev_id = conn.execute(
        "SELECT MAX(id) FROM records WHERE id < ? AND id > ?", (record_id, lowest)
    ).fetchone()[0]
    next_id = conn.execute("SELECT MIN(id) FROM records WHERE id > ?", (record_id,)).fetchone()[0]
    return render_template(
        "block.html",
        block=block,
        result=result,
        public=public,
        verdict=verdict,
        record_id=record_id,
        prev_id=prev_id,
        next_id=next_id,
        next_block=ledger.find_block(result, next_id) if next_id else None,
        hash_input=ledger.build_hash_input(block["record"]) if block else None,
        public_url=public_url_for(record_id),
    )


@app.route("/ledger/<int:record_id>")
@login_required
def record_detail(record_id):
    conn = db.get_db()
    if not conn.execute("SELECT 1 FROM records WHERE id = ?", (record_id,)).fetchone():
        abort(404)
    return _block_page(record_id, public=False)


@app.route("/verify-chain", methods=["GET", "POST"])
@login_required
def verify_chain():
    result = None
    if request.method == "POST":
        result = run_verification("Verify Chain")
    return render_template("verify_chain.html", result=result)


@app.route("/audit")
@login_required
def audit():
    conn = db.get_db()
    total = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    pages = max(1, math.ceil(total / PER_PAGE))
    page = min(max(1, page_arg()), pages)
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?",
        (PER_PAGE, (page - 1) * PER_PAGE),
    ).fetchall()
    passed = conn.execute("SELECT COUNT(*) FROM audit_log WHERE result = 'VALID'").fetchone()[0]
    return render_template(
        "audit.html", rows=rows, total=total, passed=passed, page=page, pages=pages,
    )


# ---------------------------------------------------------------------------
# Demo tools (admin only, for demonstration)
# ---------------------------------------------------------------------------
@app.route("/demo")
@login_required
def demo_tools():
    return render_template("demo.html", records=student_records(db.get_db()))


def _demo_inputs(need_marks=True):
    try:
        record_id = int(request.form.get("record_id", ""))
        marks = int(request.form.get("new_marks", "")) if need_marks else None
        if need_marks and not 0 <= marks <= 100:
            raise ValueError
    except ValueError:
        raise ValueError("Choose a record and enter marks between 0 and 100.")
    return record_id, marks


@app.route("/demo/tamper", methods=["POST"])
@login_required
def demo_tamper():
    try:
        record_id, marks = _demo_inputs()
        old = demo.tamper_marks(db.get_db(), record_id, marks)
        flash(f"DEMO: record #{record_id} marks changed {old} → {marks} directly in the database "
              "(hash NOT updated). Now run Verify Chain.", "warning")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("demo_tools"))


@app.route("/demo/rebuild", methods=["POST"])
@login_required
def demo_rebuild():
    try:
        record_id, marks = _demo_inputs()
        old = demo.hacker_rebuild(db.get_db(), record_id, marks)
        flash(f"DEMO: record #{record_id} marks changed {old} → {marks} and EVERY hash recomputed. "
              "Every block will look valid — but the anchor will not match. Now run Verify Chain.", "warning")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("demo_tools"))


@app.route("/demo/delete", methods=["POST"])
@login_required
def demo_delete():
    try:
        record_id, _ = _demo_inputs(need_marks=False)
        row = demo.delete_record(db.get_db(), record_id)
        flash(f"DEMO: record #{record_id} ({row['student_name']}) deleted directly from the database. "
              "Now run Verify Chain.", "warning")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("demo_tools"))


@app.route("/demo/reset", methods=["POST"])
@login_required
def demo_reset():
    demo.reset_demo(db.get_db())
    flash(f"Demo reset: fresh genesis block + {len(init_db.SAMPLE_RECORDS)} sample records, "
          "new anchor, audit log cleared.", "success")
    return redirect(url_for("demo_tools"))


# ---------------------------------------------------------------------------
# Public verification (no login) + QR codes
# ---------------------------------------------------------------------------
def _public_result(record_id):
    result = run_verification(f"Public check #{record_id}")
    block = ledger.find_block(result, record_id)
    if block is None or block["is_genesis"]:
        verdict = "NOT_FOUND"
    elif block["status"] != ledger.STATUS_VALID:
        verdict = "NOT_GENUINE"
    elif not result["ok"]:
        verdict = "WARNING"      # this record is intact, but the ledger has problems elsewhere
    else:
        verdict = "GENUINE"
    return result, block, verdict


@app.route("/verify")
def public_verify():
    """Public explorer home: hero search, chain stats, latest blocks."""
    record_id = request.args.get("record_id", "").strip().lstrip("#")
    if record_id:
        return redirect(url_for("search", q=record_id))
    conn = db.get_db()
    latest_blocks = conn.execute(
        "SELECT * FROM records WHERE id != ? ORDER BY id DESC LIMIT 6", (ledger.GENESIS_ID,)
    ).fetchall()
    return render_template("home.html", stats=explorer_stats(conn), latest_blocks=latest_blocks)


@app.route("/verify/<int:record_id>")
def public_verify_record(record_id):
    result, block, verdict = _public_result(record_id)
    return _block_page(record_id, public=True, verdict=verdict, result=result)


HEX = set("0123456789abcdef")


@app.route("/search")
def search():
    """One search box for everything, like a block explorer.

    Everyone:  record ID ("4" or "#4") or a block hash (full or first 6+ chars)
    Admins:    also roll number or student name
    """
    q = request.args.get("q", "").strip()
    back = url_for("index")   # never redirect to a Referer from another site
    if not q:
        return redirect(back)
    conn = db.get_db()
    term = q.lstrip("#")

    # A short number is a record ID. (A long run of digits could also be the
    # start of a hash, so it falls through to the hash search below.)
    if term.isdigit() and len(term) < 6:
        rid = int(term)
        found = conn.execute("SELECT 1 FROM records WHERE id = ?", (rid,)).fetchone()
        if found and block_url(rid):
            return redirect(block_url(rid))
        if not g.user:     # let the public page explain "not found"
            return redirect(url_for("public_verify_record", record_id=rid))

    matches = []
    lowered = term.lower()
    if len(lowered) >= 6 and set(lowered) <= HEX:
        matches = conn.execute(
            "SELECT * FROM records WHERE hash LIKE ? ORDER BY id", (lowered + "%",)
        ).fetchall()
        if not g.user:
            matches = [m for m in matches if m["id"] != ledger.GENESIS_ID]
    elif g.user:
        like = f"%{term}%"
        matches = conn.execute(
            "SELECT * FROM records WHERE id != ? AND (roll_no LIKE ? OR student_name LIKE ? OR subject LIKE ?) "
            "ORDER BY id LIMIT 100",
            (ledger.GENESIS_ID, like, like, like),
        ).fetchall()
    else:
        flash("Search by record ID (e.g. 4) or block hash (at least the first 6 characters).", "info")
        return redirect(back)

    if len(matches) == 1:
        return redirect(block_url(matches[0]["id"]))
    return render_template("search.html", q=q, matches=matches)


@app.route("/qr/<int:record_id>.png")
def qr_code(record_id):
    exists = db.get_db().execute(
        "SELECT 1 FROM records WHERE id = ? AND id != ?", (record_id, ledger.GENESIS_ID)
    ).fetchone()
    if not exists:
        abort(404)
    img = qrcode.make(public_url_for(record_id), box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png", download_name=f"record-{record_id}-qr.png")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
@app.errorhandler(404)
def not_found(_e):
    return render_template("error.html", code=404, message="That page or record does not exist."), 404


@app.errorhandler(400)
def bad_request(e):
    return render_template("error.html", code=400, message=e.description), 400


if __name__ == "__main__":
    # Debug mode ONLY when started with "python app.py" on your own computer.
    # On EC2, gunicorn imports `app` directly, so this block never runs there.
    # (Port 5050 because macOS uses 5000 for AirPlay.)
    port = int(os.environ.get("PORT", 5050))
    app.run(host="127.0.0.1", port=port, debug=True)
