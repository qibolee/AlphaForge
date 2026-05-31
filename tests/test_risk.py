from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from alphaforge.config import RiskConfig
from alphaforge.models import OrderIntent, Portfolio, Quote, Side
from alphaforge.risk import RiskManager


class RiskTest(unittest.TestCase):
    def test_rejects_wide_spread(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            risk = RiskManager(_risk_config(), Path(tmp) / "kill-switch")
            decision = risk.check(
                OrderIntent("SPY", Side.BUY, 10, "DU123", 100.0, "test"),
                Portfolio("DU123", 100_000, 100_000),
                Quote("SPY", bid=99.0, ask=101.0, last=100.0),
            )

        self.assertFalse(decision.allowed)
        self.assertIn("spread", decision.reason)

    def test_allows_small_buy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            risk = RiskManager(_risk_config(), Path(tmp) / "kill-switch")
            decision = risk.check(
                OrderIntent("SPY", Side.BUY, 10, "DU123", 100.0, "test"),
                Portfolio("DU123", 100_000, 100_000),
                Quote("SPY", bid=99.99, ask=100.01, last=100.0),
            )

        self.assertTrue(decision.allowed)


def _risk_config() -> RiskConfig:
    return RiskConfig(
        max_positions=3,
        max_symbol_position_pct=0.10,
        max_gross_exposure_pct=0.30,
        daily_loss_limit_pct=0.01,
        max_spread_bps=5,
    )


if __name__ == "__main__":
    unittest.main()

