from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from alphaforge.logging.event_log import EventLogger
from alphaforge.state.grid_store import GridStateStore
from alphaforge.core.models import (
    ActiveOrder,
    GridEntry,
    GridRuntimeConfig,
    GridState,
    OrderEvent,
    OrderEventType,
    OrderIntent,
    Portfolio,
    Quote,
)
from alphaforge.execution.risk import RiskManager
from alphaforge.strategies.grid_v1 import GridTrigger, GridStrategy

FINAL_STATUSES = {"Filled", "Cancelled", "ApiCancelled", "Expired", "Inactive"}
REJECTED_STATUSES = {"Inactive"}


class OrderManager:
    def __init__(
        self,
        client: object,
        state_store: GridStateStore,
        risk: RiskManager,
        logger: EventLogger,
        account_id: str,
        strategy: GridStrategy,
    ) -> None:
        self.client = client
        self.state_store = state_store
        self.risk = risk
        self.logger = logger
        self.account_id = account_id
        self.strategy = strategy

    async def reconcile(self, config: GridRuntimeConfig) -> None:
        for event in await self.client.reconcile_order_events():
            self.apply_order_event(config, event)
        for grid in config.grids:
            if grid.state == GridState.WAITING_TRADE and grid.active_order is not None:
                self.logger.trade("reconciled", grid.symbol, active_order=grid.active_order)
        self.state_store.save(config)

    async def drain_order_events(self, config: GridRuntimeConfig) -> None:
        while True:
            event = self.client.get_order_event_nowait()
            if event is None:
                return
            self.apply_order_event(config, event)

    async def submit_trigger(
        self,
        config: GridRuntimeConfig,
        grid: GridEntry,
        trigger: GridTrigger,
        quote: Quote,
        portfolio: Portfolio,
    ) -> bool:
        order_ref = f"alphaforge:grid_v1:{grid.symbol}:{uuid4().hex}"
        intent = OrderIntent(
            symbol=grid.symbol,
            side=trigger.side,
            quantity=trigger.quantity,
            account_id=self.account_id,
            limit_price=trigger.limit_price,
            reason=trigger.reason,
            order_ref=order_ref,
            tif="GTD",
            good_till_date=self.strategy.good_till_date(quote.timestamp),
            good_till_timezone=self.strategy.window.timezone,
            outside_rth=self.strategy.window.outside_rth,
        )

        decision = self.risk.check(intent, portfolio, quote)
        if not decision.allowed:
            self.logger.trade("risk_rejected", grid.symbol, reason=decision.reason, intent=intent)
            return False

        self.logger.trade("triggered", grid.symbol, trigger=trigger, quote=quote)

        grid.state = GridState.WAITING_TRADE
        grid.active_order = ActiveOrder(
            side=trigger.side,
            base_price_at_submit=grid.base_price,
            limit_price=trigger.limit_price,
            quantity=trigger.quantity,
            filled_quantity=0,
            remaining_quantity=trigger.quantity,
            order_id=None,
            perm_id=None,
            order_ref=order_ref,
            status="SUBMITTING",
            submitted_at=datetime.now().astimezone(),
        )
        self.state_store.save(config)

        try:
            broker_ref = await self.client.place_limit_order(intent)
        except Exception as exc:
            self.logger.trade("order_rejected", grid.symbol, reason=str(exc), intent=intent)
            grid.state = GridState.WAITING_TRIGGER
            grid.active_order = None
            self.state_store.save(config)
            return False

        active = grid.active_order
        if active is None:
            raise RuntimeError("active order state disappeared during order submission")
        active.order_id = broker_ref.order_id
        active.perm_id = broker_ref.perm_id
        active.order_ref = broker_ref.order_ref or order_ref
        active.status = "Submitted"
        self.state_store.save(config)
        self.logger.trade("order_submitted", grid.symbol, intent=intent, broker_ref=broker_ref)
        return True

    async def cancel_if_needed(
        self,
        config: GridRuntimeConfig,
        grid: GridEntry,
        evaluation_reason: str,
    ) -> None:
        active = grid.active_order
        if active is None or active.order_id is None or active.cancel_requested:
            return

        await self.client.cancel_order(active.order_id)
        active.cancel_requested = True
        active.status = "CANCEL_REQUESTED"
        self.state_store.save(config)
        self.logger.trade(
            "order_cancel_requested",
            grid.symbol,
            reason=evaluation_reason,
            active_order=active,
        )

    def apply_order_event(self, config: GridRuntimeConfig, event: OrderEvent) -> None:
        grid = _find_grid(config, event)
        if grid is None or grid.active_order is None:
            return

        active = grid.active_order
        if event.event_type == OrderEventType.OPEN_ORDER:
            self._apply_open_order(grid, active, event)
        elif event.event_type == OrderEventType.ORDER_STATUS:
            self._apply_order_status(grid, active, event)
        elif event.event_type == OrderEventType.EXECUTION:
            self._apply_execution(grid, active, event)
        elif event.event_type == OrderEventType.COMMISSION:
            self.logger.trade("order_commission", grid.symbol, event=event)

        if _is_final(active):
            self._settle_final_order(grid, active)
        self.state_store.save(config)

    def _apply_open_order(self, grid: GridEntry, active: ActiveOrder, event: OrderEvent) -> None:
        if event.order_id is not None:
            active.order_id = event.order_id
        if event.perm_id is not None:
            active.perm_id = event.perm_id
        if event.status:
            active.status = event.status
        self.logger.trade("order_opened", grid.symbol, event=event, active_order=active)

    def _apply_order_status(self, grid: GridEntry, active: ActiveOrder, event: OrderEvent) -> None:
        previous_filled = active.filled_quantity
        if event.status:
            active.status = event.status
        if event.order_id is not None:
            active.order_id = event.order_id
        if event.perm_id is not None:
            active.perm_id = event.perm_id
        if event.filled is not None:
            active.filled_quantity = max(active.filled_quantity, event.filled)
        if event.remaining is not None:
            active.remaining_quantity = event.remaining
        else:
            active.remaining_quantity = max(active.quantity - active.filled_quantity, 0)

        has_new_partial_fill = (
            0 < active.filled_quantity < active.quantity
            and active.filled_quantity > previous_filled
        )
        if has_new_partial_fill:
            self.logger.trade(
                "order_partially_filled",
                grid.symbol,
                event=event,
                active_order=active,
            )

    def _apply_execution(self, grid: GridEntry, active: ActiveOrder, event: OrderEvent) -> None:
        if event.exec_id and event.exec_id in active.seen_exec_ids:
            return
        if event.exec_id:
            active.seen_exec_ids.append(event.exec_id)
        if event.order_id is not None:
            active.order_id = event.order_id
        if event.perm_id is not None:
            active.perm_id = event.perm_id
        if event.filled is not None:
            active.execution_filled_quantity += event.filled
            active.filled_quantity = max(active.filled_quantity, active.execution_filled_quantity)
            active.remaining_quantity = max(active.quantity - active.filled_quantity, 0)
        if 0 < active.filled_quantity < active.quantity:
            self.logger.trade(
                "order_partially_filled",
                grid.symbol,
                event=event,
                active_order=active,
            )

    def _settle_final_order(self, grid: GridEntry, active: ActiveOrder) -> None:
        old_base_price = active.base_price_at_submit
        fill_ratio = active.fill_ratio
        new_base_price = round(
            old_base_price + fill_ratio * (active.limit_price - old_base_price),
            4,
        )

        if active.cancel_requested or active.status in {"Cancelled", "ApiCancelled", "Expired"}:
            self.logger.trade("order_cancel_confirmed", grid.symbol, active_order=active)

        if active.status in REJECTED_STATUSES and active.filled_quantity <= 0:
            grid.paused = True
            event_type = "order_rejected"
        elif active.filled_quantity >= active.quantity:
            event_type = "order_filled"
        elif active.filled_quantity <= 0:
            event_type = "order_cancelled_zero_fill"
        else:
            event_type = "order_cancelled_partial_filled"

        self.logger.trade(
            event_type,
            grid.symbol,
            old_base_price=old_base_price,
            new_base_price=new_base_price,
            active_order=active,
        )
        if new_base_price != grid.base_price:
            self.logger.trade(
                "base_price_updated",
                grid.symbol,
                old_base_price=grid.base_price,
                new_base_price=new_base_price,
                fill_ratio=fill_ratio,
                active_order=active,
            )

        grid.base_price = new_base_price
        grid.state = GridState.WAITING_TRIGGER
        grid.active_order = None


def _find_grid(config: GridRuntimeConfig, event: OrderEvent) -> GridEntry | None:
    for grid in config.grids:
        active = grid.active_order
        if active is None:
            continue
        if event.order_ref and active.order_ref == event.order_ref:
            return grid
        if event.order_id is not None and active.order_id == event.order_id:
            return grid
        if event.perm_id is not None and active.perm_id == event.perm_id:
            return grid
    return None


def _is_final(active: ActiveOrder) -> bool:
    if active.status == "Filled" and active.remaining_quantity <= 0:
        active.filled_quantity = max(active.filled_quantity, float(active.quantity))
        active.execution_filled_quantity = max(
            active.execution_filled_quantity,
            active.filled_quantity,
        )
        return True
    if active.filled_quantity >= active.quantity:
        active.status = "Filled"
        active.remaining_quantity = 0
        return True
    if active.remaining_quantity <= 0 and active.status != "CANCEL_REQUESTED":
        active.status = "Filled"
        return True
    return active.status in FINAL_STATUSES
