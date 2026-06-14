from __future__ import annotations

import json
import random
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alphaforge.alerts import Notifier

# Size-based rotation keeps the JSONL logs self-contained in ./logs (no host
# logrotate/cron). Each stream is capped at roughly (backups + 1) * max_bytes.
DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # 50 MiB per file
DEFAULT_BACKUP_COUNT = 5  # keep .1 .. .5 -> ~300 MiB per stream worst case

# Low-frequency, high-importance events that page you via the notifier.
# Deliberately excludes noisy ones (risk_rejected, engine_session_retrying);
# sustained outages are covered by the heartbeat watchdog instead.
CRITICAL_ALERT_EVENTS = frozenset(
    {"engine_session_failed", "order_rejected", "grid_spec_reload_failed"}
)


class EventLogger:
    def __init__(
        self,
        audit_path: Path,
        trade_path: Path,
        strategy_name: str,
        mode: str,
        account: str,
        audit_sample_rate: float,
        max_bytes: int = DEFAULT_MAX_BYTES,
        backup_count: int = DEFAULT_BACKUP_COUNT,
        notifier: "Notifier | None" = None,
        alert_events: frozenset[str] = CRITICAL_ALERT_EVENTS,
    ) -> None:
        self.audit_path = audit_path
        self.trade_path = trade_path
        self.strategy_name = strategy_name
        self.mode = mode
        self.account = account
        self.audit_sample_rate = audit_sample_rate
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.notifier = notifier
        self.alert_events = alert_events
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        self.trade_path.parent.mkdir(parents=True, exist_ok=True)

    _ALERT_HEADINGS = {
        "engine_session_failed": "🔴 引擎崩溃（不可恢复，进程将退出）",
        "order_rejected": "🟠 订单被拒，该标的已熔断",
        "grid_spec_reload_failed": "🟠 grid.yaml 热加载失败，沿用上一份配置",
    }

    def trade(self, event_type: str, symbol: str, **details: object) -> None:
        record = self._record(event_type, symbol, details)
        self._append(self.trade_path, record)
        self._append(self.audit_path, record)
        # Fire-and-forget alert on critical events. notify() never blocks the
        # trading loop (sends on a daemon thread) and never raises.
        if self.notifier is not None and event_type in self.alert_events:
            self.notifier.notify(*self._alert_message(event_type, symbol, details))

    def _alert_message(
        self, event_type: str, symbol: str, details: dict[str, object]
    ) -> tuple[str, str]:
        heading = self._ALERT_HEADINGS.get(event_type, event_type)
        title = f"AlphaForge {heading}"
        lines = [f"账户 {self.mode}/{self.account}"]
        if symbol and symbol != "_system":
            lines.append(f"标的 {symbol}")
        for key in ("reason", "error", "error_type"):
            value = details.get(key)
            if value:
                lines.append(f"{key}: {value}")
        return title, "\n".join(lines)

    def regular(self, event_type: str, symbol: str, **details: object) -> None:
        if self.audit_sample_rate <= 0:
            return
        if self.audit_sample_rate < 1 and random.random() > self.audit_sample_rate:
            return
        self._append(self.audit_path, self._record(event_type, symbol, details))

    def _record(
        self,
        event_type: str,
        symbol: str,
        details: dict[str, object],
    ) -> dict[str, object]:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "strategy_name": self.strategy_name,
            "event_type": event_type,
            "symbol": symbol,
            "mode": self.mode,
            "account": self.account,
            "details": _jsonable(details),
        }

    def _append(self, path: Path, record: dict[str, object]) -> None:
        line = json.dumps(record, separators=(",", ":")) + "\n"
        if (
            self.max_bytes > 0
            and path.exists()
            and path.stat().st_size + len(line.encode("utf-8")) > self.max_bytes
        ):
            self._rotate(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)

    def _rotate(self, path: Path) -> None:
        if self.backup_count < 1:
            path.write_text("", encoding="utf-8")
            return
        # Shift .{n-1} -> .{n} (dropping the oldest), then current -> .1.
        for index in range(self.backup_count - 1, 0, -1):
            src = path.with_name(f"{path.name}.{index}")
            if src.exists():
                src.replace(path.with_name(f"{path.name}.{index + 1}"))
        path.replace(path.with_name(f"{path.name}.1"))


def _jsonable(value: object) -> object:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value
