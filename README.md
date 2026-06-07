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
  用途：日志目录，保存 service.log 和 audit.jsonl。
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

## 日志

```bash
sudo journalctl -u alphaforge -f
sudo tail -f /var/log/alphaforge/trade.jsonl
sudo tail -f /var/log/alphaforge/service.log
sudo tail -f /var/log/alphaforge/audit.jsonl
```

`service.log` 是服务 stdout/stderr。`trade.jsonl` 只记录交易关键事件。`audit.jsonl`
记录交易事件和采样后的常规事件。

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
