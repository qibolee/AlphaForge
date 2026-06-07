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


class GridState(str, Enum):
    WAITING_TRIGGER = "WAITING_TRIGGER"
    WAITING_TRADE = "WAITING_TRADE"


class OrderEventType(str, Enum):
    OPEN_ORDER = "open_order"
    ORDER_STATUS = "order_status"
    EXECUTION = "execution"
    COMMISSION = "commission"


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
        if self.bid is not None and self.ask is not None and self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.last

    @property
    def spread_bps(self) -> float | None:
        mid = self.mid
        if self.bid is None or self.ask is None or mid is None or mid <= 0:
            return None
        return (self.ask - self.bid) / mid * 10_000


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
class OrderIntent:
    symbol: str
    side: Side
    quantity: int
    account_id: str
    limit_price: float
    reason: str
    order_ref: str = ""
    tif: str = "DAY"
    good_till_date: datetime | None = None
    good_till_timezone: str = "America/New_York"
    outside_rth: bool = False

    @property
    def notional(self) -> float:
        return abs(self.quantity * self.limit_price)


@dataclass(frozen=True)
class BrokerOrderRef:
    order_id: int
    perm_id: int | None
    order_ref: str


@dataclass
class ActiveOrder:
    side: Side
    base_price_at_submit: float
    limit_price: float
    quantity: int
    filled_quantity: float
    remaining_quantity: float
    order_id: int | None
    perm_id: int | None
    order_ref: str
    status: str
    submitted_at: datetime
    cancel_requested: bool = False
    execution_filled_quantity: float = 0.0
    seen_exec_ids: list[str] = field(default_factory=list)

    @property
    def fill_ratio(self) -> float:
        if self.quantity <= 0:
            return 0.0
        return min(max(self.filled_quantity / self.quantity, 0.0), 1.0)


@dataclass
class GridEntry:
    symbol: str
    base_price: float
    up_pct: float
    down_pct: float
    trade_amount: float
    state: GridState = GridState.WAITING_TRIGGER
    paused: bool = False
    active_order: ActiveOrder | None = None


@dataclass(frozen=True)
class TradingWindow:
    timezone: str
    start: str
    end: str
    outside_rth: bool


@dataclass
class GridRuntimeConfig:
    strategy_name: str
    audit_log_sample_rate: float
    trading_window: TradingWindow
    grids: list[GridEntry]

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(grid.symbol for grid in self.grids if not grid.paused)

    def grid_for(self, symbol: str) -> GridEntry | None:
        symbol = symbol.upper()
        for grid in self.grids:
            if grid.symbol == symbol:
                return grid
        return None


@dataclass(frozen=True)
class OrderEvent:
    event_type: OrderEventType
    order_id: int | None
    perm_id: int | None
    order_ref: str
    status: str | None = None
    filled: float | None = None
    remaining: float | None = None
    avg_fill_price: float | None = None
    last_fill_price: float | None = None
    exec_id: str | None = None
    commission: float | None = None
    currency: str | None = None
    timestamp: datetime = field(default_factory=utc_now)
