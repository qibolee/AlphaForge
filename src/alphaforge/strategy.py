from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone

from alphaforge.config import StrategyConfig
from alphaforge.models import Bar, Portfolio, Quote, Signal, SignalAction


@dataclass
class _PartialBar:
    bucket: int
    open: float
    high: float
    low: float
    close: float

    def update(self, price: float) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price

    def finish(self, symbol: str) -> Bar:
        return Bar(
            symbol=symbol,
            timestamp=datetime.fromtimestamp(self.bucket, tz=timezone.utc),
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
        )


class BarAggregator:
    def __init__(self, interval_seconds: int) -> None:
        self.interval_seconds = interval_seconds
        self._bars: dict[str, _PartialBar] = {}

    def update(self, quote: Quote) -> Bar | None:
        price = quote.mid
        if price is None or price <= 0:
            return None
        bucket = int(quote.timestamp.timestamp()) // self.interval_seconds * self.interval_seconds
        current = self._bars.get(quote.symbol)
        if current is None:
            self._bars[quote.symbol] = _PartialBar(bucket, price, price, price, price)
            return None
        if current.bucket == bucket:
            current.update(price)
            return None
        completed = current.finish(quote.symbol)
        self._bars[quote.symbol] = _PartialBar(bucket, price, price, price, price)
        return completed


class MomentumStrategy:
    def __init__(self, config: StrategyConfig) -> None:
        self.config = config
        maxlen = max(config.long_ema * 4, 32)
        self._history: dict[str, deque[Bar]] = defaultdict(lambda: deque(maxlen=maxlen))

    def on_bar(self, bar: Bar, portfolio: Portfolio) -> list[Signal]:
        history = self._history[bar.symbol]
        history.append(bar)
        if len(history) < self.config.long_ema:
            return []

        closes = [item.close for item in history]
        short = _ema(closes[-self.config.short_ema :], self.config.short_ema)
        long = _ema(closes[-self.config.long_ema :], self.config.long_ema)
        vwap = _simple_vwap(list(history)[-self.config.long_ema :])
        position = portfolio.position(bar.symbol)
        quantity = position.quantity if position else 0

        if quantity <= 0 and bar.close > vwap and short > long:
            return [Signal(bar.symbol, SignalAction.BUY, "price_above_vwap_and_short_ema_above_long")]

        if quantity > 0 and (bar.close < vwap or short < long):
            return [Signal(bar.symbol, SignalAction.SELL, "ema_or_vwap_exit")]

        return []


def _ema(values: list[float], period: int) -> float:
    alpha = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


def _simple_vwap(bars: list[Bar]) -> float:
    # IB delayed quotes often do not include usable volume in the first version.
    return sum(bar.typical_price for bar in bars) / len(bars)

