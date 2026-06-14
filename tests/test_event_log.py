from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from alphaforge.logging.event_log import EventLogger


class EventLogRotationTest(unittest.TestCase):
    def test_rotation_caps_file_count_and_drops_oldest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = _logger(root, max_bytes=400, backup_count=3)

            for i in range(50):
                log.trade("tick", "TSLA", i=i, note="x" * 40)

            names = sorted(p.name for p in root.glob("trade.jsonl*"))

        # Current file plus exactly backup_count backups; nothing beyond .3.
        self.assertEqual(names, ["trade.jsonl", "trade.jsonl.1", "trade.jsonl.2", "trade.jsonl.3"])

    def test_no_rotation_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = _logger(root, max_bytes=0, backup_count=3)  # rotation off

            for i in range(50):
                log.trade("tick", "TSLA", i=i, note="x" * 40)

            names = sorted(p.name for p in root.glob("trade.jsonl*"))

        self.assertEqual(names, ["trade.jsonl"])

    def test_backup_count_zero_truncates_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = _logger(root, max_bytes=400, backup_count=0)

            for i in range(50):
                log.trade("tick", "TSLA", i=i, note="x" * 40)

            names = sorted(p.name for p in root.glob("trade.jsonl*"))

        self.assertEqual(names, ["trade.jsonl"])  # truncated, never spawns backups


def _logger(root: Path, max_bytes: int, backup_count: int) -> EventLogger:
    return EventLogger(
        root / "audit.jsonl",
        root / "trade.jsonl",
        "grid_v1",
        "paper",
        "DU123",
        1.0,
        max_bytes=max_bytes,
        backup_count=backup_count,
    )


if __name__ == "__main__":
    unittest.main()
