from __future__ import annotations

import asyncio
import unittest

from alphaforge.engine import _is_recoverable_session_error, _retry_delay_seconds


class EngineRetryTest(unittest.TestCase):
    def test_retry_delay_is_capped(self) -> None:
        self.assertEqual(_retry_delay_seconds(0), 30)
        self.assertEqual(_retry_delay_seconds(1), 30)
        self.assertEqual(_retry_delay_seconds(2), 60)
        self.assertEqual(_retry_delay_seconds(10), 300)
        self.assertEqual(_retry_delay_seconds(20), 300)

    def test_recoverable_session_errors(self) -> None:
        self.assertTrue(_is_recoverable_session_error(TimeoutError("portfolio request timed out")))
        self.assertTrue(_is_recoverable_session_error(asyncio.TimeoutError()))
        self.assertTrue(_is_recoverable_session_error(ConnectionError("connection reset")))
        self.assertTrue(_is_recoverable_session_error(OSError("network unavailable")))
        self.assertTrue(_is_recoverable_session_error(RuntimeError("open orders request timed out")))
        self.assertTrue(_is_recoverable_session_error(RuntimeError("IBKR client is not connected")))

    def test_non_recoverable_session_errors(self) -> None:
        self.assertFalse(_is_recoverable_session_error(ValueError("bad config")))
        self.assertFalse(_is_recoverable_session_error(RuntimeError("IB_ACCOUNT is wrong")))


if __name__ == "__main__":
    unittest.main()
