from __future__ import annotations

import json
import random
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


class EventLogger:
    def __init__(
        self,
        audit_path: Path,
        trade_path: Path,
        strategy_name: str,
        mode: str,
        account: str,
        regular_sample_rate: float,
    ) -> None:
        self.audit_path = audit_path
        self.trade_path = trade_path
        self.strategy_name = strategy_name
        self.mode = mode
        self.account = account
        self.regular_sample_rate = regular_sample_rate
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        self.trade_path.parent.mkdir(parents=True, exist_ok=True)

    def trade(self, event_type: str, symbol: str, **details: object) -> None:
        record = self._record(event_type, symbol, details)
        self._append(self.trade_path, record)
        self._append(self.audit_path, record)

    def regular(self, event_type: str, symbol: str, **details: object) -> None:
        if self.regular_sample_rate <= 0:
            return
        if self.regular_sample_rate < 1 and random.random() > self.regular_sample_rate:
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

    @staticmethod
    def _append(path: Path, record: dict[str, object]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")


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
