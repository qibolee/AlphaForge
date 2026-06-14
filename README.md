# AlphaForge — AWS · IBKR 网格交易引擎

一套自托管、单命令运维的网格交易程序。跑在你自己的 AWS us-east-1 实例上：**IB Gateway** 与 **交易引擎** 都以容器形式由 `docker compose` 管理，整个项目自包含在一个目录里——配置、状态、日志全在眼皮底下，一个 `afctl` 收敛所有日常操作。

- **项目即运行时**：`git clone` 出来的目录就是运行目录，不再 copy 到 `/etc`、`/opt`、`/var`。
- **全容器化**：引擎打成镜像，和 IB Gateway 一起 `docker compose` 管理；崩溃自动重启、开机自启，无人值守。
- **单一入口**：`./afctl status|logs|grid|edit|kill|update`，不用再记 systemctl / journalctl / docker / `/var/log` 一堆路径。
- **更新有安全网**：`./afctl update` 会先跑验收单测，测试不过就**不重启**，避免改坏已跑通的交易链路。

> ⚠️ 交易有风险。默认 `paper`（模拟盘）。切到 `live` 需显式 `LIVE_TRADING_ENABLED=true`，请自行承担实盘风险。

---

## 目录
1. [架构](#架构)
2. [部署：三步上线](#部署三步上线)
3. [日常管理（afctl）](#日常管理afctl)
4. [配置说明](#配置说明)
5. [grid_v1 策略](#grid_v1-策略)
6. [日志与状态观测](#日志与状态观测)
7. [更新与回滚](#更新与回滚)
8. [故障排查](#故障排查)
9. [本地测试](#本地测试)
10. [目录结构](#目录结构)

---

## 架构

```
        本仓库目录 (git clone = 运行时)
        │
        ├── docker compose
        │     ├── ib-gateway   ── 用 config/env 账号自动登录，发布 127.0.0.1:4001/4002
        │     ├── engine       ── 本仓库代码镜像；network_mode: host，连 127.0.0.1:4002
        │     │                    挂载 ./config ./state ./logs 到容器 /app 下
        │     └── watchdog     ── 盯 state/heartbeat.json，引擎异常时推微信告警
        │
        └── afctl              ── 唯一控制入口（status / logs / grid / edit / kill / update）
```

- **职责分离**：`ib-gateway` 是「管道」——用 `config/env` 账号经 IBC 自动登录、维持到 IBKR 的连接；`engine` 是「大脑」——跑网格策略、下单撤单。引擎**从不直连 IBKR**，行情与订单全经 gateway 转发（`engine → 127.0.0.1:4002 → gateway → IBKR`）。因此重启引擎不会断开券商登录，`afctl update` 也只重建 engine 镜像。
- **引擎用 host 网络**，连接 `127.0.0.1:4002`（paper）/ `4001`（live），与重构前的 host venv 行为完全一致。
- **凭证不进镜像**：`config/env` 通过卷挂载进容器，`.dockerignore` 排除 `config/`。
- **自愈**：两个容器 `restart: always`；引擎自身还有断线指数退避重连（30s→300s）。Docker 开机自启即全栈自启。
- **可观测**：引擎每 ~15s 写一次心跳 `state/heartbeat.json`（计时驱动，行情清淡也不会过期）；`docker compose` 据此对引擎做 `healthcheck`（`alphaforge healthz`），`compose ps` 显示 healthy/unhealthy，`./afctl status` 显示心跳新鲜度与会话阶段。
- **主动告警**（可选）：填 `config/env` 的 `SERVERCHAN_SENDKEY`（Server酱→微信）即启用。引擎内对关键事件（崩溃 / 拒单熔断 / 热加载失败）实时推送；独立 `watchdog` 容器盯心跳——**引擎进程死了也能告警**（死进程无法自报）。

---

## 部署：三步上线

在 AWS 实例上（Ubuntu，root / sudo）：

```bash
# 1) 拉代码
git clone <你的GitHub地址> AlphaForge && cd AlphaForge

# 2) 一键部署（首次会自动装 Docker，并创建 config/env 后停下）
sudo ./start.sh
vim config/env            # 填 IB_USERNAME / IB_PASSWORD / IB_ACCOUNT

# 3) 再跑一次，拉起服务
sudo ./start.sh
```

`start.sh` 是幂等的，可反复执行。完成后用 `./afctl status` 确认两个容器都 `running`、`4001/4002` 在监听。

**为什么要跑两次**：第一次检测到还没有 `config/env`，创建模板后**主动退出**（没凭证无法启动）；填好账号再跑第二次，才会构建镜像、拉起容器、做健康自检。

**`start.sh` vs `afctl up`**：两者核心都是 `docker compose up -d`（建+起）。`start.sh` 是「首次部署」超集——多了装 Docker、建配置模板、校验凭证、启动后自检与命令清单；`afctl up` 是「日常拉起」精简版（假设机器已就绪）。**第二次 `start.sh` 后容器已在运行，无需再 `afctl up`**；且容器 `restart: always`，机器重启会自动拉起，两条命令通常都不用敲。

---

## 日常管理（afctl）

```bash
sudo ./afctl up               构建并启动（gateway + engine + watchdog）
sudo ./afctl stop             停止容器（保留，不删除）
sudo ./afctl restart          重启容器
sudo ./afctl down             停止并移除容器

     ./afctl status           容器/端口/心跳/网格 一屏看全（容器状态那部分需 sudo）
sudo ./afctl logs             引擎运行日志（默认 engine）
sudo ./afctl logs gateway     IB Gateway 登录/会话日志
sudo ./afctl logs watchdog    看门狗日志
     ./afctl logs trade       交易事件日志（连接/触发/成交/撤单/风控，免 sudo）
     ./afctl logs audit       采样后的常规事件（行情/未触发等，免 sudo）
     ./afctl grid             查看策略声明(spec) + 运行态(status)

sudo ./afctl edit             改策略声明 grid.yaml（引擎热加载，无需停服务）
sudo ./afctl resume [标的]    解除引擎对某标的的熔断（下单被拒后自动暂停）
sudo ./afctl kill on|off      紧急停止/恢复下单（撤单与状态仍正常）
     ./afctl kill status      查看 kill switch 状态

sudo ./afctl doctor           引擎自检（配置/路径/IBKR 端口）
sudo ./afctl alert-test       发送一条微信测试告警（验证渠道连通）
sudo ./afctl test             运行验收单测（paper 安全网）
sudo ./afctl update           git pull → 重建 → 跑测试 → 重启
```

> **关于 sudo**：凡是要访问 docker 的命令都需 root——① docker 守护进程默认只允许 root；② `config/env` 是 root 私有(`600`)，compose 读不到。纯读文件的命令（`status` 的心跳/端口/网格部分、`grid`、`logs trade|audit`、`kill status`）可免 sudo。**拿不准就加 sudo**；不加时这些命令会明确提示"请用 root 运行"，不再抛费解的 `permission denied`。

---

## 配置说明

三个文件，职责清晰：

| 文件 | 是否提交 | 内容 | 谁写 |
|---|---|---|---|
| `config/env` | 否（gitignore） | IB 账号密钥、paper/live 模式、Server酱 SendKey（可选告警） | 你 |
| `config/config.yaml` | 是 | IBKR 端口、容器内路径、风控阈值 | 你（很少改） |
| `config/grid.yaml` | 否（gitignore） | **策略声明 spec**：网格参数 | **只有你** |
| `state/grid_state.json` | 否（gitignore） | **运行态 status**：演化后的 base_price、订单、状态 | **只有引擎** |

仓库里只放模板 `config/env.example` 和 `config/grid.example.yaml`，`start.sh` 首次会复制成真文件。因为这些都被 gitignore，`./afctl update` 的 `git pull` **不会**和你的配置/运行态冲突。

**声明与运行态分离**是这次重构的关键：`grid.yaml` 是你的「声明」，引擎**只读、绝不写回**，所以可以**在引擎运行时直接改**——

```bash
sudo ./afctl edit     # 备份 → $EDITOR 打开 → 校验；引擎下一轮(数秒)自动热加载，无需停服务
```

`edit` 会先校验再生效；即使存错了语法，引擎也会继续用上一份有效配置运行（不会崩），并提示你备份位置。引擎的运行态写在 `state/grid_state.json`，不要手改，用 `./afctl grid` 查看。

---

## grid_v1 策略

**声明 vs 运行态**：你在 `config/grid.yaml`（spec）为每个标的声明 `base_price`、涨跌幅、单次交易金额、`paused`；引擎把演化后的 `base_price`、活跃订单、状态写到 `state/grid_state.json`（status）。

`base_price` 有双重身份：你设的是**初始锚点**，引擎成交后会**自适应演化**。引擎记住「上次见到的 spec 锚点」——

- 你**没改** spec 的 `base_price` → 沿用引擎演化后的值；
- 你**改了** spec 的 `base_price`（`afctl edit`）→ 视为**手动重锚**，你的值立即覆盖演化值。

`paused: true` 是你的手动开关。引擎在订单被拒时也会**自动熔断**该标的（记在 status 的 `halted`），用 `sudo ./afctl resume [标的]` 解除。

> **恢复被引擎暂停的标的**：`resume` 直接改写 `state/grid_state.json`。当该标的处于空闲（无订单事件）时立即可靠生效；但若**其它标的正在频繁成交**，引擎可能在下一次存盘时重新写回 `halted` 把它盖掉。要确保生效，请在该标的安静时执行 `resume`，或 `sudo ./afctl restart` 重启引擎（重启必然从 status 重新读取）。

用户可见状态只有两种：

```
WAITING_TRIGGER  没有活跃订单，等待价格触发买入或卖出。
WAITING_TRADE    已触发并提交订单，等待 IBKR 成交、取消或过期事件。
```

触发规则（留 1% 容差先触发再挂单）：

```
卖出触发：latest_price >= base_price * (1 + up_pct * 0.99)
买入触发：latest_price <= base_price * (1 - down_pct * 0.99)
```

挂单用完整网格价：

```
卖出限价：base_price * (1 + up_pct)
买入限价：base_price * (1 - down_pct)
数量：    floor(trade_amount / limit_price)
```

订单用 `GTD`、有效期 7 个自然日、允许盘前盘后成交。挂单等待期间，如果价格回到下单时的 `base_price`，引擎会主动撤单。撤单/成交后 `base_price` 按成交比例自适应：

```
new_base_price = old_base_price + fill_ratio * (limit_price - old_base_price)
```

零成交不变；部分成交向挂单价移动一部分；全部成交更新为挂单价。

---

## 日志与状态观测

重构后日志只在两个地方，不用再记 `/var/log`：

```bash
./afctl logs trade      # logs/trade.jsonl —— 交易关键事件（连接/reconcile/触发/成交/撤单/风控）
./afctl logs audit      # logs/audit.jsonl —— 交易事件 + 采样常规事件（默认 1% 采样）
./afctl logs engine     # 引擎容器 stdout/stderr —— 异常堆栈、底层库提示
./afctl logs gateway    # IB Gateway 容器日志 —— 登录与会话状态
```

健康判断（`./afctl status` 一屏看全）：

```
ib-gateway / alphaforge-engine 都是 running（compose ps 显示 healthy）
引擎心跳 ✓ 活着（距上次心跳 < 60s）—— 容器 running 但心跳过期 = 引擎卡住/重连中
4001/4002 在监听
trade.jsonl 出现 connected、reconcile_completed、portfolio_loaded、quote_stream_starting
audit.jsonl 持续出现 quote_no_trigger / outside_trading_window 等行情事件
grid_state.json 里 state 与 active_order 关系合理（WAITING_TRIGGER↔无单，WAITING_TRADE↔有单）
```

引擎每 ~15s 写一次 `state/heartbeat.json`（计时驱动，行情清淡也不会过期），记录会话阶段、是否已连接、距上次行情多久——`./afctl status` 直接展示，容器 `healthcheck` 也据此判 healthy。心跳是「进程/事件循环还活着」的信号，与「行情是否在流动」分开看（后者是 `last_quote_age`）。

`trade.jsonl` 出现 `engine_session_retrying` 表示进程仍在、但当前 IBKR 会话不可用，正在退避重连，恢复前不会下单（此时心跳阶段为 `retrying`）。

> 注意：Paper 常驻期间，尽量别用同一个 IBKR 用户在手机端重新登录/切换会话，否则可能干扰 Gateway 会话导致查询超时。更稳的做法是给 AWS Gateway 用专门的量化登录用户。

### 主动告警（Server酱 → 微信，可选）

原理：告警通过 [Server酱](https://sct.ftqq.com) 推到你的微信——你只需一个 `SendKey`。

1. 微信扫码登录 https://sct.ftqq.com ，复制页面上的 **SendKey**（形如 `SCTxxxxxxxx`）。
2. 关注它提示的服务号（这样消息才能进你微信）。
3. 在 `config/env` 填上（留空则全程静默）：
   ```
   SERVERCHAN_SENDKEY=SCTxxxxxxxx
   ```
4. 验证与生效：
   ```bash
   sudo ./afctl alert-test      # 立即发一条到微信，验证连通（一次性容器，现读现发）
   sudo ./afctl restart         # 让常驻 engine + watchdog 用上新 SendKey
   ./afctl logs watchdog        # 查看看门狗
   ```

两层覆盖：

- **引擎内告警**：关键事件实时推送——`engine_session_failed`（崩溃）、`order_rejected`（拒单熔断）、`grid_spec_reload_failed`（热加载失败）。低频高价值，刻意不含噪声事件（如 `risk_rejected`、重连中）。
- **看门狗容器**：独立进程盯 `heartbeat.json`，心跳超过 120s 未更新就告警——**引擎进程死了也能通知你**（死进程无法自报）。只在「健康↔异常」切换时各推一次，不会每分钟刷屏。

> 局限：若整台 AWS 实例宕机，两者都发不出告警（需外部探活，如 CloudWatch / UptimeRobot）。

---

## 更新与回滚

```bash
sudo ./afctl update
```

它会依次：`git pull --ff-only` → `docker compose build engine` → **跑验收单测** → 测试通过才 `compose up -d`。测试不过会中止且**保持原服务不动**，已跑通的交易链路不会被改坏。

回滚到上一个版本：

```bash
git -C . log --oneline -5     # 找到要回退的 commit
git checkout <commit>
sudo ./afctl up               # 重建并重启
```

---

## 故障排查

| 现象 | 排查 |
|---|---|
| 引擎连不上 IBKR | `./afctl logs gateway` 确认已登录；`./afctl status` 看 4001/4002 是否监听 |
| 手机登录后查询超时 | `sudo ./afctl restart`；常驻期间避免手机端重复登录同一用户 |
| 改了 grid.yaml 没生效 | 引擎数秒内热加载；用 `sudo ./afctl edit` 改可顺带校验。若校验失败会沿用旧配置 |
| 某标的不再交易了 | 可能被引擎熔断（下单被拒）；`./afctl grid` 看 `halted`，`sudo ./afctl resume <标的>` 解除 |
| 想紧急止损 | `sudo ./afctl kill on`（约 1 秒生效，不再下单；撤单/状态仍正常）|
| 容器起不来 | `./afctl logs engine` / `./afctl logs gateway` 看最近日志 |

---

## 已知限制

当前设计下的边界，了解后可避免踩坑（多数有简单规避手段）：

1. **行情订阅集合在启动时固定**。引擎启动时一次性订阅 `grid.yaml` 里所有未 `paused` 标的的行情；运行期间热加载只更新**已订阅标的**的参数（涨跌幅、`base_price` 等）。因此**新增标的、或恢复启动时即 `paused` 的标的，需 `sudo ./afctl restart` 才会开始报价**；改已有标的的参数则无需重启。

2. **`resume` 在多标的并发成交时可能被覆盖**。它从引擎外改写 `grid_state.json`，若恰好其它标的正在频繁成交，引擎下一次存盘可能盖回 `halted`。规避：在该标的安静时执行，或 `sudo ./afctl restart`（详见上文策略章节的警示框）。彻底解法（resume 走引擎控制通道）已记在 `clear_halt` 注释里，待真正用到多标的时再实现。

3. **手动清空 `state/grid_state.json` 时，同步删掉 `grid.yaml` 里的遗留运行态字段**。首次迁移时引擎会容忍 spec 里残留的 `state:`/`active_order:` 并据此 seed 状态；若日后手删了状态文件却把这些字段留在 spec 里，可能复活一个 IBKR 已不存在的幽灵订单（无事件可结算 → 卡在 `WAITING_TRADE`）。迁移成功后请清掉 spec 里的运行态字段。

4. **`afctl edit` 的语法校验依赖 IB 凭证已填**。校验会完整加载配置（含凭证检查），凭证为空时即使 grid 本身没问题也会报校验失败；正常部署后凭证已填，不受影响。

5. **（待实例确认）`compose run` 与固定 `container_name`**。`validate`/`resume`/`doctor` 用 `docker compose run` 起一次性容器执行，视 compose 版本可能与运行中的 `alphaforge-engine` 重名冲突。在实例上 `sudo ./afctl up` 后跑 `sudo ./afctl doctor`：若报 `container name ... already in use`，需把这三条改用 `compose exec`。

---

## 本地测试

无需 Docker，纯逻辑单测（策略/订单/风控/配置）：

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```

或在容器里跑（与 `afctl update` 用的同一套验收）：`./afctl test`。

---

## 目录结构

```
AlphaForge/
  afctl                  唯一控制入口
  start.sh               一键部署（幂等）
  docker-compose.yml     ib-gateway + engine
  Dockerfile             引擎镜像
  config/
    env(.example)        IB 凭证（真文件 gitignore）
    config.yaml          端口/路径/风控
    grid(.example).yaml  策略声明 spec（真文件 gitignore）
  state/
    grid_state.json      运行态 status：演化 base_price/订单/状态（引擎写）
    heartbeat.json       引擎心跳 liveness（每 ~15s 覆盖写；healthcheck/status 读取）
    kill-switch          紧急停下单标志；grid.yaml.bak.* 编辑备份
  logs/                  trade.jsonl、audit.jsonl（运行时，引擎内按 50MiB×5 自动轮转）
  scripts/               lib.sh（公共函数）、bootstrap.sh（装 Docker）
  src/alphaforge/        引擎代码：core / strategies / adapters / execution / state / logging
  tests/                 验收单测
```
