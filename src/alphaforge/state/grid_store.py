from __future__ import annotations

import json
import shutil
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

STATE_VERSION = 1


class GridStateStore:
    """Spec/Status split for the grid.

    - Spec  (config/grid.yaml): user-owned strategy params. Engine reads, never writes,
      so it can be edited live (``afctl edit``) without stopping the engine.
    - Status (state/grid_state.json): engine-owned runtime — the evolved ``base_price``,
      active orders and grid state. Engine reads + writes; users do not hand-edit it.

    ``load()`` reconciles the two into the in-memory ``GridRuntimeConfig`` the rest of the
    engine already uses (unchanged). ``save()`` persists only the runtime status.

    ``base_price`` is dual-natured: the user seeds an anchor in the spec and the engine
    evolves it after fills. We remember the last spec anchor in the status; if the spec
    anchor changes, the user re-anchored manually and their value wins, otherwise the
    engine's evolved value is kept.
    """

    def __init__(self, spec_path: Path, state_path: Path) -> None:
        self.spec_path = spec_path
        self.state_path = state_path
        # Filled on load(), consumed on save() to translate the in-memory single
        # ``paused`` flag back into "user pause" (spec) vs "engine halt" (status).
        self._spec_base: dict[str, float] = {}
        self._spec_paused: dict[str, bool] = {}

    # --- load -----------------------------------------------------------------
    def load(self) -> GridRuntimeConfig:
        spec = self._load_spec()
        symbols_state = self._load_state().get("symbols", {})

        self._spec_base = {}
        self._spec_paused = {}
        grids = [self._reconcile(raw, symbols_state.get(raw["symbol"])) for raw in spec["grids"]]

        return GridRuntimeConfig(
            strategy_name=spec["strategy_name"],
            audit_log_sample_rate=spec["audit_log_sample_rate"],
            trading_window=spec["trading_window"],
            grids=grids,
        )

    def _reconcile(self, spec_raw: dict[str, Any], st: dict[str, Any] | None) -> GridEntry:
        symbol = spec_raw["symbol"]
        spec_base = spec_raw["base_price"]
        spec_paused = spec_raw["paused"]
        self._spec_paused[symbol] = spec_paused

        if st is None:
            # First run for this symbol. Tolerate a legacy combined grid.yaml by seeding
            # runtime from any state/active_order still present in the spec entry.
            base_price = spec_base
            grid_state = GridState(str(spec_raw.get("state") or GridState.WAITING_TRIGGER.value))
            active = _active_order(spec_raw.get("active_order"))
            halted = False
            self._spec_base[symbol] = spec_base
        else:
            persisted_anchor = float(st.get("spec_base_price", spec_base))
            # Re-anchor is detected purely by the spec anchor changing vs. the last-seen
            # snapshot — the engine never diffs your edit, it just compares this one field.
            # Compare at the same rounding both sides so a >4-decimal anchor cannot be
            # mistaken for a constant re-anchor (which would stop evolution from sticking).
            if _round_price(spec_base) != _round_price(persisted_anchor):
                # You edited the spec anchor → manual re-anchor wins over engine evolution.
                base_price = spec_base
                self._spec_base[symbol] = spec_base
            else:
                # Anchor unchanged → keep the engine's evolved base_price; never clobber it.
                base_price = float(st.get("base_price", spec_base))
                self._spec_base[symbol] = persisted_anchor
            grid_state = GridState(str(st.get("state", GridState.WAITING_TRIGGER.value)))
            active = _active_order(st.get("active_order"))
            halted = bool(st.get("halted", False))

        return GridEntry(
            symbol=symbol,
            base_price=base_price,
            up_pct=spec_raw["up_pct"],
            down_pct=spec_raw["down_pct"],
            trade_amount=spec_raw["trade_amount"],
            state=grid_state,
            paused=spec_paused or halted,
            active_order=active,
        )

    # --- save -----------------------------------------------------------------
    def save(self, config: GridRuntimeConfig) -> None:
        self._atomic_write({
            "version": STATE_VERSION,
            "symbols": {grid.symbol: self._state_entry(grid) for grid in config.grids},
        })

    def _atomic_write(self, payload: dict[str, Any]) -> None:
        """Durably replace the status file: backup -> temp -> atomic rename.

        Shared by every writer of grid_state.json so a reader (the engine loop,
        another afctl invocation) can never observe a half-written file.
        """
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        if self.state_path.exists():
            shutil.copy2(self.state_path, self.state_path.with_suffix(self.state_path.suffix + ".bak"))
        tmp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2))
        tmp_path.replace(self.state_path)

    def _state_entry(self, grid: GridEntry) -> dict[str, Any]:
        spec_paused = self._spec_paused.get(grid.symbol, False)
        spec_base = self._spec_base.get(grid.symbol, grid.base_price)
        return {
            "base_price": _round_price(grid.base_price),
            "spec_base_price": _round_price(spec_base),
            "state": grid.state.value,
            # In-memory ``paused`` collapses user pause + engine halt. The user switch
            # lives in the spec, so anything paused-but-not-user-paused is an engine halt.
            "halted": bool(grid.paused and not spec_paused),
            "active_order": _active_order_dict(grid.active_order),
        }

    # --- resume ---------------------------------------------------------------
    def clear_halt(self, symbol: str | None = None) -> list[str]:
        """Clear engine-set halts in the status file. Returns the symbols resumed.

        NOTE: this writes the file from outside the engine's event loop. It is
        reliable when the halted symbol is idle (no order events -> the engine
        does not re-save and clobber the cleared flag before its next reload).
        While other symbols are actively filling, the engine may re-affirm
        ``halted`` on its next save and overwrite this. The robust fix is a
        resume control-channel the engine consumes itself rather than poking the
        file underneath it; until then, resume a busy grid when it is quiet or
        restart the engine. See README "恢复被引擎暂停的标的".
        """
        state = self._load_state()
        symbols = state.get("symbols", {})
        target = symbol.upper().strip() if symbol else None
        cleared: list[str] = []
        for sym, st in symbols.items():
            if target and sym != target:
                continue
            if st.get("halted"):
                st["halted"] = False
                cleared.append(sym)
        if cleared:
            self._atomic_write(state)
        return cleared

    # --- io -------------------------------------------------------------------
    def _load_spec(self) -> dict[str, Any]:
        if not self.spec_path.exists():
            raise ConfigError(f"grid spec not found: {self.spec_path}")
        raw = yaml.safe_load(self.spec_path.read_text()) or {}
        if not isinstance(raw, dict):
            raise ConfigError("grid.yaml must contain a mapping")
        window_raw = _mapping(raw, "trading_window")
        grids_raw = raw.get("grids", [])
        if not isinstance(grids_raw, list) or not grids_raw:
            raise ConfigError("grid.yaml grids must be a non-empty list")
        return {
            "strategy_name": str(raw.get("strategy_name", "grid_v1")),
            "audit_log_sample_rate": float(raw.get("audit_log_sample_rate", 0.01)),
            "trading_window": TradingWindow(
                timezone=str(window_raw.get("timezone", "America/New_York")),
                start=str(window_raw.get("start", "04:00")),
                end=str(window_raw.get("end", "20:00")),
                outside_rth=bool(window_raw.get("outside_rth", True)),
            ),
            "grids": [_spec_grid(item) for item in grids_raw],
        }

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            data = json.loads(self.state_path.read_text())
        except json.JSONDecodeError as exc:
            raise ConfigError(f"grid state is corrupt: {self.state_path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError("grid_state.json must contain an object")
        return data


def _spec_grid(raw: Any) -> dict[str, Any]:
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
    return {
        "symbol": symbol,
        "base_price": base_price,
        "up_pct": up_pct,
        "down_pct": down_pct,
        "trade_amount": trade_amount,
        "paused": bool(raw.get("paused", False)),
        # Legacy passthrough: only consulted on first run when no status file exists yet.
        "state": raw.get("state"),
        "active_order": raw.get("active_order"),
    }


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
