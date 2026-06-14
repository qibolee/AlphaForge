from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alphaforge.core.config import Settings

# Watchdog: a separate process/container watches the engine's heartbeat file and
# alerts on staleness. Stale threshold is deliberately more lenient than the
# container healthcheck (60s) so only a real outage — not a blip — pages you.
WATCHDOG_INTERVAL_SECONDS = 60
WATCHDOG_STALE_SECONDS = 120

# Server酱 (sct.ftqq.com) pushes a message to 微信 via a single SendKey.
_SERVERCHAN_URL = "https://sctapi.ftqq.com/{key}.send"
_TITLE_MAX = 32  # Server酱 caps the title length


class Notifier:
    """Server酱 notifier (pushes to 微信). Disabled (no-op) unless a SendKey is
    set, so an unconfigured deployment simply sends nothing. Never raises."""

    def __init__(self, sendkey: str, timeout: float = 5.0) -> None:
        self.sendkey = sendkey
        self.timeout = timeout

    @classmethod
    def from_settings(cls, settings: "Settings") -> "Notifier":
        return cls(settings.env.serverchan_sendkey)

    @property
    def enabled(self) -> bool:
        return bool(self.sendkey)

    def send(self, title: str, body: str = "") -> bool:
        """Blocking send; returns True only on Server酱 code==0. Swallows errors."""
        if not self.enabled:
            return False
        url = _SERVERCHAN_URL.format(key=self.sendkey)
        data = urllib.parse.urlencode({"title": title[:_TITLE_MAX], "desp": body}).encode()
        try:
            request = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return payload.get("code") == 0
        except Exception:
            return False

    def notify(self, title: str, body: str = "") -> None:
        """Fire-and-forget send for the trading path — never blocks the caller
        (a slow/timing-out push API must not stall the engine loop)."""
        if not self.enabled:
            return
        threading.Thread(target=self.send, args=(title, body), daemon=True).start()


def watchdog_decision(
    age_seconds: float | None,
    was_healthy: bool,
    stale_after: float = WATCHDOG_STALE_SECONDS,
) -> tuple[bool, str | None]:
    """Pure transition logic for the heartbeat watchdog. Returns the new health
    state and a message to send only on a health transition (None otherwise), so
    a sustained outage pages once rather than every poll."""
    healthy = age_seconds is not None and age_seconds < stale_after
    if not healthy and was_healthy:
        detail = "无心跳文件" if age_seconds is None else f"{age_seconds:.0f}s 未更新"
        return False, f"🔴 引擎心跳异常：{detail}，引擎可能已停止或卡住。"
    if healthy and not was_healthy:
        return True, f"🟢 引擎心跳恢复（{age_seconds:.0f}s）。"
    return healthy, None
