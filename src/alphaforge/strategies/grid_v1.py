from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from math import floor
from zoneinfo import ZoneInfo

from alphaforge.core.models import GridEntry, GridState, Portfolio, Quote, Side, TradingWindow

TRIGGER_TOLERANCE = 0.99


@dataclass(frozen=True)
class GridTrigger:
    symbol: str
    side: Side
    limit_price: float
    quantity: int
    reason: str


@dataclass(frozen=True)
class GridEvaluation:
    trigger: GridTrigger | None = None
    cancel_active_order: bool = False
    reason: str = ""


class GridStrategy:
    def __init__(self, window: TradingWindow) -> None:
        self.window = window

    def evaluate(self, grid: GridEntry, quote: Quote, portfolio: Portfolio) -> GridEvaluation:
        price = quote.mid
        if price is None or price <= 0:
            return GridEvaluation(reason="quote_invalid")
        if not self.is_in_trading_window(quote.timestamp):
            return GridEvaluation(reason="outside_trading_window")
        if grid.paused:
            return GridEvaluation(reason="grid_paused")

        if grid.state == GridState.WAITING_TRADE:
            return self._evaluate_waiting_trade(grid, price)
        if grid.state != GridState.WAITING_TRIGGER:
            return GridEvaluation(reason="unknown_grid_state")

        sell_trigger = grid.base_price * (1 + grid.up_pct * TRIGGER_TOLERANCE)
        buy_trigger = grid.base_price * (1 - grid.down_pct * TRIGGER_TOLERANCE)

        if price >= sell_trigger:
            limit_price = _round_limit(grid.base_price * (1 + grid.up_pct))
            position = portfolio.position(grid.symbol)
            current_quantity = floor(position.quantity) if position else 0
            quantity = min(floor(grid.trade_amount / limit_price), current_quantity)
            if quantity <= 0:
                return GridEvaluation(reason="sell_triggered_but_no_position")
            return GridEvaluation(
                trigger=GridTrigger(
                    grid.symbol,
                    Side.SELL,
                    limit_price,
                    quantity,
                    "price_above_grid_sell_trigger",
                )
            )

        if price <= buy_trigger:
            limit_price = _round_limit(grid.base_price * (1 - grid.down_pct))
            quantity = floor(grid.trade_amount / limit_price)
            if quantity <= 0:
                return GridEvaluation(reason="buy_triggered_but_quantity_is_zero")
            return GridEvaluation(
                trigger=GridTrigger(
                    grid.symbol,
                    Side.BUY,
                    limit_price,
                    quantity,
                    "price_below_grid_buy_trigger",
                )
            )

        return GridEvaluation(reason="quote_no_trigger")

    def is_in_trading_window(self, timestamp: datetime) -> bool:
        tz = ZoneInfo(self.window.timezone)
        local = timestamp.astimezone(tz)
        start = _parse_time(self.window.start)
        end = _parse_time(self.window.end)
        current = local.time()
        if start <= end:
            return start <= current <= end
        return current >= start or current <= end

    def good_till_date(self, timestamp: datetime) -> datetime:
        return timestamp.astimezone(ZoneInfo(self.window.timezone)) + timedelta(days=7)

    def _evaluate_waiting_trade(self, grid: GridEntry, price: float) -> GridEvaluation:
        active = grid.active_order
        if active is None:
            return GridEvaluation(reason="waiting_trade_without_active_order")
        if active.cancel_requested:
            return GridEvaluation(reason="waiting_cancel_confirmation")

        if active.side == Side.BUY and price >= active.base_price_at_submit:
            return GridEvaluation(
                cancel_active_order=True,
                reason="buy_order_price_returned_to_base",
            )
        if active.side == Side.SELL and price <= active.base_price_at_submit:
            return GridEvaluation(
                cancel_active_order=True,
                reason="sell_order_price_returned_to_base",
            )

        return GridEvaluation(reason="waiting_existing_order")


def _parse_time(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def _round_limit(value: float) -> float:
    return round(float(value), 2)
