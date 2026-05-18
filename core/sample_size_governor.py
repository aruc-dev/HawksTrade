"""
Code-enforced sample-size risk discipline.

Strategies should not receive full risk before they have enough closed-trade
evidence. The governor maps each strategy's closed trade count to an effective
risk multiplier and position cap.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Iterable


@dataclass(frozen=True)
class RiskTier:
    name: str
    min_trades: int
    closed_trades: int
    risk_multiplier: float
    position_cap_pct: float
    override: bool = False
    reason: str = ""
    expires_on: str = ""

    @property
    def audit_label(self) -> str:
        suffix = ":override" if self.override else ""
        return f"{self.name}:{self.closed_trades}{suffix}"


def _positive_float(value, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) and parsed > 0 else default


def _today(now=None) -> date:
    if now is None:
        return date.today()
    if hasattr(now, "date"):
        return now.date()
    return date.fromisoformat(str(now)[:10])


def _strategy_closed_count(strategy: str, rows: Iterable[dict]) -> int:
    return sum(
        1
        for row in rows
        if row.get("strategy") == strategy
        and row.get("status") == "closed"
        and row.get("side") == "sell"
    )


def closed_trade_count(strategy: str, rows: Iterable[dict]) -> int:
    return _strategy_closed_count(strategy, rows)


def _configured_tiers(cfg: dict) -> list[dict]:
    validation = cfg.get("validation", {})
    scaling = validation.get("sample_size_scaling", {})
    tiers = scaling.get("tiers") or [
        {"name": "exploration", "min_trades": 0, "risk_multiplier": 0.25, "position_cap_pct": 0.02},
        {"name": "half", "min_trades": 30, "risk_multiplier": 0.50, "position_cap_pct": 0.04},
        {"name": "full", "min_trades": 100, "risk_multiplier": 1.00, "position_cap_pct": 0.08},
    ]
    return sorted(tiers, key=lambda item: int(item.get("min_trades", 0)))


def _active_override(strategy: str, cfg: dict, now=None) -> dict | None:
    scaling = cfg.get("validation", {}).get("sample_size_scaling", {})
    override = (scaling.get("overrides") or {}).get(strategy)
    if not override:
        return None
    reason = str(override.get("reason") or "").strip()
    expires_on = str(override.get("expires_on") or "").strip()
    if not reason or not expires_on:
        return None
    try:
        expiry = date.fromisoformat(expires_on[:10])
    except ValueError:
        return None
    if expiry < _today(now):
        return None
    return override


def effective_risk_for(
    strategy: str,
    cfg: dict,
    *,
    closed_trades: int | None = None,
    rows: Iterable[dict] | None = None,
    now=None,
) -> RiskTier:
    """Return the effective risk tier for a strategy."""
    scaling = cfg.get("validation", {}).get("sample_size_scaling", {})
    base_cap = _positive_float(cfg.get("trading", {}).get("max_position_pct"), 0.08)
    if scaling.get("enabled", True) is False:
        count = int(closed_trades if closed_trades is not None else _strategy_closed_count(strategy, rows or []))
        return RiskTier("disabled", 0, count, 1.0, base_cap)

    count = int(closed_trades if closed_trades is not None else _strategy_closed_count(strategy, rows or []))
    override = _active_override(strategy, cfg, now=now)
    if override is not None:
        return RiskTier(
            name=str(override.get("name") or "override"),
            min_trades=count,
            closed_trades=count,
            risk_multiplier=_positive_float(override.get("risk_multiplier"), 1.0),
            position_cap_pct=_positive_float(override.get("position_cap_pct"), base_cap),
            override=True,
            reason=str(override.get("reason") or ""),
            expires_on=str(override.get("expires_on") or ""),
        )

    selected = _configured_tiers(cfg)[0]
    for tier in _configured_tiers(cfg):
        if count >= int(tier.get("min_trades", 0)):
            selected = tier
    return RiskTier(
        name=str(selected.get("name") or f"tier_{selected.get('min_trades', 0)}"),
        min_trades=int(selected.get("min_trades", 0)),
        closed_trades=count,
        risk_multiplier=_positive_float(selected.get("risk_multiplier"), 1.0),
        position_cap_pct=_positive_float(selected.get("position_cap_pct"), base_cap),
    )


def _finite_nonnegative(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def scale_quantity(
    qty: float,
    tier: RiskTier,
    *,
    base_position_cap_pct: float | None = None,
    base_cap_qty: float | None = None,
    cash_qty: float | None = None,
) -> float:
    """Apply the risk multiplier and tier position cap to a requested quantity."""
    try:
        scaled = float(qty) * float(tier.risk_multiplier)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(scaled) or scaled <= 0:
        return 0.0

    if base_position_cap_pct and base_cap_qty is not None and base_position_cap_pct > 0:
        cap_ratio = min(1.0, float(tier.position_cap_pct) / float(base_position_cap_pct))
        scaled = min(scaled, float(base_cap_qty) * cap_ratio)
    cash_cap = _finite_nonnegative(cash_qty)
    if cash_cap is not None:
        scaled = min(scaled, cash_cap)
    return round(max(scaled, 0.0), 6)


def next_tier_for(strategy: str, cfg: dict, closed_trades: int) -> dict | None:
    for tier in _configured_tiers(cfg):
        minimum = int(tier.get("min_trades", 0))
        if closed_trades < minimum:
            return {
                "strategy": strategy,
                "name": str(tier.get("name") or f"tier_{minimum}"),
                "min_trades": minimum,
                "trades_needed": minimum - closed_trades,
            }
    return None


def format_tier_report(cfg: dict, rows: Iterable[dict]) -> str:
    strategies = sorted(cfg.get("strategies", {}).keys())
    if not strategies:
        return ""
    trade_rows = list(rows)
    lines = ["Sample-Size Risk Tiers:"]
    for strategy in strategies:
        count = closed_trade_count(strategy, trade_rows)
        tier = effective_risk_for(strategy, cfg, closed_trades=count)
        next_tier = next_tier_for(strategy, cfg, count)
        next_text = "full tier reached" if next_tier is None else f"{next_tier['trades_needed']} trades to {next_tier['name']}"
        override = f" override expires={tier.expires_on}" if tier.override else ""
        lines.append(
            f"  {strategy:<16} closed={count:>3} tier={tier.name:<12} "
            f"risk={tier.risk_multiplier:.2f} cap={tier.position_cap_pct:.2%} "
            f"next={next_text}{override}"
        )
    return "\n".join(lines)
