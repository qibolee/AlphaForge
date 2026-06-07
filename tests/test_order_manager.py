from __future__ import annotations

import tempfile
import unittest
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from alphaforge.logging.event_log import EventLogger
from alphaforge.state.grid_store import GridStateStore
from alphaforge.core.config import RiskConfig
from alphaforge.core.models import (
    ActiveOrder,
    BrokerOrderRef,
    GridEntry,
    GridRuntimeConfig,
    GridState,
    OrderEvent,
    OrderEventType,
    Portfolio,
    Quote,
    Side,
    TradingWindow,
)
from alphaforge.execution.order_manager import OrderManager
from alphaforge.execution.risk import RiskManager
from alphaforge.strategies.grid_v1 import GridStrategy, GridTrigger


class OrderManagerTest(unittest.TestCase):
    def test_partial_cancel_adjusts_base_price_by_fill_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, manager = _manager(Path(tmp), filled_quantity=0)

            manager.apply_order_event(
                config,
                OrderEvent(
                    OrderEventType.ORDER_STATUS,
                    order_id=1,
                    perm_id=None,
                    order_ref="test",
                    status="Cancelled",
                    filled=2,
                    remaining=2,
                ),
            )

            grid = config.grids[0]
            self.assertEqual(grid.state, GridState.WAITING_TRIGGER)
            self.assertIsNone(grid.active_order)
            self.assertEqual(grid.base_price, 243.75)

    def test_zero_fill_cancel_keeps_base_price(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, manager = _manager(Path(tmp), filled_quantity=0)

            manager.apply_order_event(
                config,
                OrderEvent(
                    OrderEventType.ORDER_STATUS,
                    order_id=1,
                    perm_id=None,
                    order_ref="test",
                    status="Cancelled",
                    filled=0,
                    remaining=4,
                ),
            )

            self.assertEqual(config.grids[0].base_price, 250.0)

    def test_execution_events_are_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, manager = _manager(Path(tmp), filled_quantity=0)
            event = OrderEvent(
                OrderEventType.EXECUTION,
                order_id=1,
                perm_id=None,
                order_ref="test",
                status="Execution",
                filled=1,
                remaining=None,
                exec_id="exec-1",
            )

            manager.apply_order_event(config, event)
            manager.apply_order_event(config, event)

            active = config.grids[0].active_order
            self.assertIsNotNone(active)
            self.assertEqual(active.filled_quantity, 1)
            self.assertEqual(active.execution_filled_quantity, 1)

    def test_submit_persists_submitting_state_before_broker_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, manager, client, store = _submit_manager(Path(tmp))

            ok = asyncio.run(
                manager.submit_trigger(
                    config,
                    config.grids[0],
                    GridTrigger("TSLA", Side.BUY, 237.5, 4, "test_trigger"),
                    _quote(237.62),
                    Portfolio("DU123", 100_000, 100_000),
                )
            )

            self.assertTrue(ok)
            self.assertIsNotNone(client.snapshot)
            self.assertEqual(client.snapshot.status, "SUBMITTING")
            self.assertIsNone(client.snapshot.order_id)

            final_active = store.load().grids[0].active_order
            self.assertIsNotNone(final_active)
            self.assertEqual(final_active.order_id, 123)
            self.assertEqual(final_active.perm_id, 456)
            self.assertEqual(final_active.status, "Submitted")

    def test_submit_rolls_back_state_when_broker_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, manager, _client, store = _submit_manager(Path(tmp), reject=True)

            ok = asyncio.run(
                manager.submit_trigger(
                    config,
                    config.grids[0],
                    GridTrigger("TSLA", Side.BUY, 237.5, 4, "test_trigger"),
                    _quote(237.62),
                    Portfolio("DU123", 100_000, 100_000),
                )
            )

            saved_grid = store.load().grids[0]
            self.assertFalse(ok)
            self.assertEqual(saved_grid.state, GridState.WAITING_TRIGGER)
            self.assertIsNone(saved_grid.active_order)


def _manager(tmp: Path, filled_quantity: float) -> tuple[GridRuntimeConfig, OrderManager]:
    config = GridRuntimeConfig(
        strategy_name="grid_v1",
        audit_log_sample_rate=1,
        trading_window=TradingWindow("America/New_York", "04:00", "20:00", True),
        grids=[
            GridEntry(
                symbol="TSLA",
                base_price=250.0,
                up_pct=0.05,
                down_pct=0.05,
                trade_amount=1000,
                state=GridState.WAITING_TRADE,
                active_order=ActiveOrder(
                    side=Side.BUY,
                    base_price_at_submit=250.0,
                    limit_price=237.5,
                    quantity=4,
                    filled_quantity=filled_quantity,
                    remaining_quantity=4 - filled_quantity,
                    order_id=1,
                    perm_id=None,
                    order_ref="test",
                    status="Submitted",
                    submitted_at=datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc),
                ),
            )
        ],
    )
    store = GridStateStore(tmp / "grid.yaml")
    logger = EventLogger(
        tmp / "audit.jsonl",
        tmp / "trade.jsonl",
        "grid_v1",
        "paper",
        "DU123",
        1,
    )
    risk = RiskManager(
        RiskConfig(
            max_positions=3,
            max_symbol_position_pct=0.10,
            max_gross_exposure_pct=0.30,
            daily_loss_limit_pct=0.01,
            max_spread_bps=5,
        ),
        tmp / "kill-switch",
    )
    return config, OrderManager(
        _FakeClient(),
        store,
        risk,
        logger,
        "DU123",
        GridStrategy(config.trading_window),
    )


def _submit_manager(
    tmp: Path,
    reject: bool = False,
) -> tuple[GridRuntimeConfig, OrderManager, "_SubmittingClient", GridStateStore]:
    config = GridRuntimeConfig(
        strategy_name="grid_v1",
        audit_log_sample_rate=1,
        trading_window=TradingWindow("America/New_York", "04:00", "20:00", True),
        grids=[
            GridEntry(
                symbol="TSLA",
                base_price=250.0,
                up_pct=0.05,
                down_pct=0.05,
                trade_amount=1000,
            )
        ],
    )
    store = GridStateStore(tmp / "grid.yaml")
    logger = EventLogger(
        tmp / "audit.jsonl",
        tmp / "trade.jsonl",
        "grid_v1",
        "paper",
        "DU123",
        1,
    )
    risk = RiskManager(
        RiskConfig(
            max_positions=3,
            max_symbol_position_pct=0.10,
            max_gross_exposure_pct=0.30,
            daily_loss_limit_pct=0.01,
            max_spread_bps=5,
        ),
        tmp / "kill-switch",
    )
    client = _SubmittingClient(store, reject=reject)
    manager = OrderManager(
        client,
        store,
        risk,
        logger,
        "DU123",
        GridStrategy(config.trading_window),
    )
    return config, manager, client, store


def _quote(price: float) -> Quote:
    return Quote(
        "TSLA",
        bid=price - 0.01,
        ask=price + 0.01,
        last=price,
        timestamp=datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc),
    )


class _FakeClient:
    async def reconcile_order_events(self):
        return []

    def get_order_event_nowait(self):
        return None


class _SubmittingClient:
    def __init__(self, store: GridStateStore, reject: bool) -> None:
        self.store = store
        self.reject = reject
        self.snapshot: ActiveOrder | None = None

    async def place_limit_order(self, intent):
        self.snapshot = self.store.load().grids[0].active_order
        if self.reject:
            raise RuntimeError("broker rejected")
        return BrokerOrderRef(123, 456, intent.order_ref)

    async def reconcile_order_events(self):
        return []

    def get_order_event_nowait(self):
        return None


if __name__ == "__main__":
    unittest.main()
