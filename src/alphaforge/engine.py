from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
from pathlib import Path

from alphaforge.alerts import Notifier
from alphaforge.core.config import ConfigError, Settings, load_settings
from alphaforge.logging.event_log import EventLogger
from alphaforge.state.grid_store import GridStateStore
from alphaforge.state.heartbeat import (
    HEARTBEAT_INTERVAL_SECONDS,
    LivenessState,
    write_heartbeat,
)
from alphaforge.adapters.ibkr import IBKRClient
from alphaforge.core.models import GridRuntimeConfig
from alphaforge.core.models import GridState, Portfolio, Quote
from alphaforge.execution.order_manager import OrderManager
from alphaforge.execution.risk import RiskManager
from alphaforge.strategies.grid_v1 import GridStrategy

PORTFOLIO_TIMEOUT_SECONDS = 10
SESSION_RETRY_INITIAL_SECONDS = 30
SESSION_RETRY_MAX_SECONDS = 300


class TradingEngine:
    def __init__(self, settings: Settings, liveness: LivenessState | None = None) -> None:
        self.settings = settings
        self.client = IBKRClient(settings)
        self.state_store = GridStateStore(settings.paths.grid_config, settings.paths.grid_state)
        self.risk = RiskManager(settings.risk, settings.paths.kill_switch)
        self.liveness = liveness or LivenessState()

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
            notifier=Notifier.from_settings(self.settings),
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
        self.liveness.connected = True
        self.liveness.symbols = grid_config.symbols
        self.liveness.session_phase = "running"
        self.liveness.last_error = ""  # cleared on a successful connect
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
            reload_failing = False
            async for quote in self.client.stream_quotes(grid_config.symbols):
                self.liveness.last_quote_at = datetime.now(timezone.utc)
                await order_manager.drain_order_events(grid_config)
                try:
                    grid_config = self.state_store.load()
                    if reload_failing:
                        reload_failing = False
                        logger.trade("grid_spec_reload_recovered", "_system")
                except ConfigError as exc:
                    # A live spec edit (afctl edit) introduced an error. Keep the last-good
                    # config so a typo cannot crash the trading loop; the next valid save
                    # of grid.yaml is picked up automatically. Log once on the good->bad
                    # transition at trade level — sampled regular() would hide it, and
                    # logging every loop iteration (~1/s) would spam the log.
                    if not reload_failing:
                        reload_failing = True
                        logger.trade("grid_spec_reload_failed", "_system", error=str(exc))
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
            self.liveness.connected = False
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
    boot_settings = load_settings()
    liveness = LivenessState()
    # Timer-driven heartbeat runs alongside (and outlives) each trading session,
    # so liveness keeps beating through reconnect backoffs and quiet markets.
    heartbeat_task = asyncio.create_task(_heartbeat_loop(boot_settings.paths.heartbeat, liveness))
    try:
        retry_attempt = 0
        while True:
            settings = load_settings()
            logger = _system_logger(settings)
            liveness.session_phase = "starting"
            liveness.connected = False
            try:
                await TradingEngine(settings, liveness).run()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                liveness.connected = False
                liveness.last_error = f"{type(exc).__name__}: {exc}"[:300]
                if not _is_recoverable_session_error(exc):
                    liveness.session_phase = "failed"
                    logger.trade(
                        "engine_session_failed",
                        "_system",
                        error_type=type(exc).__name__,
                        error=str(exc),
                        recoverable=False,
                    )
                    raise

                retry_attempt += 1
                delay_seconds = _retry_delay_seconds(retry_attempt)
                liveness.session_phase = "retrying"
                liveness.retry_attempt = retry_attempt
                logger.trade(
                    "engine_session_retrying",
                    "_system",
                    error_type=type(exc).__name__,
                    error=str(exc),
                    retry_attempt=retry_attempt,
                    delay_seconds=delay_seconds,
                )
                await asyncio.sleep(delay_seconds)
                continue

            retry_attempt += 1
            delay_seconds = _retry_delay_seconds(retry_attempt)
            liveness.session_phase = "ended"
            liveness.retry_attempt = retry_attempt
            logger.trade(
                "engine_session_ended",
                "_system",
                retry_attempt=retry_attempt,
                delay_seconds=delay_seconds,
            )
            await asyncio.sleep(delay_seconds)
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task


async def _heartbeat_loop(path: Path, liveness: LivenessState) -> None:
    while True:
        try:
            write_heartbeat(path, liveness)
        except Exception:
            # Liveness is best-effort: a failed write must never crash trading.
            # Its absence is itself the signal (healthz goes stale -> unhealthy).
            pass
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


def _system_logger(settings: Settings) -> EventLogger:
    grid_config = GridStateStore(settings.paths.grid_config, settings.paths.grid_state).load()
    return EventLogger(
        settings.paths.audit_log,
        settings.paths.trade_log,
        grid_config.strategy_name,
        settings.env.mode.value,
        settings.env.account,
        grid_config.audit_log_sample_rate,
        notifier=Notifier.from_settings(settings),
    )


def _is_recoverable_session_error(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError, OSError)):
        return True
    if isinstance(exc, RuntimeError):
        message = str(exc).lower()
        return "timed out" in message or "not connected" in message
    return False


def _retry_delay_seconds(attempt: int) -> int:
    return min(
        SESSION_RETRY_INITIAL_SECONDS * max(1, attempt),
        SESSION_RETRY_MAX_SECONDS,
    )
