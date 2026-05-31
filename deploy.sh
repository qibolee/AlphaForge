#!/usr/bin/env bash
set -euo pipefail
umask 027

APP_NAME="alphaforge"
APP_ROOT="/opt/alphaforge"
APP_DIR="${APP_ROOT}/app"
VENV_DIR="${APP_ROOT}/venv"
ETC_DIR="/etc/alphaforge"
LOG_DIR="/var/log/alphaforge"
STATE_DIR="/var/lib/alphaforge"
SERVICE_FILE="/etc/systemd/system/alphaforge.service"

SOURCE_DIR=""
DEPLOY_DIR=""

step() {
  printf "\n[deploy] [%s] %s\n" "$1" "$2"
}

info() {
  printf "[info] %s\n" "$1"
}

fail() {
  printf "[error] %s\n" "$1" >&2
  exit 1
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    fail "deploy.sh must run with sudo"
  fi
}

detect_source_dir() {
  SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  DEPLOY_DIR="${SOURCE_DIR}/deploy"
  info "source directory: ${SOURCE_DIR}"
  info "deploy templates:  ${DEPLOY_DIR}"
  info "production app:   ${APP_DIR}"
}

validate_source_workspace() {
  [[ -d "${DEPLOY_DIR}" ]] \
    || fail "deploy.sh must run from the source workspace, e.g. /home/ubuntu/AlphaForge; deploy/ is missing"
  [[ -d "${SOURCE_DIR}/src" ]] \
    || fail "deploy.sh must run from the source workspace, e.g. /home/ubuntu/AlphaForge; src/ is missing"
  [[ -f "${SOURCE_DIR}/pyproject.toml" ]] \
    || fail "deploy.sh must run from the source workspace, e.g. /home/ubuntu/AlphaForge; pyproject.toml is missing"
}

require_file() {
  local path="$1"
  [[ -f "${path}" ]] || fail "required file is missing: ${path}"
}

copy_once() {
  local src="$1"
  local dst="$2"
  local mode="$3"
  local owner="$4"
  local group="$5"

  require_file "${src}"
  if [[ -f "${dst}" ]]; then
    printf "[keep] %s exists; not overwritten\n" "${dst}"
    return
  fi

  install -m "${mode}" -o "${owner}" -g "${group}" "${src}" "${dst}"
  printf "[create] %s\n" "${dst}"
}

copy_always() {
  local src="$1"
  local dst="$2"
  local mode="$3"
  local owner="$4"
  local group="$5"

  require_file "${src}"
  install -m "${mode}" -o "${owner}" -g "${group}" "${src}" "${dst}"
  printf "[update] %s\n" "${dst}"
}

install_packages() {
  apt update
  apt install -y git curl ca-certificates python3 python3-venv python3-pip docker.io rsync

  if docker compose version >/dev/null 2>&1; then
    info "docker compose is already available"
  else
    info "installing Docker Compose plugin"
    apt install -y docker-compose-plugin || apt install -y docker-compose-v2
  fi
}

ensure_user_and_dirs() {
  if id "${APP_NAME}" >/dev/null 2>&1; then
    info "system user ${APP_NAME} already exists"
  else
    useradd --system --home-dir "${STATE_DIR}" --shell /usr/sbin/nologin "${APP_NAME}"
    info "created system user ${APP_NAME}"
  fi

  install -d -m 0755 "${APP_DIR}" "${VENV_DIR}" "${ETC_DIR}" "${LOG_DIR}" "${STATE_DIR}"
  chown -R "${APP_NAME}:${APP_NAME}" "${LOG_DIR}" "${STATE_DIR}"
  info "created production directories"
}

sync_app() {
  local source_real
  local app_real

  source_real="$(realpath "${SOURCE_DIR}")"
  app_real="$(realpath -m "${APP_DIR}")"

  if [[ "${source_real}" == "${app_real}" ]]; then
    info "source is already ${APP_DIR}; skipping rsync"
    return
  fi

  rsync -a --delete --delete-excluded \
    --exclude .git \
    --exclude .gitignore \
    --exclude .venv \
    --exclude __pycache__ \
    --exclude .pytest_cache \
    --exclude .ruff_cache \
    --exclude deploy \
    --exclude tests \
    --exclude deploy.sh \
    "${SOURCE_DIR}/" \
    "${APP_DIR}/"

  info "synced ${SOURCE_DIR} -> ${APP_DIR}"
}

install_python_env() {
  python3 -m venv "${VENV_DIR}"
  "${VENV_DIR}/bin/pip" install --upgrade pip
  "${VENV_DIR}/bin/pip" install -e "${APP_DIR}"
}

install_configs() {
  copy_once \
    "${DEPLOY_DIR}/etc/alphaforge/env" \
    "${ETC_DIR}/env" \
    "0640" \
    "root" \
    "${APP_NAME}"

  copy_once \
    "${DEPLOY_DIR}/etc/alphaforge/config.yaml" \
    "${ETC_DIR}/config.yaml" \
    "0640" \
    "root" \
    "${APP_NAME}"

  copy_always \
    "${DEPLOY_DIR}/etc/alphaforge/docker-compose.yml" \
    "${ETC_DIR}/docker-compose.yml" \
    "0644" \
    "root" \
    "root"
}

install_service() {
  copy_always \
    "${DEPLOY_DIR}/etc/systemd/system/alphaforge.service" \
    "${SERVICE_FILE}" \
    "0644" \
    "root" \
    "root"

  chmod +x "${APP_DIR}/start.sh"
}

reload_systemd() {
  systemctl daemon-reload
}

print_summary() {
  cat <<EOF

Deploy complete.

Protected files, not overwritten on future deploys:
  ${ETC_DIR}/env
  ${ETC_DIR}/config.yaml

Updated on every deploy:
  ${ETC_DIR}/docker-compose.yml
  ${SERVICE_FILE}
  ${APP_DIR}
  ${VENV_DIR}

Next steps:
  sudo nano ${ETC_DIR}/env
  sudo ${APP_DIR}/start.sh

Production paths:
  app:   ${APP_DIR}
  venv:  ${VENV_DIR}
  conf:  ${ETC_DIR}
  logs:  ${LOG_DIR}
  state: ${STATE_DIR}
EOF
}

main() {
  require_root
  detect_source_dir
  validate_source_workspace

  step "1/7" "Install system packages"
  install_packages

  step "2/7" "Create system user and directories"
  ensure_user_and_dirs

  step "3/7" "Sync application code"
  sync_app

  step "4/7" "Create/update Python virtualenv"
  install_python_env

  step "5/7" "Install configuration files"
  install_configs

  step "6/7" "Install systemd service"
  install_service

  step "7/7" "Reload systemd"
  reload_systemd

  print_summary
}

main "$@"
