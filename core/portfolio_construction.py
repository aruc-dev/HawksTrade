"""
Portfolio construction helpers for the scan pipeline.

The first pipeline migration keeps existing sizing behavior in order_executor
by carrying strategy-provided quantity hints forward unchanged.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from core.trading_models import OrderPlan, PortfolioTarget, RiskDecision, Signal


@dataclass(frozen=True)
class EntrySizingDecision:
    requested_qty: float
    capped_qty: float
    source: str

    @property
    def capped(self) -> bool:
        return self.capped_qty < self.requested_qty


def _positive_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


def build_portfolio_target(signal: Signal) -> PortfolioTarget:
    return PortfolioTarget(
        symbol=signal.symbol,
        side=signal.action,
        strategy=signal.strategy,
        asset_class=signal.asset_class,
        quantity_hint=signal.suggested_qty,
        atr_stop_price=signal.atr_stop_price,
        source_signal=signal,
    )


def build_portfolio_targets(signals: Iterable[Signal]) -> list[PortfolioTarget]:
    return [build_portfolio_target(signal) for signal in signals]


def build_order_plan(decision: RiskDecision) -> OrderPlan | None:
    if not decision.allowed:
        return None
    target = decision.target
    return OrderPlan(
        symbol=target.symbol,
        side=target.side,
        strategy=target.strategy,
        asset_class=target.asset_class,
        suggested_qty=target.quantity_hint,
        atr_stop_price=target.atr_stop_price,
        reason=decision.reason,
        source_target=target,
    )


def construct_entry_size(
    *,
    price: float,
    strategy: str,
    pre_trade_qty,
    suggested_qty=None,
    kelly_sizer=None,
    capper=None,
) -> EntrySizingDecision:
    """
    Preserve the existing entry sizing precedence:
    signal ATR-risk quantity > momentum Kelly quantity > pre-trade quantity,
    followed by the configured max-position cap.
    """
    requested_qty = _positive_float(pre_trade_qty) or 0.0
    source = "pre_trade"

    signal_qty = _positive_float(suggested_qty)
    if signal_qty is not None:
        requested_qty = signal_qty
        source = "signal_quantity_hint"
    elif strategy == "momentum" and kelly_sizer is not None:
        kelly_qty = _positive_float(kelly_sizer(price=price))
        if kelly_qty is not None:
            requested_qty = kelly_qty
            source = "kelly"

    capped_raw = capper(price, requested_qty) if capper is not None else requested_qty
    capped_qty = capped_raw if _positive_float(capped_raw) is not None else 0.0
    return EntrySizingDecision(
        requested_qty=requested_qty,
        capped_qty=capped_qty,
        source=source,
    )
