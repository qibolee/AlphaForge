from __future__ import annotations

import unittest
from datetime import datetime, timezone

from alphaforge.core.models import (
    GridEntry,
    GridState,
    Portfolio,
    Position,
    Quote,
    Side,
    TradingWindow,
)
from alphaforge.strategies.grid_v1 import GridStrategy


class StrategyTest(unittest.TestCase):
    def test_buy_trigger_uses_tolerance_but_places_full_grid_price(self) -> None:
        strategy = _strategy()
        grid = GridEntry("TSLA", 250.0, up_pct=0.05, down_pct=0.05, trade_amount=1000)
        portfolio = Portfolio("DU123", 100_000, 100_000)

        result = strategy.evaluate(grid, _quote(237.62), portfolio)

        self.assertIsNotNone(result.trigger)
        self.assertEqual(result.trigger.side, Side.BUY)
        self.assertEqual(result.trigger.limit_price, 237.5)
        self.assertEqual(result.trigger.quantity, 4)

    def test_sell_trigger_does_not_exceed_position(self) -> None:
        strategy = _strategy()
        grid = GridEntry("TSLA", 250.0, up_pct=0.05, down_pct=0.05, trade_amount=1000)
        portfolio = Portfolio(
            "DU123",
            100_000,
            100_000,
            positions={"TSLA": Position("TSLA", 2, 200, 260)},
        )

        result = strategy.evaluate(grid, _quote(262.38), portfolio)

        self.assertIsNotNone(result.trigger)
        self.assertEqual(result.trigger.side, Side.SELL)
        self.assertEqual(result.trigger.limit_price, 262.5)
        self.assertEqual(result.trigger.quantity, 2)

    def test_waiting_trade_requests_cancel_when_price_returns_to_base(self) -> None:
        strategy = _strategy()
        grid = GridEntry("TSLA", 250.0, up_pct=0.05, down_pct=0.05, trade_amount=1000)
        grid.state = GridState.WAITING_TRADE
        grid.active_order = _active_buy_order()

        result = strategy.evaluate(grid, _quote(250.0), Portfolio("DU123", 100_000, 100_000))

        self.assertTrue(result.cancel_active_order)


def _strategy() -> GridStrategy:
    return GridStrategy(TradingWindow("America/New_York", "04:00", "20:00", True))


def _quote(price: float) -> Quote:
    return Quote(
        "TSLA",
        bid=price - 0.01,
        ask=price + 0.01,
        last=price,
        timestamp=datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc),
    )


def _active_buy_order():
    from alphaforge.core.models import ActiveOrder

    return ActiveOrder(
        side=Side.BUY,
        base_price_at_submit=250.0,
        limit_price=237.5,
        quantity=4,
        filled_quantity=0,
        remaining_quantity=4,
        order_id=1,
        perm_id=None,
        order_ref="test",
        status="Submitted",
        submitted_at=datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc),
    )


if __name__ == "__main__":
    unittest.main()
