#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "start.sh must run with sudo" >&2
  exit 1
fi

APP_DIR="/opt/alphaforge/app"
VENV_DIR="/opt/alphaforge/venv"
ETC_DIR="/etc/alphaforge"

if [[ ! -f "${ETC_DIR}/env" ]]; then
  echo "missing ${ETC_DIR}/env; run deploy.sh first" >&2
  exit 1
fi

if [[ ! -x "${VENV_DIR}/bin/alphaforge" ]]; then
  echo "missing ${VENV_DIR}/bin/alphaforge; run deploy.sh first" >&2
  exit 1
fi

echo "[start] starting IB Gateway container"
docker compose --env-file "${ETC_DIR}/env" -f "${ETC_DIR}/docker-compose.yml" up -d

echo "[start] running health check"
"${VENV_DIR}/bin/alphaforge" doctor

echo "[start] starting AlphaForge service"
systemctl enable alphaforge
systemctl restart alphaforge

cat <<EOF

AlphaForge started.

Logs:
  sudo journalctl -u alphaforge -f
  tail -f /var/log/alphaforge/audit.jsonl
  tail -f /var/log/alphaforge/service.log

Production app:
  ${APP_DIR}
EOF

