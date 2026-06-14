#!/usr/bin/env bash
# ============================================================
#  AlphaForge 一键部署入口
#  用法（在 AWS 服务器上）:
#     git clone <你的GitHub地址> AlphaForge && cd AlphaForge && sudo ./start.sh
#  首次运行会创建 config/env，填好账号后再次运行即可拉起服务。
# ============================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/scripts/lib.sh"
need_root ./start.sh

info "================= AlphaForge 一键部署 ================="

# 1) 环境准备（缺 Docker 才装）
"$HERE/scripts/bootstrap.sh"

mkdir -p "$HERE/state" "$HERE/logs"

# 2) 首次创建 config/env，提示填写后退出
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$HERE/config/env.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  warn "已创建 config/env，请填写 IB 账号信息后重新运行 sudo ./start.sh："
  echo "    vim $ENV_FILE"
  exit 0
fi

# 3) 校验关键字段已填写
load_env
for k in IB_USERNAME IB_PASSWORD IB_ACCOUNT; do
  [[ -n "${!k:-}" ]] || die "config/env 中 $k 为空，请填写后重试：vim $ENV_FILE"
done

# 4) 首次从模板创建 grid.yaml（之后由引擎读写，不再覆盖）
if [[ ! -f "$HERE/config/grid.yaml" ]]; then
  cp "$HERE/config/grid.example.yaml" "$HERE/config/grid.yaml"
  log "已从模板创建 config/grid.yaml（用 ./afctl edit 修改参数）。"
fi

# 5) 构建并启动
log "构建引擎镜像并启动容器..."
compose build
compose up -d

sleep 5
echo; compose ps; echo

# 6) 校验容器确实在运行（配置错会崩溃，避免假成功）
ok=1
for c in ib-gateway alphaforge-engine alphaforge-watchdog; do
  st="$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null || echo missing)"
  if [[ "$st" != "running" ]]; then
    warn "$c 状态异常（$st），最近日志："
    docker logs --tail 30 "$c" 2>&1 | sed 's/^/    /' || true
    ok=0
  fi
done
[[ "$ok" == "1" ]] || die "有容器未正常运行，请按上面日志排查后重跑 sudo ./start.sh"

log "部署完成！"
cat <<'EOF'

──────────────── 日常命令 ────────────────
  ./afctl status              服务/容器/端口/网格 一屏看全
  ./afctl logs trade          交易事件日志（连接/触发/成交/撤单/风控）
  ./afctl logs engine         引擎运行日志（异常堆栈）
  ./afctl logs gateway        IB Gateway 登录/会话日志
  ./afctl grid                查看网格参数与状态
  sudo ./afctl edit           安全修改 grid.yaml（自动 停→备份→改→启）
  sudo ./afctl kill on|off    紧急停止/恢复下单
  sudo ./afctl update         更新代码 + 验收测试 + 重启
────────────────────────────────────────
  • 确认 AWS 安全组按需放行；IB Gateway 用 config/env 的账号自动登录。
  • 若交易连不上，先看 ./afctl logs gateway 确认已登录成功。
EOF
