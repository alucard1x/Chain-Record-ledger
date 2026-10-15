#!/bin/bash
# =============================================================================
# ChainLedger - local setup for macOS (also works on Linux)
#
#   bash setup_mac.sh
#
# 1. Finds a Python 3.9+ on this computer
# 2. Creates ./venv (or repairs a broken one)
# 3. Installs everything in requirements.txt
# 4. Checks every package imports correctly
# 5. Creates the database (admin user, genesis block, 8 sample records)
# Safe to run again at any time.
# =============================================================================
set -e
cd "$(dirname "$0")"

echo "==> [1/5] Looking for Python 3.9 or newer"
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3 python3.9 \
            /opt/homebrew/bin/python3 /usr/local/bin/python3 "$HOME/.homebrew/bin/python3" \
            /Library/Frameworks/Python.framework/Versions/Current/bin/python3; do
  if command -v "$cand" >/dev/null 2>&1 && \
     "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    PY="$(command -v "$cand")"
    break
  fi
done

if [ -z "$PY" ]; then
  echo
  echo "  No Python 3.9+ found. Install one of these, then run this script again:"
  echo "    * Easiest: download the macOS installer from https://www.python.org/downloads/"
  echo "    * Or run:  xcode-select --install   (installs Apple's Python 3.9)"
  exit 1
fi
echo "    Using $PY ($("$PY" --version 2>&1))"

echo "==> [2/5] Virtual environment"
if [ -d venv ] && ! venv/bin/python -c "import sys" >/dev/null 2>&1; then
  echo "    Existing venv is broken - recreating it"
  rm -rf venv
fi
if [ ! -d venv ]; then
  "$PY" -m venv venv
  echo "    Created ./venv"
else
  echo "    ./venv already exists ($(venv/bin/python --version 2>&1))"
fi

echo "==> [3/5] Installing requirements (this can take a minute)"
venv/bin/python -m pip install --upgrade pip -q
venv/bin/python -m pip install -r requirements.txt -q

echo "==> [4/5] Checking every package imports"
venv/bin/python - <<'PYCHECK'
from importlib.metadata import version
import flask, werkzeug, qrcode, PIL, gunicorn, sqlite3, hashlib
for pkg in ("Flask", "Werkzeug", "qrcode", "pillow", "gunicorn"):
    print(f"    OK  {pkg:<10} {version(pkg)}")
print(f"    OK  sqlite3    {sqlite3.sqlite_version} (built in)")
qrcode.make("test")   # proves Pillow works for QR images
print("    OK  QR code generation")
PYCHECK

echo "==> [5/5] Initialising the database"
venv/bin/python init_db.py

echo
echo "============================================================"
echo " All dependencies installed. To start ChainLedger:"
echo
echo "   cd \"$(pwd)\""
echo "   source venv/bin/activate"
echo "   python app.py"
echo
echo " Then open http://127.0.0.1:5050   (login: admin / admin123)"
echo "============================================================"
