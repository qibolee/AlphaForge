#!/usr/bin/env bash
# 首次部署的环境准备：缺 Docker 才装，幂等可重复执行。
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
need_root ./start.sh

export DEBIAN_FRONTEND=noninteractive

if ! have docker; then
  log "安装 Docker..."
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker >/dev/null 2>&1 || true

if ! docker compose version >/dev/null 2>&1; then
  log "安装 docker compose 插件..."
  apt-get update -y >/dev/null 2>&1 || true
  apt-get install -y docker-compose-plugin >/dev/null 2>&1 || true
fi

docker compose version >/dev/null 2>&1 || die "docker compose 不可用，请手动安装后重试。"
log "Docker 环境就绪。"
