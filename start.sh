#!/usr/bin/env bash
set -euo pipefail

APP_NAME="alphaforge"
APP_DIR="/opt/alphaforge/app"
VENV_DIR="/opt/alphaforge/venv"
ETC_DIR="/etc/alphaforge"
SERVICE_NAME="${APP_NAME}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
ALPHAFORGE_BIN="${VENV_DIR}/bin/alphaforge"
RUN_USER="${APP_NAME}"
RUN_GROUP="${APP_NAME}"
DOCTOR_RETRIES=12
DOCTOR_SLEEP_SECONDS=5

step() {
  printf "\n[start] [%s] %s\n" "$1" "$2"
}

fail() {
  printf "[error] %s\n" "$1" >&2
  exit 1
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    fail "start.sh must run with sudo"
  fi
}

check_install_outputs() {
  id "${RUN_USER}" >/dev/null 2>&1 || fail "missing system user ${RUN_USER}; run install.sh first"
  [[ -f "${ETC_DIR}/env" ]] || fail "missing ${ETC_DIR}/env; fill in account settings"
  [[ -f "${ETC_DIR}/config.yaml" ]] || fail "missing ${ETC_DIR}/config.yaml; run install.sh first"
  [[ -f "${ETC_DIR}/docker-compose.yml" ]] || fail "missing ${ETC_DIR}/docker-compose.yml"
  [[ -f "${SERVICE_FILE}" ]] || fail "missing ${SERVICE_FILE}; run install.sh first"
  [[ -x "${ALPHAFORGE_BIN}" ]] || fail "missing ${ALPHAFORGE_BIN}; run install.sh first"
}

start_ib_gateway() {
  # Start only the IB Gateway container; AlphaForge connects through host ports 4001/4002.
  docker compose --env-file "${ETC_DIR}/env" -f "${ETC_DIR}/docker-compose.yml" up -d
}

run_health_check() {
  # start.sh runs as root for Docker/systemd.
  # doctor runs as alphaforge to match the trading service permissions.
  # This catches config/log/state permission issues before systemd starts run.
  local attempt

  for attempt in $(seq 1 "${DOCTOR_RETRIES}"); do
    if runuser -u "${RUN_USER}" -g "${RUN_GROUP}" -- "${ALPHAFORGE_BIN}" doctor; then
      return
    fi

    if [[ "${attempt}" -lt "${DOCTOR_RETRIES}" ]]; then
      printf "[start] doctor failed; retrying in %s seconds (%s/%s)\n" \
        "${DOCTOR_SLEEP_SECONDS}" "${attempt}" "${DOCTOR_RETRIES}"
      sleep "${DOCTOR_SLEEP_SECONDS}"
    fi
  done

  fail "alphaforge doctor failed after ${DOCTOR_RETRIES} attempts"
}

start_systemd_service() {
  # The long-running trading loop is owned by systemd, not by this shell script.
  # The service ExecStart runs: /opt/alphaforge/venv/bin/alphaforge run.
  systemctl enable "${SERVICE_NAME}"
  systemctl restart "${SERVICE_NAME}"
}

print_summary() {
  cat <<EOF

AlphaForge started.

Logs:
  sudo journalctl -u ${SERVICE_NAME} -f
  sudo tail -f /var/log/alphaforge/audit.jsonl
  sudo tail -f /var/log/alphaforge/service.log

Production paths:
  app:  ${APP_DIR}
  venv: ${VENV_DIR}
  conf: ${ETC_DIR}
EOF
}

main() {
  require_root

  step "1/4" "Check install outputs"
  check_install_outputs

  step "2/4" "Start IB Gateway container"
  start_ib_gateway

  step "3/4" "Run AlphaForge health check"
  run_health_check

  step "4/4" "Start AlphaForge systemd service"
  start_systemd_service

  print_summary
}

main "$@"
