#!/usr/bin/env bash
# 公共函数库：日志、环境加载、docker compose 封装。被 afctl / start.sh / bootstrap.sh 共用。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/config/env"
COMPOSE_FILE="$ROOT_DIR/docker-compose.yml"

c_reset='\033[0m'; c_green='\033[0;32m'; c_yellow='\033[0;33m'; c_red='\033[0;31m'; c_blue='\033[0;36m'
log()  { echo -e "${c_green}[+]${c_reset} $*"; }
info() { echo -e "${c_blue}[i]${c_reset} $*"; }
warn() { echo -e "${c_yellow}[!]${c_reset} $*" >&2; }
die()  { echo -e "${c_red}[x]${c_reset} $*" >&2; exit 1; }

have()      { command -v "$1" >/dev/null 2>&1; }
need_root() { [[ "$(id -u)" -eq 0 ]] || die "请用 root 运行：sudo $*"; }

# 加载 config/env 并导出（供 ib-gateway 变量插值 / 字段校验使用）。
load_env() {
  [[ -f "$ENV_FILE" ]] || die "config/env 不存在，请先运行 sudo ./start.sh"
  set -a; . "$ENV_FILE"; set +a
}

# docker compose 封装：固定项目目录与 compose 文件；env 存在时带 --env-file 供变量插值。
compose() {
  local args=(-f "$COMPOSE_FILE")
  [[ -f "$ENV_FILE" ]] && args+=(--env-file "$ENV_FILE")
  ( cd "$ROOT_DIR" && docker compose "${args[@]}" "$@" )
}
