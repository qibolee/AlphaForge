from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, AsyncIterator

from alphaforge.core.config import Settings
from alphaforge.core.models import (
    BrokerOrderRef,
    OrderEvent,
    OrderEventType,
    OrderIntent,
    Portfolio,
    Position,
    Quote,
)


class IBKRClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._ib: Any | None = None
        self._module: Any | None = None
        self._order_events: asyncio.Queue[OrderEvent] = asyncio.Queue()
        self._trades_by_order_id: dict[int, Any] = {}

    async def connect(self) -> None:
        module = _load_ib_async()
        self._module = module
        self._ib = module.IB()
        await self._ib.connectAsync(
            self.settings.ibkr.host,
            self.settings.ibkr_port,
            clientId=self.settings.ibkr.client_id,
        )
        self._register_order_events()
        self._ib.reqMarketDataType(self.settings.ibkr.market_data_type)
        accounts = set(self._ib.managedAccounts())
        if self.settings.env.account not in accounts:
            message = (
                f"IB_ACCOUNT {self.settings.env.account!r} "
                f"not in managed accounts: {sorted(accounts)}"
            )
            raise RuntimeError(message)

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
        return Portfolio(
            account_id=account,
            net_liquidation=net_liq,
            cash=cash,
            positions=positions,
        )

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

    async def place_limit_order(self, intent: OrderIntent) -> BrokerOrderRef:
        self._require_connected()
        if intent.account_id != self.settings.env.account:
            raise RuntimeError("order account does not match IB_ACCOUNT")

        module = self._module
        contract = module.Stock(intent.symbol, "SMART", "USD")
        await self._ib.qualifyContractsAsync(contract)
        order = module.LimitOrder(intent.side.value, intent.quantity, intent.limit_price)
        order.account = intent.account_id
        order.tif = intent.tif
        order.outsideRth = intent.outside_rth
        order.orderRef = intent.order_ref
        if intent.good_till_date is not None:
            order.goodTillDate = _good_till_date(intent.good_till_date, intent.good_till_timezone)

        trade = self._ib.placeOrder(contract, order)
        order_id = int(getattr(trade.order, "orderId"))
        self._trades_by_order_id[order_id] = trade
        return BrokerOrderRef(
            order_id=order_id,
            perm_id=_optional_int(getattr(getattr(trade, "orderStatus", None), "permId", None)),
            order_ref=intent.order_ref,
        )

    async def cancel_order(self, order_id: int) -> None:
        self._require_connected()
        trade = self._trades_by_order_id.get(int(order_id))
        if trade is not None:
            self._ib.cancelOrder(trade.order)
            return

        order = self._module.Order()
        order.orderId = int(order_id)
        self._ib.cancelOrder(order)

    async def reconcile_order_events(self) -> list[OrderEvent]:
        self._require_connected()
        req_open_orders = getattr(self._ib, "reqOpenOrdersAsync", None)
        if req_open_orders is not None:
            await req_open_orders()
            await asyncio.sleep(1)

        events: list[OrderEvent] = []
        for trade in getattr(self._ib, "openTrades", lambda: [])():
            order_id = _optional_int(getattr(getattr(trade, "order", None), "orderId", None))
            if order_id is not None:
                self._trades_by_order_id[order_id] = trade
            events.append(_trade_event(OrderEventType.OPEN_ORDER, trade))

        req_executions = getattr(self._ib, "reqExecutionsAsync", None)
        if req_executions is not None:
            for fill in await req_executions():
                events.append(_execution_event(None, fill))
        return events

    def get_order_event_nowait(self) -> OrderEvent | None:
        try:
            return self._order_events.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def _register_order_events(self) -> None:
        self._add_event_handler("openOrderEvent", self._on_open_order)
        self._add_event_handler("orderStatusEvent", self._on_order_status)
        self._add_event_handler("execDetailsEvent", self._on_exec_details)
        self._add_event_handler("commissionReportEvent", self._on_commission_report)

    def _add_event_handler(self, event_name: str, handler: object) -> None:
        event = getattr(self._ib, event_name, None)
        if event is not None:
            event += handler

    def _on_open_order(self, trade: Any) -> None:
        self._remember_trade(trade)
        self._order_events.put_nowait(_trade_event(OrderEventType.OPEN_ORDER, trade))

    def _on_order_status(self, trade: Any) -> None:
        self._remember_trade(trade)
        self._order_events.put_nowait(_trade_event(OrderEventType.ORDER_STATUS, trade))

    def _on_exec_details(self, trade: Any, fill: Any) -> None:
        self._remember_trade(trade)
        self._order_events.put_nowait(_execution_event(trade, fill))

    def _on_commission_report(self, trade: Any, fill: Any, report: Any) -> None:
        self._remember_trade(trade)
        self._order_events.put_nowait(_commission_event(trade, fill, report))

    def _remember_trade(self, trade: Any) -> None:
        order_id = _optional_int(getattr(getattr(trade, "order", None), "orderId", None))
        if order_id is not None:
            self._trades_by_order_id[order_id] = trade

    def _require_connected(self) -> None:
        if self._ib is None or not self._ib.isConnected():
            raise RuntimeError("IBKR client is not connected")


def _load_ib_async() -> Any:
    try:
        import ib_async
    except ImportError as exc:  # pragma: no cover - installed on AWS by install.sh.
        raise RuntimeError("ib_async is not installed; run install.sh") from exc
    return ib_async


def _trade_event(event_type: OrderEventType, trade: Any) -> OrderEvent:
    order = getattr(trade, "order", None)
    status = getattr(trade, "orderStatus", None)
    return OrderEvent(
        event_type=event_type,
        order_id=_optional_int(getattr(order, "orderId", None)),
        perm_id=_optional_int(getattr(status, "permId", None)),
        order_ref=str(getattr(order, "orderRef", "") or ""),
        status=getattr(status, "status", None),
        filled=_optional_float(getattr(status, "filled", None)),
        remaining=_optional_float(getattr(status, "remaining", None)),
        avg_fill_price=_optional_float(getattr(status, "avgFillPrice", None)),
        last_fill_price=_optional_float(getattr(status, "lastFillPrice", None)),
    )


def _execution_event(trade: Any | None, fill: Any) -> OrderEvent:
    order = getattr(trade, "order", None)
    execution = getattr(fill, "execution", fill)
    return OrderEvent(
        event_type=OrderEventType.EXECUTION,
        order_id=_optional_int(
            getattr(execution, "orderId", None) or getattr(order, "orderId", None)
        ),
        perm_id=_optional_int(getattr(execution, "permId", None)),
        order_ref=str(getattr(order, "orderRef", "") or ""),
        status="Execution",
        filled=_optional_float(getattr(execution, "shares", None)),
        last_fill_price=_optional_float(getattr(execution, "price", None)),
        exec_id=str(getattr(execution, "execId", "") or ""),
    )


def _commission_event(trade: Any, fill: Any, report: Any) -> OrderEvent:
    order = getattr(trade, "order", None)
    execution = getattr(fill, "execution", fill)
    return OrderEvent(
        event_type=OrderEventType.COMMISSION,
        order_id=_optional_int(
            getattr(execution, "orderId", None) or getattr(order, "orderId", None)
        ),
        perm_id=_optional_int(getattr(execution, "permId", None)),
        order_ref=str(getattr(order, "orderRef", "") or ""),
        status="Commission",
        exec_id=str(getattr(report, "execId", "") or getattr(execution, "execId", "") or ""),
        commission=_optional_float(getattr(report, "commission", None)),
        currency=getattr(report, "currency", None),
    )


def _good_till_date(value: datetime, timezone_name: str) -> str:
    return f"{value.strftime('%Y%m%d %H:%M:%S')} {timezone_name}"


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


def _optional_float(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    try:
        return None if value in (None, "") else int(value)
    except (TypeError, ValueError):
        return None
