from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from alphaforge.config import Settings
from alphaforge.models import OrderIntent, Portfolio, Position, Quote, Side


class IBKRClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._ib: Any | None = None
        self._module: Any | None = None

    async def connect(self) -> None:
        module = _load_ib_async()
        self._module = module
        self._ib = module.IB()
        await self._ib.connectAsync(
            self.settings.ibkr.host,
            self.settings.ibkr_port,
            clientId=self.settings.ibkr.client_id,
        )
        self._ib.reqMarketDataType(self.settings.ibkr.market_data_type)
        accounts = set(self._ib.managedAccounts())
        if self.settings.env.account not in accounts:
            raise RuntimeError(
                f"IB_ACCOUNT {self.settings.env.account!r} not in managed accounts: {sorted(accounts)}"
            )

    def disconnect(self) -> None:
        if self._ib is not None and self._ib.isConnected():
            self._ib.disconnect()

    async def portfolio(self) -> Portfolio:
        self._require_connected()
        account = self.settings.env.account
        summary = await self._ib.accountSummaryAsync()
        net_liq = _summary_value(summary, account, "NetLiquidation")
        if net_liq is None or net_liq <= 0:
            raise RuntimeError(f"NetLiquidation is unavailable for account {account}")
        cash = _summary_value(summary, account, "TotalCashValue") or 0.0
        positions = await self.positions()
        return Portfolio(account_id=account, net_liquidation=net_liq, cash=cash, positions=positions)

    async def positions(self) -> dict[str, Position]:
        self._require_connected()
        positions: dict[str, Position] = {}
        for item in self._ib.positions():
            if item.account != self.settings.env.account:
                continue
            symbol = getattr(item.contract, "symbol", "")
            if not symbol:
                continue
            average_cost = float(item.avgCost or 0.0)
            positions[symbol] = Position(
                symbol=symbol,
                quantity=float(item.position),
                average_cost=average_cost,
                market_price=average_cost,
            )
        return positions

    async def stream_quotes(self, symbols: tuple[str, ...]) -> AsyncIterator[Quote]:
        self._require_connected()
        module = self._module
        tickers = []
        for symbol in symbols:
            contract = module.Stock(symbol, "SMART", "USD")
            await self._ib.qualifyContractsAsync(contract)
            tickers.append(self._ib.reqMktData(contract, "", False, False))
        while True:
            await asyncio.sleep(1)
            for ticker in tickers:
                yield Quote(
                    symbol=ticker.contract.symbol,
                    bid=_price(getattr(ticker, "bid", None)),
                    ask=_price(getattr(ticker, "ask", None)),
                    last=_price(ticker.marketPrice()),
                )

    async def place_limit_order(self, intent: OrderIntent) -> str:
        self._require_connected()
        if intent.account_id != self.settings.env.account:
            raise RuntimeError("order account does not match IB_ACCOUNT")
        module = self._module
        contract = module.Stock(intent.symbol, "SMART", "USD")
        await self._ib.qualifyContractsAsync(contract)
        order = module.LimitOrder(intent.side.value, intent.quantity, intent.limit_price)
        order.account = intent.account_id
        order.tif = "DAY"
        trade = self._ib.placeOrder(contract, order)
        return str(trade.order.orderId)

    def _require_connected(self) -> None:
        if self._ib is None or not self._ib.isConnected():
            raise RuntimeError("IBKR client is not connected")


def _load_ib_async() -> Any:
    try:
        import ib_async
    except ImportError as exc:  # pragma: no cover - installed on AWS by deploy.sh.
        raise RuntimeError("ib_async is not installed; run deploy.sh") from exc
    return ib_async


def _price(value: Any) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if price <= 0 or price != price:
        return None
    return price


def _summary_value(rows: list[Any], account: str, tag: str) -> float | None:
    for row in rows:
        if row.account == account and row.tag == tag:
            try:
                return float(row.value)
            except ValueError:
                return None
    return None


def side_for_quantity(quantity: float) -> Side:
    return Side.BUY if quantity >= 0 else Side.SELL
