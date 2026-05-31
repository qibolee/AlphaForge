from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from math import floor
from pathlib import Path

from alphaforge.config import Settings, load_settings
from alphaforge.ibkr import IBKRClient
from alphaforge.models import OrderIntent, Portfolio, Quote, Side, Signal, SignalAction
from alphaforge.risk import RiskManager
from alphaforge.strategy import BarAggregator, MomentumStrategy


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, **payload: object) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **{key: _jsonable(value) for key, value in payload.items()},
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


class TradingEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.audit = AuditLog(settings.paths.audit_log)
        self.client = IBKRClient(settings)
        self.aggregator = BarAggregator(settings.strategy.bar_seconds)
        self.strategy = MomentumStrategy(settings.strategy)
        self.risk = RiskManager(settings.risk, settings.paths.kill_switch)
        self._last_order_at: dict[str, datetime] = {}

    async def run(self) -> None:
        self.settings.paths.log_dir.mkdir(parents=True, exist_ok=True)
        self.settings.paths.state_dir.mkdir(parents=True, exist_ok=True)
        await self.client.connect()
        self.audit.write(
            "connected",
            mode=self.settings.env.mode.value,
            account=self.settings.env.account,
            port=self.settings.ibkr_port,
        )
        try:
            portfolio = await self.client.portfolio()
            async for quote in self.client.stream_quotes(self.settings.strategy.universe):
                bar = self.aggregator.update(quote)
                if bar is None:
                    continue
                portfolio = await self.client.portfolio()
                for signal in self.strategy.on_bar(bar, portfolio):
                    await self._handle_signal(signal, quote, portfolio)
        finally:
            self.client.disconnect()
            self.audit.write("disconnected")

    async def _handle_signal(self, signal: Signal, quote: Quote, portfolio: Portfolio) -> None:
        self.audit.write("signal", signal=signal)
        if not self._cooldown_ok(signal.symbol):
            self.audit.write("signal_skipped", symbol=signal.symbol, reason="cooldown")
            return
        intent = _order_from_signal(signal, quote, portfolio, self.settings)
        if intent is None:
            self.audit.write("signal_skipped", symbol=signal.symbol, reason="no_order_intent")
            return
        decision = self.risk.check(intent, portfolio, quote)
        if not decision.allowed:
            self.audit.write("risk_rejected", reason=decision.reason, intent=intent)
            return
        broker_order_id = await self.client.place_limit_order(intent)
        self._last_order_at[intent.symbol] = datetime.now(timezone.utc)
        self.audit.write("order_submitted", broker_order_id=broker_order_id, intent=intent)

    def _cooldown_ok(self, symbol: str) -> bool:
        last = self._last_order_at.get(symbol)
        if last is None:
            return True
        age = (datetime.now(timezone.utc) - last).total_seconds()
        return age >= self.settings.strategy.cooldown_seconds


async def run_forever() -> None:
    await TradingEngine(load_settings()).run()


def _order_from_signal(
    signal: Signal,
    quote: Quote,
    portfolio: Portfolio,
    settings: Settings,
) -> OrderIntent | None:
    price = quote.mid
    if price is None or price <= 0:
        return None
    if signal.action == SignalAction.BUY:
        max_notional = portfolio.net_liquidation * settings.risk.max_symbol_position_pct
        quantity = floor(max_notional / price)
        if quantity <= 0:
            return None
        limit = round((quote.ask or price) * 1.0005, 2)
        return OrderIntent(signal.symbol, Side.BUY, quantity, portfolio.account_id, limit, signal.reason)
    if signal.action == SignalAction.SELL:
        position = portfolio.position(signal.symbol)
        quantity = floor(position.quantity) if position else 0
        if quantity <= 0:
            return None
        limit = round((quote.bid or price) * 0.9995, 2)
        return OrderIntent(signal.symbol, Side.SELL, quantity, portfolio.account_id, limit, signal.reason)
    return None


def _jsonable(value: object) -> object:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return value

