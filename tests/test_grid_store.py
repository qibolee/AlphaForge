from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from alphaforge.state.grid_store import GridStateStore


class GridStateStoreTest(unittest.TestCase):
    def test_loads_audit_log_sample_rate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_grid(Path(tmp), "audit_log_sample_rate: 0.02")

            config = GridStateStore(path).load()

        self.assertEqual(config.audit_log_sample_rate, 0.02)

    def test_loads_legacy_regular_log_sample_rate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_grid(Path(tmp), "regular_log_sample_rate: 0.03")

            config = GridStateStore(path).load()

        self.assertEqual(config.audit_log_sample_rate, 0.03)

    def test_save_writes_audit_log_sample_rate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_grid(Path(tmp), "regular_log_sample_rate: 0.03")
            store = GridStateStore(path)

            config = store.load()
            store.save(config)
            saved = yaml.safe_load(path.read_text())

        self.assertEqual(saved["audit_log_sample_rate"], 0.03)
        self.assertNotIn("regular_log_sample_rate", saved)


def _write_grid(tmp: Path, sample_rate_line: str) -> Path:
    path = tmp / "grid.yaml"
    path.write_text(
        f"""
strategy_name: grid_v1
{sample_rate_line}
trading_window:
  timezone: America/New_York
  start: "04:00"
  end: "20:00"
  outside_rth: true
grids:
  - symbol: TSLA
    base_price: 400.00
    up_pct: 0.30
    down_pct: 0.25
    trade_amount: 1000
    state: WAITING_TRIGGER
    paused: false
    active_order: null
"""
    )
    return path


if __name__ == "__main__":
    unittest.main()
