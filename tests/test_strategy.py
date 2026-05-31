from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from alphaforge.config import StrategyConfig
from alphaforge.models import Bar, Portfolio, SignalAction
from alphaforge.strategy import MomentumStrategy


class StrategyTest(unittest.TestCase):
    def test_momentum_buy_signal(self) -> None:
        strategy = MomentumStrategy(
            StrategyConfig(
                universe=("SPY",),
                bar_seconds=60,
                cooldown_seconds=300,
                short_ema=3,
                long_ema=12,
            )
        )
        portfolio = Portfolio("DU123", 100_000, 100_000)
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        signals = []

        for index in range(15):
            price = 100 + index
            signals.extend(
                strategy.on_bar(
                    Bar(
                        symbol="SPY",
                        timestamp=now + timedelta(minutes=index),
                        open=price - 0.5,
                        high=price + 1,
                        low=price - 1,
                        close=price,
                    ),
                    portfolio,
                )
            )

        self.assertTrue(any(signal.action == SignalAction.BUY for signal in signals))


if __name__ == "__main__":
    unittest.main()

