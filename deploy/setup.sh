#!/usr/bin/env bash
# =============================================================================
# ChainLedger - one-shot setup for a fresh Ubuntu EC2 instance
#
# Usage (as the normal "ubuntu" user, NOT with sudo):
#     cd ~/chainledger
#     bash deploy/setup.sh
#
# What it does:
#   1. Installs python3-venv, pip, nginx, git and sqlite3 with apt
#   2. Creates a Python virtual environment in ./venv and installs requirements
#   3. Generates a random SECRET_KEY in /etc/chainledger.env
#   4. Runs init_db.py (tables, admin user, genesis block, 8 sample records)
#   5. Installs and starts the systemd service (gunicorn on 127.0.0.1:8000)
#   6. Installs the nginx site (port 80 -> gunicorn) and restarts nginx
# Safe to run again after you upload new code: existing data is kept.
# =============================================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="$(whoami)"
VENV="$APP_DIR/venv"

if [ "$APP_USER" = "root" ]; then
  echo "Please run this as the normal user (e.g. ubuntu), without sudo:  bash deploy/setup.sh"
  exit 1
fi

echo "==> ChainLedger folder: $APP_DIR (user: $APP_USER)"

echo "==> [1/6] Installing system packages (python3-venv, nginx, git, sqlite3)"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv python3-pip nginx git sqlite3 curl

echo "==> [2/6] Creating virtual environment and installing requirements"
# A venv copied from Windows/macOS will not work on Linux - remove it if broken.
if [ -d "$VENV" ] && ! "$VENV/bin/python" -c "import sys" >/dev/null 2>&1; then
  echo "    Removing an unusable venv (probably copied from your laptop)"
  rm -rf "$VENV"
fi
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install -r "$APP_DIR/requirements.txt" -q

echo "==> [3/6] Creating SECRET_KEY (only the first time)"
if [ ! -f /etc/chainledger.env ]; then
  SECRET=$("$VENV/bin/python" -c "import secrets; print(secrets.token_hex(32))")
  echo "SECRET_KEY=$SECRET" | sudo tee /etc/chainledger.env >/dev/null
  sudo chmod 600 /etc/chainledger.env
  echo "    Written to /etc/chainledger.env"
else
  echo "    /etc/chainledger.env already exists - keeping it"
fi

echo "==> [4/6] Initialising the database"
mkdir -p "$APP_DIR/instance"
(cd "$APP_DIR" && "$VENV/bin/python" init_db.py)

echo "==> [5/6] Installing systemd service"
sed -e "s#__APP_DIR__#$APP_DIR#g" -e "s#__APP_USER__#$APP_USER#g" \
    "$APP_DIR/deploy/chainledger.service" | sudo tee /etc/systemd/system/chainledger.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable chainledger >/dev/null
sudo systemctl restart chainledger

echo "==> [6/6] Configuring nginx"
sudo cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/chainledger
sudo ln -sf /etc/nginx/sites-available/chainledger /etc/nginx/sites-enabled/chainledger
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl enable nginx >/dev/null
sudo systemctl restart nginx

# --- Health check -------------------------------------------------------------
sleep 2
STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1/verify || true)

# Ask the EC2 metadata service (IMDSv2) for this instance's public IP.
TOKEN=$(curl -sf -m 2 -X PUT "http://169.254.169.254/latest/api/token" \
        -H "X-aws-ec2-metadata-token-ttl-seconds: 60" || true)
PUBLIC_IP=$(curl -sf -m 2 -H "X-aws-ec2-metadata-token: $TOKEN" \
        http://169.254.169.254/latest/meta-data/public-ipv4 || true)
if ! [[ "$PUBLIC_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  PUBLIC_IP="<your-EC2-public-IP>"
fi

echo
echo "============================================================"
if [ "$STATUS" = "200" ]; then
  echo " ChainLedger is running!"
else
  echo " Setup finished but the health check returned HTTP $STATUS."
  echo " Check logs with:  sudo journalctl -u chainledger -n 50"
fi
echo
echo "   Open:   http://$PUBLIC_IP"
echo "   Login:  admin / admin123   (change it after first login!)"
echo
echo " Useful commands:"
echo "   sudo systemctl status chainledger     # is the app running?"
echo "   sudo journalctl -u chainledger -f     # live app logs"
echo "   sudo systemctl restart chainledger    # after changing code"
echo "============================================================"
