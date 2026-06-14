from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import yaml

from alphaforge.core.models import GridState, Side
from alphaforge.state.grid_store import GridStateStore


class GridStateStoreTest(unittest.TestCase):
    def test_load_merges_spec_into_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(Path(tmp))
            config = store.load()

        self.assertEqual(config.audit_log_sample_rate, 0.01)
        grid = config.grids[0]
        self.assertEqual(grid.base_price, 400.0)
        self.assertEqual(grid.up_pct, 0.30)
        self.assertEqual(grid.state, GridState.WAITING_TRIGGER)
        self.assertIsNone(grid.active_order)
        self.assertFalse(grid.paused)

    def test_save_writes_state_json_and_leaves_spec_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = _store(root)
            spec_before = (root / "grid.yaml").read_text()

            store.save(store.load())

            state = json.loads((root / "grid_state.json").read_text())
            self.assertEqual(state["symbols"]["TSLA"]["base_price"], 400.0)
            self.assertEqual(state["symbols"]["TSLA"]["spec_base_price"], 400.0)
            self.assertEqual(state["symbols"]["TSLA"]["state"], "WAITING_TRIGGER")
            self.assertFalse(state["symbols"]["TSLA"]["halted"])
            self.assertIsNone(state["symbols"]["TSLA"]["active_order"])
            # Spec is never rewritten by the engine.
            self.assertEqual((root / "grid.yaml").read_text(), spec_before)

    def test_engine_evolved_base_price_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = _store(root)
            config = store.load()
            config.grids[0].base_price = 412.5  # engine self-adapts after a fill
            store.save(config)

            reloaded = _open(root).load()

        # Spec anchor unchanged (400) → engine's evolved value (412.5) is kept.
        self.assertEqual(reloaded.grids[0].base_price, 412.5)

    def test_editing_other_params_keeps_evolved_base_price(self) -> None:
        # The user's concern: editing up_pct must NOT reset the running symbol's
        # evolved base_price. The engine only re-anchors when the anchor itself changes.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = _store(root)
            config = store.load()
            config.grids[0].base_price = 412.5  # engine evolved after a fill
            store.save(config)

            _edit_spec(root, up_pct=0.40)  # change a different param, leave base_price=400
            reloaded = _open(root).load()

        self.assertEqual(reloaded.grids[0].up_pct, 0.40)  # new param picked up
        self.assertEqual(reloaded.grids[0].base_price, 412.5)  # evolved value preserved

    def test_manual_reanchor_in_spec_overrides_evolved_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = _store(root)
            config = store.load()
            config.grids[0].base_price = 412.5
            store.save(config)

            _rewrite_base_price(root, 350.0)  # user edits the spec anchor
            store2 = _open(root)
            reloaded = store2.load()
            self.assertEqual(reloaded.grids[0].base_price, 350.0)

            store2.save(reloaded)
            state = json.loads((root / "grid_state.json").read_text())

        self.assertEqual(state["symbols"]["TSLA"]["spec_base_price"], 350.0)

    def test_engine_halt_persists_and_clear_halt_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = _store(root)
            config = store.load()
            config.grids[0].paused = True  # engine auto-halt on rejection
            store.save(config)

            state = json.loads((root / "grid_state.json").read_text())
            self.assertTrue(state["symbols"]["TSLA"]["halted"])
            self.assertTrue(_open(root).load().grids[0].paused)

            cleared = _open(root).clear_halt()
            self.assertEqual(cleared, ["TSLA"])
            self.assertFalse(_open(root).load().grids[0].paused)

    def test_user_pause_in_spec_is_not_an_engine_halt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _store(root, paused=True)  # writes spec with paused: true
            store = _open(root)
            config = store.load()
            self.assertTrue(config.grids[0].paused)

            store.save(config)
            state = json.loads((root / "grid_state.json").read_text())

        # User pause lives in the spec; status must not double-count it as a halt.
        self.assertFalse(state["symbols"]["TSLA"]["halted"])

    def test_migrates_legacy_combined_grid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Legacy grid.yaml that still carries runtime fields; no state file yet.
            (root / "grid.yaml").write_text(
                """
strategy_name: grid_v1
audit_log_sample_rate: 0.01
trading_window:
  timezone: America/New_York
  start: "04:00"
  end: "20:00"
  outside_rth: true
grids:
  - symbol: TSLA
    base_price: 410.0
    up_pct: 0.30
    down_pct: 0.25
    trade_amount: 1000
    state: WAITING_TRADE
    active_order:
      side: BUY
      base_price_at_submit: 400.0
      limit_price: 300.0
      quantity: 3
      status: Submitted
"""
            )
            grid = GridStateStore(root / "grid.yaml", root / "grid_state.json").load().grids[0]

        self.assertEqual(grid.base_price, 410.0)
        self.assertEqual(grid.state, GridState.WAITING_TRADE)
        self.assertIsNotNone(grid.active_order)
        self.assertEqual(grid.active_order.side, Side.BUY)
        self.assertEqual(grid.active_order.quantity, 3)


def _store(root: Path, paused: bool = False) -> GridStateStore:
    spec = root / "grid.yaml"
    spec.write_text(
        f"""
strategy_name: grid_v1
audit_log_sample_rate: 0.01
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
    paused: {str(paused).lower()}
"""
    )
    return GridStateStore(spec, root / "grid_state.json")


def _open(root: Path) -> GridStateStore:
    # Reopen existing files without rewriting the spec (proves cross-instance persistence).
    return GridStateStore(root / "grid.yaml", root / "grid_state.json")


def _rewrite_base_price(root: Path, base_price: float) -> None:
    _edit_spec(root, base_price=base_price)


def _edit_spec(root: Path, **fields: float) -> None:
    spec_path = root / "grid.yaml"
    spec = yaml.safe_load(spec_path.read_text())
    spec["grids"][0].update(fields)
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))


if __name__ == "__main__":
    unittest.main()
