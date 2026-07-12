#!/usr/bin/env bash
# Install SysVitals natively on Ubuntu/WSL (no Docker required).
# Run from the repository root after enabling systemd in WSL.
set -euo pipefail

readonly APP_USER="sysvitals"
readonly APP_DIR="/opt/sysvitals"
readonly DATA_DIR="/var/lib/sysvitals"
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

if [[ ! -f "${REPO_DIR}/backend/main.py" || ! -d "${REPO_DIR}/frontend" ]]; then
  echo "Run this script from a complete SysVitals checkout." >&2
  exit 1
fi

if [[ ! -d /run/systemd/system ]]; then
  echo "systemd is not running in this WSL distribution." >&2
  echo "Enable it in /etc/wsl.conf, run 'wsl --shutdown' from Windows, then reopen Ubuntu." >&2
  exit 1
fi

sudo apt-get update
sudo apt-get install -y caddy python3 python3-venv rsync

if ! id -u "${APP_USER}" >/dev/null 2>&1; then
  sudo useradd --system --home-dir "${APP_DIR}" --create-home --shell /usr/sbin/nologin "${APP_USER}"
fi

sudo install -d -o "${APP_USER}" -g "${APP_USER}" -m 0755 "${APP_DIR}"
sudo install -d -o "${APP_USER}" -g "${APP_USER}" -m 0750 "${DATA_DIR}"
sudo rsync -a --delete \
  --exclude '.git' --exclude '.env' --exclude '.venv' --exclude '__pycache__' --exclude 'data' \
  "${REPO_DIR}/" "${APP_DIR}/"
sudo chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
sudo chmod -R a+rX "${APP_DIR}/frontend"

sudo -u "${APP_USER}" python3 -m venv "${APP_DIR}/.venv"
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install --upgrade pip
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/backend/requirements.txt"

sudo install -m 0644 "${REPO_DIR}/deploy/sysvitals-backend.service" /etc/systemd/system/sysvitals-backend.service
sudo install -m 0644 "${REPO_DIR}/deploy/Caddyfile" /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl daemon-reload
sudo systemctl enable --now sysvitals-backend caddy

echo
echo "SysVitals is running at http://127.0.0.1:8080 for Cloudflare Tunnel."
echo "Configure the tunnel hostname to point to that URL, then verify:"
echo "  curl -fsS https://sysvitals.mukhildharan.dev/health"
