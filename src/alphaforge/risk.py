from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from alphaforge.config import RiskConfig
from alphaforge.models import OrderIntent, Portfolio, Quote, Side


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str


class RiskManager:
    def __init__(self, config: RiskConfig, kill_switch: Path) -> None:
        self.config = config
        self.kill_switch = kill_switch

    def check(self, intent: OrderIntent, portfolio: Portfolio, quote: Quote) -> RiskDecision:
        if self.kill_switch.exists():
            return RiskDecision(False, "kill switch is enabled")
        if intent.account_id != portfolio.account_id:
            return RiskDecision(False, "order account does not match portfolio account")
        if intent.quantity <= 0:
            return RiskDecision(False, "order quantity must be positive")
        if quote.spread_bps is None:
            return RiskDecision(False, "quote spread is unavailable")
        if quote.spread_bps > self.config.max_spread_bps:
            return RiskDecision(False, "quote spread exceeds limit")
        if portfolio.net_liquidation <= 0:
            return RiskDecision(False, "net liquidation must be positive")
        if portfolio.day_pnl <= -portfolio.net_liquidation * self.config.daily_loss_limit_pct:
            return RiskDecision(False, "daily loss limit reached")

        position = portfolio.position(intent.symbol)
        if intent.side == Side.SELL:
            current_quantity = position.quantity if position else 0
            if intent.quantity > current_quantity:
                return RiskDecision(False, "short selling is disabled")
            return RiskDecision(True, "allowed")

        if position is None and portfolio.position_count >= self.config.max_positions:
            return RiskDecision(False, "max positions reached")

        max_symbol_notional = portfolio.net_liquidation * self.config.max_symbol_position_pct
        current_notional = abs(position.market_value) if position else 0.0
        if current_notional + intent.notional > max_symbol_notional:
            return RiskDecision(False, "symbol exposure limit exceeded")

        max_gross = portfolio.net_liquidation * self.config.max_gross_exposure_pct
        if portfolio.gross_exposure + intent.notional > max_gross:
            return RiskDecision(False, "gross exposure limit exceeded")

        return RiskDecision(True, "allowed")

