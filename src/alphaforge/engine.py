from __future__ import annotations

import asyncio

from alphaforge.core.config import Settings, load_settings
from alphaforge.logging.event_log import EventLogger
from alphaforge.state.grid_store import GridStateStore
from alphaforge.adapters.ibkr import IBKRClient
from alphaforge.core.models import GridRuntimeConfig
from alphaforge.core.models import GridState, Portfolio, Quote
from alphaforge.execution.order_manager import OrderManager
from alphaforge.execution.risk import RiskManager
from alphaforge.strategies.grid_v1 import GridStrategy

PORTFOLIO_TIMEOUT_SECONDS = 10


class TradingEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = IBKRClient(settings)
        self.state_store = GridStateStore(settings.paths.grid_config)
        self.risk = RiskManager(settings.risk, settings.paths.kill_switch)

    async def run(self) -> None:
        self.settings.paths.log_dir.mkdir(parents=True, exist_ok=True)
        self.settings.paths.state_dir.mkdir(parents=True, exist_ok=True)
        grid_config = self.state_store.load()
        logger = EventLogger(
            self.settings.paths.audit_log,
            self.settings.paths.trade_log,
            grid_config.strategy_name,
            self.settings.env.mode.value,
            self.settings.env.account,
            grid_config.audit_log_sample_rate,
        )
        strategy = GridStrategy(grid_config.trading_window)
        order_manager = OrderManager(
            self.client,
            self.state_store,
            self.risk,
            logger,
            self.settings.env.account,
            strategy,
        )

        await self.client.connect()
        logger.trade(
            "connected",
            "_system",
            port=self.settings.ibkr_port,
            symbols=grid_config.symbols,
        )
        try:
            logger.trade("reconcile_started", "_system")
            await order_manager.reconcile(grid_config)
            self._log_ibkr_warnings(logger)
            logger.trade("reconcile_completed", "_system")
            portfolio = await self._load_portfolio(logger)
            logger.trade(
                "portfolio_loaded",
                "_system",
                net_liquidation=portfolio.net_liquidation,
                cash=portfolio.cash,
                positions=list(portfolio.positions),
            )
            logger.trade("quote_stream_starting", "_system", symbols=grid_config.symbols)
            async for quote in self.client.stream_quotes(grid_config.symbols):
                await order_manager.drain_order_events(grid_config)
                grid_config = self.state_store.load()
                try:
                    portfolio = await self._load_portfolio(logger)
                except TimeoutError:
                    continue
                await self._handle_quote(
                    quote,
                    portfolio,
                    grid_config,
                    strategy,
                    order_manager,
                    logger,
                )
        finally:
            self.client.disconnect()
            logger.trade("disconnected", "_system")

    def _log_ibkr_warnings(self, logger: EventLogger) -> None:
        for request in self.client.drain_runtime_warnings():
            logger.trade("ibkr_request_timeout", "_system", request=request)

    async def _load_portfolio(self, logger: EventLogger) -> Portfolio:
        try:
            return await asyncio.wait_for(
                self.client.portfolio(),
                timeout=PORTFOLIO_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            logger.trade("portfolio_timeout", "_system")
            raise TimeoutError("portfolio request timed out") from exc

    async def _handle_quote(
        self,
        quote: Quote,
        portfolio: Portfolio,
        grid_config: GridRuntimeConfig,
        strategy: GridStrategy,
        order_manager: OrderManager,
        logger: EventLogger,
    ) -> None:
        grid = grid_config.grid_for(quote.symbol)
        if grid is None:
            logger.regular("quote_unconfigured_symbol", quote.symbol, quote=quote)
            return

        evaluation = strategy.evaluate(grid, quote, portfolio)
        if evaluation.cancel_active_order:
            await order_manager.cancel_if_needed(grid_config, grid, evaluation.reason)
            return
        if grid.state == GridState.WAITING_TRADE:
            logger.regular(evaluation.reason or "waiting_existing_order", grid.symbol, quote=quote)
            return
        if evaluation.trigger is None:
            logger.regular(evaluation.reason or "quote_no_trigger", grid.symbol, quote=quote)
            return

        await order_manager.submit_trigger(
            grid_config,
            grid,
            evaluation.trigger,
            quote,
            portfolio,
        )


async def run_forever() -> None:
    await TradingEngine(load_settings()).run()
