from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from alphaforge.alerts import WATCHDOG_STALE_SECONDS, Notifier, watchdog_decision
from alphaforge.logging.event_log import EventLogger


class _RecordingNotifier:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def notify(self, title: str, body: str = "") -> None:
        self.messages.append((title, body))


class NotifierTest(unittest.TestCase):
    def test_enabled_requires_sendkey(self) -> None:
        self.assertFalse(Notifier("").enabled)
        self.assertTrue(Notifier("SCTxxxx").enabled)

    def test_disabled_send_and_notify_are_noops(self) -> None:
        self.assertFalse(Notifier("").send("title", "body"))
        Notifier("").notify("title", "body")  # must not raise / must not spawn


class WatchdogDecisionTest(unittest.TestCase):
    def test_healthy_to_stale_pages_once(self) -> None:
        healthy, msg = watchdog_decision(WATCHDOG_STALE_SECONDS + 10, was_healthy=True)
        self.assertFalse(healthy)
        self.assertIsNotNone(msg)
        # Already unhealthy on the next poll -> no repeat page.
        healthy2, msg2 = watchdog_decision(WATCHDOG_STALE_SECONDS + 20, was_healthy=False)
        self.assertFalse(healthy2)
        self.assertIsNone(msg2)

    def test_missing_heartbeat_is_unhealthy(self) -> None:
        healthy, msg = watchdog_decision(None, was_healthy=True)
        self.assertFalse(healthy)
        self.assertIn("无心跳文件", msg)

    def test_recovery_pages_once(self) -> None:
        healthy, msg = watchdog_decision(5.0, was_healthy=False)
        self.assertTrue(healthy)
        self.assertIsNotNone(msg)
        healthy2, msg2 = watchdog_decision(5.0, was_healthy=True)
        self.assertTrue(healthy2)
        self.assertIsNone(msg2)

    def test_fresh_stays_quiet(self) -> None:
        healthy, msg = watchdog_decision(5.0, was_healthy=True)
        self.assertTrue(healthy)
        self.assertIsNone(msg)


class EventLoggerAlertTest(unittest.TestCase):
    def test_critical_event_fires_alert(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            notifier = _RecordingNotifier()
            log = _logger(Path(tmp), notifier)
            log.trade("order_rejected", "TSLA", reason="insufficient buying power")
            self.assertEqual(len(notifier.messages), 1)
            title, body = notifier.messages[0]
            self.assertIn("订单被拒", title)
            self.assertIn("TSLA", body)
            self.assertIn("insufficient buying power", body)

    def test_non_critical_event_does_not_alert(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            notifier = _RecordingNotifier()
            log = _logger(Path(tmp), notifier)
            log.trade("order_submitted", "TSLA")
            self.assertEqual(notifier.messages, [])

    def test_no_notifier_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = _logger(Path(tmp), None)
            log.trade("order_rejected", "TSLA", reason="x")  # must not raise


def _logger(root: Path, notifier: object) -> EventLogger:
    return EventLogger(
        root / "audit.jsonl",
        root / "trade.jsonl",
        "grid_v1",
        "paper",
        "DU123",
        1.0,
        notifier=notifier,
    )


if __name__ == "__main__":
    unittest.main()
