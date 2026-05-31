# AlphaForge

AlphaForge 是一套部署在 AWS us-east-1 EC2 上的 IBKR 量化交易程序。IB Gateway 用 Docker Compose 运行，Python 交易程序运行在 host 的 venv 中，并由 systemd 管理。

## 目录关系

```text
/home/ubuntu/AlphaForge
  用途：Git clone 得到的源码工作区，用于 git pull 和 sudo ./deploy.sh。
  创建/维护：由用户 git clone 创建，用户通过 git pull 更新。
  主要内容：src/、deploy/、tests/、deploy.sh、start.sh、pyproject.toml、README.md。

/opt/alphaforge/app
  用途：生产运行代码目录，systemd 的 WorkingDirectory 指向这里。
  创建/维护：由 deploy.sh 从源码工作区同步生成。
  说明：只同步运行所需文件；deploy/ 和 tests/ 不同步进来。

/opt/alphaforge/venv
  用途：生产 Python 虚拟环境，提供 /opt/alphaforge/venv/bin/alphaforge。
  创建/维护：由 deploy.sh 创建和更新。

/etc/alphaforge
  用途：生产配置目录，保存 env、config.yaml、docker-compose.yml。
  创建/维护：由 deploy.sh 创建；env 和 config.yaml 首次创建后不覆盖，docker-compose.yml 每次部署更新。

/etc/systemd/system/alphaforge.service
  用途：systemd 服务文件，定义 AlphaForge 如何作为后台服务运行。
  创建/维护：由 deploy.sh 每次部署更新。

/var/log/alphaforge
  用途：日志目录，保存 service.log 和 audit.jsonl。
  创建/维护：由 deploy.sh 创建，运行时由 alphaforge 服务写入。

/var/lib/alphaforge
  用途：状态目录，保存 kill-switch 和轻量状态文件。
  创建/维护：由 deploy.sh 创建，运行时由 alphaforge 服务读写。
```

简单理解：`/home/ubuntu/AlphaForge` 是源码；`/opt/alphaforge` 是程序运行环境；`/etc/alphaforge` 是配置；`/var/log` 和 `/var/lib` 是运行产物。

`deploy.sh` 必须从源码工作区执行：

```bash
cd /home/ubuntu/AlphaForge
sudo ./deploy.sh
```

它会把运行所需代码同步到 `/opt/alphaforge/app`，并排除 `deploy/`、`tests/`、`deploy.sh` 等部署和测试文件。生产启动统一使用：

```bash
sudo /opt/alphaforge/app/start.sh
```

## 首次部署

```bash
git clone git@github.com:<owner>/AlphaForge.git
cd AlphaForge
sudo ./deploy.sh
sudo nano /etc/alphaforge/env
sudo /opt/alphaforge/app/start.sh
```

## 更新部署

```bash
cd /home/ubuntu/AlphaForge
git pull
sudo ./deploy.sh
sudo /opt/alphaforge/app/start.sh
```

## 配置覆盖规则

首次部署会创建：

```text
/etc/alphaforge/env
/etc/alphaforge/config.yaml
```

非首次部署不会覆盖这两个文件，避免覆盖已经填写好的真实账户信息和本机参数。

每次部署会更新：

```text
/etc/alphaforge/docker-compose.yml
/etc/systemd/system/alphaforge.service
/opt/alphaforge/app
/opt/alphaforge/venv
```

## IB Gateway 端口

`deploy/etc/alphaforge/docker-compose.yml` 中不要把端口改成同端口映射。

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
sudo tail -f /var/log/alphaforge/service.log
sudo tail -f /var/log/alphaforge/audit.jsonl
```

`service.log` 是服务 stdout/stderr。`audit.jsonl` 是交易审计日志，记录连接、信号、风控和订单事件。

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
