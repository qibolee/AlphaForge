from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from alphaforge.config import ConfigError, load_settings
from alphaforge.models import Mode


class ConfigTest(unittest.TestCase):
    def test_loads_paper_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env, config = _write_files(Path(tmp), mode="paper")
            settings = load_settings(env, config)

        self.assertEqual(settings.env.mode, Mode.PAPER)
        self.assertEqual(settings.ibkr_port, 4002)
        self.assertEqual(settings.strategy.universe, ("SPY", "QQQ"))

    def test_live_requires_explicit_enable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env, config = _write_files(Path(tmp), mode="live", live_enabled=False)

            with self.assertRaises(ConfigError):
                load_settings(env, config)


def _write_files(tmp: Path, mode: str, live_enabled: bool = False) -> tuple[Path, Path]:
    env = tmp / "env"
    config = tmp / "config.yaml"
    env.write_text(
        "\n".join(
            [
                "IB_USERNAME=user",
                "IB_PASSWORD=pass",
                f"IB_MODE={mode}",
                "IB_ACCOUNT=DU123",
                f"LIVE_TRADING_ENABLED={'true' if live_enabled else 'false'}",
                "AUTO_RESTART_TIME=23:45",
            ]
        )
    )
    config.write_text(
        """
ibkr:
  host: 127.0.0.1
  paper_host_port: 4002
  live_host_port: 4001
  client_id: 10
  market_data_type: 3
paths:
  log_dir: /tmp/alphaforge-log
  state_dir: /tmp/alphaforge-state
  audit_log: /tmp/alphaforge-log/audit.jsonl
  kill_switch: /tmp/alphaforge-state/kill-switch
strategy:
  universe: [SPY, QQQ]
  bar_seconds: 60
  cooldown_seconds: 300
  short_ema: 3
  long_ema: 12
risk:
  max_positions: 3
  max_symbol_position_pct: 0.10
  max_gross_exposure_pct: 0.30
  daily_loss_limit_pct: 0.01
  max_spread_bps: 5
"""
    )
    return env, config


if __name__ == "__main__":
    unittest.main()

