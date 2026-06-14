from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alphaforge.state.heartbeat import (
    HEARTBEAT_MAX_AGE_SECONDS,
    LivenessState,
    heartbeat_age_seconds,
    write_heartbeat,
)


class HeartbeatTest(unittest.TestCase):
    def test_snapshot_reports_quote_age(self) -> None:
        now = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
        liveness = LivenessState(
            session_phase="running",
            connected=True,
            symbols=["TSLA"],
            last_quote_at=now - timedelta(seconds=20),
        )

        snap = liveness.snapshot(now=now)

        self.assertEqual(snap["session_phase"], "running")
        self.assertTrue(snap["connected"])
        self.assertEqual(snap["symbols"], ["TSLA"])
        self.assertEqual(snap["last_quote_age_seconds"], 20.0)

    def test_snapshot_handles_no_quote_yet(self) -> None:
        snap = LivenessState().snapshot()
        self.assertIsNone(snap["last_quote_age_seconds"])
        self.assertIsNone(snap["last_quote_at"])
        self.assertEqual(snap["last_error"], "")

    def test_snapshot_includes_last_error(self) -> None:
        snap = LivenessState(last_error="TimeoutError: API connection failed").snapshot()
        self.assertEqual(snap["last_error"], "TimeoutError: API connection failed")

    def test_write_then_age_is_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "heartbeat.json"
            write_heartbeat(path, LivenessState(session_phase="running"))

            age = heartbeat_age_seconds(path)

            self.assertIsNotNone(age)
            self.assertLess(age, HEARTBEAT_MAX_AGE_SECONDS)
            self.assertEqual(json.loads(path.read_text())["session_phase"], "running")

    def test_age_none_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(heartbeat_age_seconds(Path(tmp) / "nope.json"))

    def test_age_none_when_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "heartbeat.json"
            path.write_text("{ not json")
            self.assertIsNone(heartbeat_age_seconds(path))

    def test_stale_heartbeat_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "heartbeat.json"
            write_heartbeat(path, LivenessState())
            future = datetime.now(timezone.utc) + timedelta(seconds=HEARTBEAT_MAX_AGE_SECONDS + 30)
            age = heartbeat_age_seconds(path, now=future)
            self.assertGreater(age, HEARTBEAT_MAX_AGE_SECONDS)


if __name__ == "__main__":
    unittest.main()
