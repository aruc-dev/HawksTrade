"""
Shared trading pipeline models.

These dataclasses are intentionally broker-agnostic. Strategies emit Signal
objects, portfolio construction turns them into targets, risk converts targets
into order plans, and order_executor remains the broker boundary.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping


def _optional_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


@dataclass(frozen=True)
class Signal:
    symbol: str
    action: str
    strategy: str
    asset_class: str
    suggested_qty: float | None = None
    atr_stop_price: float | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PortfolioTarget:
    symbol: str
    side: str
    strategy: str
    asset_class: str
    quantity_hint: float | None = None
    atr_stop_price: float | None = None
    source_signal: Signal | None = None


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    target: PortfolioTarget
    reason: str = "OK"
    code: str = "allowed"
    context: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    @classmethod
    def allow(
        cls,
        target: PortfolioTarget,
        *,
        reason: str = "OK",
        warnings: tuple[str, ...] = (),
    ) -> "RiskDecision":
        return cls(True, target, reason=reason, warnings=warnings)

    @classmethod
    def block(
        cls,
        target: PortfolioTarget,
        *,
        reason: str,
        code: str,
        context: Mapping[str, Any] | None = None,
    ) -> "RiskDecision":
        return cls(False, target, reason=reason, code=code, context=context or {})


@dataclass(frozen=True)
class OrderPlan:
    symbol: str
    side: str
    strategy: str
    asset_class: str
    suggested_qty: float | None = None
    atr_stop_price: float | None = None
    reason: str = "risk_approved"
    source_target: PortfolioTarget | None = None


@dataclass(frozen=True)
class ExecutionResult:
    order_plan: OrderPlan
    raw_result: Mapping[str, Any] | None = None

    @property
    def status(self) -> str:
        if not self.raw_result:
            return ""
        return str(self.raw_result.get("status", "") or "")


def signal_from_strategy_dict(raw: Mapping[str, Any], *, strategy_name: str, asset_class: str) -> Signal:
    """Adapt the existing strategy signal dict shape into a typed Signal."""
    return Signal(
        symbol=str(raw.get("symbol") or ""),
        action=str(raw.get("action") or "").lower(),
        strategy=strategy_name,
        asset_class=asset_class,
        suggested_qty=_optional_float(raw.get("atr_risk_qty")),
        atr_stop_price=_optional_float(raw.get("atr_stop_price")),
        raw=dict(raw),
    )
