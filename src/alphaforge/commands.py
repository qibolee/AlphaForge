from __future__ import annotations

import argparse
import asyncio
import os
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from alphaforge.alerts import (
    WATCHDOG_INTERVAL_SECONDS,
    WATCHDOG_STALE_SECONDS,
    Notifier,
    watchdog_decision,
)
from alphaforge.core.config import ConfigError, load_settings
from alphaforge.engine import run_forever
from alphaforge.state.grid_store import GridStateStore
from alphaforge.state.heartbeat import HEARTBEAT_MAX_AGE_SECONDS, heartbeat_age_seconds


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alphaforge")
    sub = parser.add_subparsers(required=True)

    doctor = sub.add_parser("doctor")
    doctor.set_defaults(func=_doctor)

    run = sub.add_parser("run")
    run.set_defaults(func=_run)

    kill = sub.add_parser("kill")
    group = kill.add_mutually_exclusive_group(required=True)
    group.add_argument("--on", action="store_true")
    group.add_argument("--off", action="store_true")
    group.add_argument("--status", action="store_true")
    kill.set_defaults(func=_kill)

    validate = sub.add_parser("validate")
    validate.set_defaults(func=_validate)

    resume = sub.add_parser("resume")
    resume.add_argument("--symbol", default=None)
    resume.set_defaults(func=_resume)

    healthz = sub.add_parser("healthz")
    healthz.set_defaults(func=_healthz)

    watchdog = sub.add_parser("watchdog")
    watchdog.set_defaults(func=_watchdog)

    alert_test = sub.add_parser("alert-test")
    alert_test.set_defaults(func=_alert_test)

    return parser


def _doctor(_args: argparse.Namespace) -> int:
    settings = load_settings()
    checks = [
        ("env/config", True, f"mode={settings.env.mode.value} account={settings.env.account}"),
        ("log_dir", _writable_dir(settings.paths.log_dir), str(settings.paths.log_dir)),
        ("state_dir", _writable_dir(settings.paths.state_dir), str(settings.paths.state_dir)),
        (
            "grid_spec",
            _grid_loads(settings),
            str(settings.paths.grid_config),
        ),
        (
            "grid_state_dir",
            os.access(settings.paths.grid_state.parent, os.W_OK),
            str(settings.paths.grid_state.parent),
        ),
        (
            "ibkr_socket",
            _socket_open(settings.ibkr.host, settings.ibkr_port),
            f"{settings.ibkr.host}:{settings.ibkr_port}",
        ),
    ]
    ok = True
    for name, passed, message in checks:
        ok = ok and passed
        print(f"{'OK' if passed else 'FAIL'} {name}: {message}")
    return 0 if ok else 1


def _run(_args: argparse.Namespace) -> int:
    _enable_timestamped_service_output()
    asyncio.run(run_forever())
    return 0


def _kill(args: argparse.Namespace) -> int:
    settings = load_settings()
    path = settings.paths.kill_switch
    if args.on:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("manual\n")
    elif args.off:
        if path.exists():
            path.unlink()
    print(f"kill_switch={'on' if path.exists() else 'off'} path={path}")
    return 0


def _validate(_args: argparse.Namespace) -> int:
    # Loads env + config + grid spec/state. Raises ConfigError on a bad grid.yaml,
    # which afctl edit uses to validate a live edit (no IBKR socket check).
    settings = load_settings()
    GridStateStore(settings.paths.grid_config, settings.paths.grid_state).load()
    print("config OK")
    return 0


def _resume(args: argparse.Namespace) -> int:
    settings = load_settings()
    store = GridStateStore(settings.paths.grid_config, settings.paths.grid_state)
    cleared = store.clear_halt(args.symbol)
    print(f"resumed: {', '.join(cleared)}" if cleared else "no halted grids to resume")
    return 0


def _healthz(_args: argparse.Namespace) -> int:
    # Liveness probe for the compose healthcheck. The engine writes a heartbeat
    # every ~15s (timer-driven, independent of quote flow); healthy if it's fresh.
    settings = load_settings()
    age = heartbeat_age_seconds(settings.paths.heartbeat)
    if age is None:
        print("FAIL healthz: no heartbeat yet")
        return 1
    if age > HEARTBEAT_MAX_AGE_SECONDS:
        print(f"FAIL healthz: stale heartbeat ({age:.0f}s old)")
        return 1
    print(f"OK healthz: heartbeat {age:.0f}s old")
    return 0


def _watchdog(_args: argparse.Namespace) -> int:
    # Standalone process (its own container) that watches the engine heartbeat and
    # pages on staleness — a dead engine cannot report its own death.
    settings = load_settings()
    notifier = Notifier.from_settings(settings)
    path = settings.paths.heartbeat
    if not notifier.enabled:
        print("watchdog: SERVERCHAN_SENDKEY 未配置，空闲待命（填好后 ./afctl restart 生效）")
        while True:
            time.sleep(3600)
    print(f"watchdog: 监视 {path}（>{WATCHDOG_STALE_SECONDS}s 视为异常，每 {WATCHDOG_INTERVAL_SECONDS}s 检查）")
    was_healthy = True
    time.sleep(WATCHDOG_INTERVAL_SECONDS)  # grace for the engine's first heartbeat
    while True:
        was_healthy, message = watchdog_decision(heartbeat_age_seconds(path), was_healthy)
        if message:
            notifier.send("AlphaForge 引擎监控", message)
        time.sleep(WATCHDOG_INTERVAL_SECONDS)


def _alert_test(_args: argparse.Namespace) -> int:
    settings = load_settings()
    notifier = Notifier.from_settings(settings)
    if not notifier.enabled:
        print("alerts disabled: 请在 config/env 填 SERVERCHAN_SENDKEY")
        return 1
    ok = notifier.send(
        "AlphaForge 告警测试",
        f"渠道连通正常 [{settings.env.mode.value}/{settings.env.account}]",
    )
    print("sent（去微信看看收到没）" if ok else "send failed（检查 SendKey / 网络）")
    return 0 if ok else 1


def _grid_loads(settings: object) -> bool:
    try:
        GridStateStore(settings.paths.grid_config, settings.paths.grid_state).load()
        return True
    except Exception:
        return False


def _writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False


def _socket_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def _enable_timestamped_service_output() -> None:
    sys.stdout = _TimestampedStream(sys.stdout)
    sys.stderr = _TimestampedStream(sys.stderr)


class _TimestampedStream:
    def __init__(self, stream: object) -> None:
        self.stream = stream
        self._line_start = True

    def write(self, text: str) -> int:
        for chunk in text.splitlines(keepends=True):
            if self._line_start and chunk:
                self.stream.write(f"{datetime.now(timezone.utc).isoformat()} ")
            self.stream.write(chunk)
            self._line_start = chunk.endswith("\n")
        self.flush()
        return len(text)

    def flush(self) -> None:
        self.stream.flush()

    def isatty(self) -> bool:
        return False


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
