from __future__ import annotations

import argparse
import asyncio
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

from alphaforge.core.config import ConfigError, load_settings
from alphaforge.engine import run_forever


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

    return parser


def _doctor(_args: argparse.Namespace) -> int:
    settings = load_settings()
    checks = [
        ("env/config", True, f"mode={settings.env.mode.value} account={settings.env.account}"),
        ("log_dir", _writable_dir(settings.paths.log_dir), str(settings.paths.log_dir)),
        ("state_dir", _writable_dir(settings.paths.state_dir), str(settings.paths.state_dir)),
        (
            "grid_config",
            _readable_writable_file(settings.paths.grid_config),
            str(settings.paths.grid_config),
        ),
        (
            "grid_config_dir",
            os.access(settings.paths.grid_config.parent, os.W_OK),
            str(settings.paths.grid_config.parent),
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


def _writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False


def _readable_writable_file(path: Path) -> bool:
    return path.exists() and path.is_file() and os.access(path, os.R_OK | os.W_OK)


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
