from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class Mode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class SignalAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: float | None
    ask: float | None
    last: float | None
    timestamp: datetime = field(default_factory=utc_now)

    @property
    def mid(self) -> float | None:
        if self.bid and self.ask and self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.last

    @property
    def spread_bps(self) -> float | None:
        mid = self.mid
        if self.bid is None or self.ask is None or mid is None or mid <= 0:
            return None
        return (self.ask - self.bid) / mid * 10_000


@dataclass(frozen=True)
class Bar:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def typical_price(self) -> float:
        return (self.high + self.low + self.close) / 3


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: float
    average_cost: float
    market_price: float

    @property
    def market_value(self) -> float:
        return self.quantity * self.market_price


@dataclass
class Portfolio:
    account_id: str
    net_liquidation: float
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    day_pnl: float = 0.0

    @property
    def gross_exposure(self) -> float:
        return sum(abs(position.market_value) for position in self.positions.values())

    @property
    def position_count(self) -> int:
        return sum(1 for position in self.positions.values() if abs(position.quantity) > 0)

    def position(self, symbol: str) -> Position | None:
        return self.positions.get(symbol)


@dataclass(frozen=True)
class Signal:
    symbol: str
    action: SignalAction
    reason: str
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: Side
    quantity: int
    account_id: str
    limit_price: float
    reason: str

    @property
    def notional(self) -> float:
        return abs(self.quantity * self.limit_price)

