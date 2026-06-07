from __future__ import annotations

import shutil
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from alphaforge.core.config import ConfigError
from alphaforge.core.models import (
    ActiveOrder,
    GridEntry,
    GridRuntimeConfig,
    GridState,
    Side,
    TradingWindow,
)


class GridStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> GridRuntimeConfig:
        if not self.path.exists():
            raise ConfigError(f"grid config file not found: {self.path}")
        raw = yaml.safe_load(self.path.read_text()) or {}
        if not isinstance(raw, dict):
            raise ConfigError("grid.yaml must contain a mapping")

        window_raw = _mapping(raw, "trading_window")
        grids_raw = raw.get("grids", [])
        if not isinstance(grids_raw, list) or not grids_raw:
            raise ConfigError("grid.yaml grids must be a non-empty list")

        return GridRuntimeConfig(
            strategy_name=str(raw.get("strategy_name", "grid_v1")),
            audit_log_sample_rate=float(raw.get("audit_log_sample_rate", 0.01)),
            trading_window=TradingWindow(
                timezone=str(window_raw.get("timezone", "America/New_York")),
                start=str(window_raw.get("start", "04:00")),
                end=str(window_raw.get("end", "20:00")),
                outside_rth=bool(window_raw.get("outside_rth", True)),
            ),
            grids=[_grid_entry(item) for item in grids_raw],
        )

    def save(self, config: GridRuntimeConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            shutil.copy2(self.path, self.path.with_suffix(self.path.suffix + ".bak"))

        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(yaml.safe_dump(_runtime_config_dict(config), sort_keys=False))
        tmp_path.chmod(0o660)
        tmp_path.replace(self.path)


def _grid_entry(raw: Any) -> GridEntry:
    if not isinstance(raw, dict):
        raise ConfigError("each grid entry must be a mapping")
    symbol = str(raw.get("symbol", "")).upper().strip()
    if not symbol:
        raise ConfigError("grid symbol is required")
    base_price = float(raw.get("base_price", 0))
    up_pct = float(raw.get("up_pct", 0))
    down_pct = float(raw.get("down_pct", 0))
    trade_amount = float(raw.get("trade_amount", 0))
    if base_price <= 0:
        raise ConfigError(f"grid {symbol} base_price must be positive")
    if up_pct <= 0 or down_pct <= 0:
        raise ConfigError(f"grid {symbol} up_pct/down_pct must be positive")
    if trade_amount <= 0:
        raise ConfigError(f"grid {symbol} trade_amount must be positive")
    active_raw = raw.get("active_order")
    return GridEntry(
        symbol=symbol,
        base_price=base_price,
        up_pct=up_pct,
        down_pct=down_pct,
        trade_amount=trade_amount,
        state=GridState(str(raw.get("state", GridState.WAITING_TRIGGER.value))),
        paused=bool(raw.get("paused", False)),
        active_order=_active_order(active_raw),
    )


def _active_order(raw: Any) -> ActiveOrder | None:
    if raw in (None, "", {}):
        return None
    if not isinstance(raw, dict):
        raise ConfigError("active_order must be null or a mapping")
    submitted_raw = raw.get("submitted_at")
    submitted_at = (
        datetime.fromisoformat(str(submitted_raw))
        if submitted_raw
        else datetime.now().astimezone()
    )
    return ActiveOrder(
        side=Side(str(raw.get("side"))),
        base_price_at_submit=float(raw.get("base_price_at_submit")),
        limit_price=float(raw.get("limit_price")),
        quantity=int(raw.get("quantity")),
        filled_quantity=float(raw.get("filled_quantity", 0)),
        remaining_quantity=float(raw.get("remaining_quantity", raw.get("quantity", 0))),
        order_id=_optional_int(raw.get("order_id")),
        perm_id=_optional_int(raw.get("perm_id")),
        order_ref=str(raw.get("order_ref", "")),
        status=str(raw.get("status", "Submitted")),
        submitted_at=submitted_at,
        cancel_requested=bool(raw.get("cancel_requested", False)),
        execution_filled_quantity=float(raw.get("execution_filled_quantity", 0)),
        seen_exec_ids=[str(item) for item in raw.get("seen_exec_ids", [])],
    )


def _runtime_config_dict(config: GridRuntimeConfig) -> dict[str, Any]:
    return {
        "strategy_name": config.strategy_name,
        "audit_log_sample_rate": config.audit_log_sample_rate,
        "trading_window": asdict(config.trading_window),
        "grids": [_grid_entry_dict(grid) for grid in config.grids],
    }


def _grid_entry_dict(grid: GridEntry) -> dict[str, Any]:
    return {
        "symbol": grid.symbol,
        "base_price": _round_price(grid.base_price),
        "up_pct": grid.up_pct,
        "down_pct": grid.down_pct,
        "trade_amount": grid.trade_amount,
        "state": grid.state.value,
        "paused": grid.paused,
        "active_order": _active_order_dict(grid.active_order),
    }


def _active_order_dict(order: ActiveOrder | None) -> dict[str, Any] | None:
    if order is None:
        return None
    return {
        "side": order.side.value,
        "base_price_at_submit": _round_price(order.base_price_at_submit),
        "limit_price": _round_price(order.limit_price),
        "quantity": order.quantity,
        "filled_quantity": order.filled_quantity,
        "remaining_quantity": order.remaining_quantity,
        "order_id": order.order_id,
        "perm_id": order.perm_id,
        "order_ref": order.order_ref,
        "status": order.status,
        "submitted_at": order.submitted_at.isoformat(),
        "cancel_requested": order.cancel_requested,
        "execution_filled_quantity": order.execution_filled_quantity,
        "seen_exec_ids": list(order.seen_exec_ids),
    }


def _mapping(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"grid.yaml {name} section must be a mapping")
    return value


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _round_price(value: float) -> float:
    return round(float(value), 4)
