from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# The heartbeat is timer-driven (not quote-driven), so it stays fresh even when
# the market is quiet — proving the *process/event-loop* is alive rather than
# "quotes are flowing". Quote flow is reported separately as last_quote_age.
HEARTBEAT_INTERVAL_SECONDS = 15
# Healthy if the last beat is younger than this. Generously larger than a few
# intervals so one slow write or GC pause never flaps the container health.
HEARTBEAT_MAX_AGE_SECONDS = 60


@dataclass
class LivenessState:
    """Liveness shared between run_forever, the session loop, and the heartbeat
    writer. Mutated only from the single asyncio thread, so no locking needed."""

    session_phase: str = "starting"  # starting | running | retrying | ended | failed
    connected: bool = False
    retry_attempt: int = 0
    symbols: list[str] = field(default_factory=list)
    last_quote_at: datetime | None = None

    def snapshot(self, now: datetime | None = None) -> dict[str, object]:
        now = now or datetime.now(timezone.utc)
        last_quote_age = (
            round((now - self.last_quote_at).total_seconds(), 1)
            if self.last_quote_at is not None
            else None
        )
        return {
            "ts": now.isoformat(),
            "pid": os.getpid(),
            "session_phase": self.session_phase,
            "connected": self.connected,
            "retry_attempt": self.retry_attempt,
            "symbols": list(self.symbols),
            "last_quote_at": self.last_quote_at.isoformat() if self.last_quote_at else None,
            "last_quote_age_seconds": last_quote_age,
        }


def write_heartbeat(path: Path, liveness: LivenessState) -> None:
    """Overwrite the heartbeat file atomically (temp + rename) so a concurrent
    reader (healthz / afctl status) never observes a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(liveness.snapshot(), indent=2))
    tmp.replace(path)


def heartbeat_age_seconds(path: Path, now: datetime | None = None) -> float | None:
    """Age of the last heartbeat in seconds, or None if missing/unreadable."""
    try:
        data = json.loads(path.read_text())
        ts = datetime.fromisoformat(str(data["ts"]))
    except (OSError, ValueError, KeyError):
        return None
    now = now or datetime.now(timezone.utc)
    return (now - ts).total_seconds()
