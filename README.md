# AlphaForge

AlphaForge 是一套运行在 AWS us-east-1 EC2 上的 IBKR 量化交易程序。
IB Gateway 用 Docker Compose 运行，Python 交易程序运行在 host 的 venv 中，并由
systemd 管理。

## 目录关系

```text
/home/ubuntu/AlphaForge
  用途：Git clone 得到的源码工作区，用于 git pull 和 sudo ./install.sh。
  创建/维护：由用户 git clone 创建，用户通过 git pull 更新。
  主要内容：src/、files/、tests/、install.sh、start.sh、pyproject.toml、README.md。

/opt/alphaforge/app
  用途：生产运行代码目录，systemd 的 WorkingDirectory 指向这里。
  创建/维护：由 install.sh 从源码工作区同步生成；只同步运行所需文件，files/、tests/ 和 install.sh 不同步进来。

/opt/alphaforge/venv
  用途：生产 Python 虚拟环境，提供 /opt/alphaforge/venv/bin/alphaforge。
  创建/维护：由 install.sh 创建和更新。

/etc/alphaforge
  用途：生产配置目录，保存 env、config.yaml、grid.yaml、docker-compose.yml。
  创建/维护：由 install.sh 创建；env/config.yaml/grid.yaml 首次创建后不覆盖，docker-compose.yml 每次安装更新。

/etc/systemd/system/alphaforge.service
  用途：systemd 服务文件，定义 AlphaForge 如何作为后台服务运行。
  创建/维护：由 install.sh 每次安装更新。

/var/log/alphaforge
  用途：日志目录，保存 service.log、trade.jsonl 和 audit.jsonl。
  创建/维护：由 install.sh 创建，运行时由 alphaforge 服务写入。

/var/lib/alphaforge
  用途：状态目录，保存 kill-switch 和轻量状态文件。
  创建/维护：由 install.sh 创建，运行时由 alphaforge 服务读写。
```

简单理解：`/home/ubuntu/AlphaForge` 是源码；`/opt/alphaforge` 是程序运行环境；
`/etc/alphaforge` 是配置；`/var/log` 和 `/var/lib` 是运行产物。

`install.sh` 必须从源码工作区执行：

```bash
cd /home/ubuntu/AlphaForge
sudo ./install.sh
```

它会把运行所需代码同步到 `/opt/alphaforge/app`，并排除 `files/`、`tests/`、`install.sh` 等安装和测试文件。
生产启动统一使用：

```bash
sudo /opt/alphaforge/app/start.sh
```

## 源码结构

```text
commands.py   alphaforge doctor/run/kill 命令入口。
engine.py     交易程序编排和主循环。
core/         基础配置和领域模型。
adapters/     外部系统适配，当前主要是 IBKR。
strategies/   策略逻辑，当前是 grid_v1。
execution/    下单、撤单、风控、订单状态处理。
state/        状态文件读写。
logging/      日志记录，当前写 trade.jsonl 和 audit.jsonl。
```

## 首次安装

```bash
git clone git@github.com:qibolee/AlphaForge.git
cd AlphaForge
sudo ./install.sh
sudo nano /etc/alphaforge/env
sudo /opt/alphaforge/app/start.sh
```

## 更新安装

```bash
cd /home/ubuntu/AlphaForge
git pull
sudo ./install.sh
sudo /opt/alphaforge/app/start.sh
```

## 配置覆盖规则

首次执行 `install.sh` 会创建：

```text
/etc/alphaforge/env
/etc/alphaforge/config.yaml
/etc/alphaforge/grid.yaml
```

再次执行 `install.sh` 不会覆盖这些文件，避免覆盖已经填写好的真实账户信息、本机参数和策略状态。

每次执行 `install.sh` 会更新：

```text
/etc/alphaforge/docker-compose.yml
/etc/systemd/system/alphaforge.service
/etc/logrotate.d/alphaforge
/opt/alphaforge/app
/opt/alphaforge/venv
```

## IB Gateway 端口

`files/etc/alphaforge/docker-compose.yml` 中不要把端口改成同端口映射。

```yaml
ports:
  - "127.0.0.1:4002:4004"
  - "127.0.0.1:4001:4003"
```

Docker 映射格式是 `host_ip:host_port:container_port`。当前镜像中：

```text
container 4004 = paper relay
container 4003 = live relay
```

所以 Python 程序仍连接 host 常用端口：

```text
paper -> 127.0.0.1:4002
live  -> 127.0.0.1:4001
```

## 常驻运行与观测

AlphaForge 在 AWS 上常驻运行时，建议从服务、Docker、日志、策略状态四个角度观察。

服务状态：

```bash
sudo systemctl status alphaforge
sudo systemctl is-active alphaforge
sudo systemctl cat alphaforge
sudo journalctl -u alphaforge -n 100 --no-pager
sudo journalctl -u alphaforge -f
```

`systemctl status` 看到 `active (running)` 表示 Python 交易服务正在运行。`systemctl cat`
可以确认 systemd 实际执行的命令，目前应为 `/opt/alphaforge/venv/bin/alphaforge run`。

IB Gateway Docker 状态：

```bash
sudo docker ps -a --filter "name=^/ib-gateway$"
sudo docker logs --tail 100 ib-gateway
sudo docker logs -f ib-gateway
sudo docker compose --env-file /etc/alphaforge/env -f /etc/alphaforge/docker-compose.yml ps
sudo docker compose --env-file /etc/alphaforge/env -f /etc/alphaforge/docker-compose.yml logs -f
```

`ib-gateway` 容器需要保持 `Up`。如果 AlphaForge 无法连接 IBKR，优先查看 Docker 日志和
VNC 登录状态，确认 IB Gateway 已经登录成功并启用 API。

如果用手机登录同一个 IBKR paper 用户后，日志出现 `portfolio request timed out`、
`accountSummaryAsync` 超时、订单/成交查询超时，通常表示 IB Gateway 登录会话被手机端干扰。
短期恢复步骤：

```bash
sudo systemctl stop alphaforge
sudo docker logs --tail 200 ib-gateway
sudo docker restart ib-gateway
sudo docker logs -f ib-gateway
sudo /opt/alphaforge/app/start.sh
```

`start.sh` 会重新执行 doctor、启动 systemd 服务，并等待 AlphaForge 重新完成
`connected`、`reconcile_completed`、`portfolio_loaded`、`quote_stream_starting`。

Paper 常驻测试期间，尽量不要用同一个 IBKR 用户在手机上重新登录或切换会话。如果手机 App
已经保持登录状态，可以只查看净值、持仓和订单；不要退出再登录、切换 paper/live、切换用户、
处理重新认证提示或发起交易。如果手机端提示必须重新登录，建议先停止 AlphaForge，查看完成后
再重启 IB Gateway 和 AlphaForge。更稳的做法是让 AWS IB Gateway 使用专门的量化登录用户，
手机查看使用另一个登录用户。

AlphaForge 日志：

```bash
sudo journalctl -u alphaforge -f
sudo tail -f /var/log/alphaforge/trade.jsonl
sudo tail -f /var/log/alphaforge/service.log
sudo tail -f /var/log/alphaforge/audit.jsonl
```

`service.log` 是服务 stdout/stderr，主要用于查看异常堆栈和底层库提示。`trade.jsonl`
只记录交易关键事件，例如连接、reconcile、组合加载、触发下单、订单成交、撤单和风控拒绝。
`audit.jsonl` 记录交易事件和采样后的常规事件，例如行情查询、未触发、非交易窗口等。

策略状态：

```bash
sudo cat /etc/alphaforge/grid.yaml
```

`grid.yaml` 是网格策略参数和状态文件。`WAITING_TRIGGER` 表示没有活跃订单，正在等待价格触发。
`WAITING_TRADE` 表示已经提交订单，正在等待 IBKR 的成交、取消或过期事件。`active_order`
为空通常应对应 `WAITING_TRIGGER`；如果存在 `active_order`，通常应对应 `WAITING_TRADE`。

常见健康判断：

```text
alphaforge.service 是 active (running)
ib-gateway 容器是 Up
trade.jsonl 能看到 connected、reconcile_completed、portfolio_loaded、quote_stream_starting
audit.jsonl 在持续出现 quote_no_trigger、outside_trading_window 或其他行情相关事件
grid.yaml 里的 state 和 active_order 关系合理
```

如果 `trade.jsonl` 出现 `engine_session_retrying`，表示 AlphaForge 进程仍在运行，但当前
IBKR 会话不可用，程序正在等待后重新连接、reconcile 和加载 portfolio。恢复前不会进入策略下单。

如果修改 `/etc/alphaforge/grid.yaml`，建议先停止交易服务，避免程序运行时同时写入：

```bash
sudo systemctl stop alphaforge
sudo cp /etc/alphaforge/grid.yaml /etc/alphaforge/grid.yaml.bak.$(date +%F-%H%M%S)
sudo nano /etc/alphaforge/grid.yaml
sudo systemctl start alphaforge
sudo systemctl status alphaforge
```

## 日志轮转

`install.sh` 会安装 Ubuntu 标准 `logrotate`，并把规则安装到 `/etc/logrotate.d/alphaforge`。
日志不是等磁盘满了才删除，而是按天轮转、超过大小提前轮转、压缩旧文件、只保留固定份数：

```text
audit.jsonl    daily, maxsize 100M, rotate 14, compress
trade.jsonl    daily, maxsize 20M,  rotate 90, compress
service.log    daily, maxsize 20M,  rotate 30, compress
```

规则里使用 `copytruncate`，所以轮转时不需要重启 AlphaForge。
轮转后的旧文件会带日期时间后缀并压缩，例如 `trade.jsonl-20260607-031500.gz`。
可以用下面命令检查规则：

```bash
ls -lh /var/log/alphaforge
sudo logrotate -d /etc/logrotate.d/alphaforge
```

如需手动强制轮转一次：

```bash
sudo logrotate -f /etc/logrotate.d/alphaforge
```

## CloudWatch Agent

CloudWatch Agent 本项目不自动安装，因为它需要 AWS 侧的 IAM role、CloudWatch Logs 权限和
账号级配置。建议在 AWS 上采集：

```text
EC2 CPU、内存、磁盘使用率
/var/log/alphaforge/service.log
/var/log/alphaforge/trade.jsonl
/var/log/alphaforge/audit.jsonl
```

参考 AWS 官方文档：

- [Install CloudWatch Agent](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/install-CloudWatch-Agent-on-EC2-Instance.html)
- [CloudWatch Agent configuration file](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-Agent-Configuration-File-Details.html)
- [CloudWatch Logs getting started](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_GettingStarted.html)

## grid_v1 策略

`/etc/alphaforge/config.yaml` 保存 IBKR、路径、风控等通用配置。`/etc/alphaforge/grid.yaml`
保存网格策略参数和可变状态，包含每个标的的 `base_price`、涨跌幅、单次交易金额、
当前状态和活跃订单。
`grid.yaml` 由 `alphaforge` 服务用户写入，`env` 和 `config.yaml` 仍由 root 管理。

`grid_v1` 的用户可见状态只有两种：

```text
WAITING_TRIGGER  没有活跃订单，等待价格触发买入或卖出。
WAITING_TRADE    已经触发并提交订单，等待 IBKR 成交、取消或过期事件。
```

触发规则：

```text
卖出触发：latest_price >= base_price * (1 + up_pct * 0.99)
买入触发：latest_price <= base_price * (1 - down_pct * 0.99)
```

挂单仍使用完整网格价：

```text
卖出限价：base_price * (1 + up_pct)
买入限价：base_price * (1 - down_pct)
数量：floor(trade_amount / limit_price)
```

订单使用 `GTD`，有效期 7 个自然日，并允许盘前盘后成交。挂单等待期间，如果价格回到
下单时的 `base_price`，程序会主动撤单。撤单完成后，`base_price` 根据最终成交比例自适应调整：

```text
new_base_price = old_base_price + fill_ratio * (limit_price - old_base_price)
```

零成交时 `base_price` 不变；部分成交时向挂单价移动一部分；全部成交时更新为挂单价。
手动修改 `/etc/alphaforge/grid.yaml` 前建议先停止服务。

## 命令

```bash
/opt/alphaforge/venv/bin/alphaforge doctor
/opt/alphaforge/venv/bin/alphaforge run
/opt/alphaforge/venv/bin/alphaforge kill --on
/opt/alphaforge/venv/bin/alphaforge kill --off
/opt/alphaforge/venv/bin/alphaforge kill --status
```

## 本地测试

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
pytest
```
