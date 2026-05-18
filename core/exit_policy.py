"""
Exit policy helpers shared by live scans, risk checks, and backtests.

The risk manager still owns hard stop-loss and take-profit exits. This module
decides whether a strategy-specific hold or profit-protection policy should
force an exit.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

VALID_MOMENTUM_EXIT_POLICIES = {
    "fixed_hold",
    "profit_trailing",
    "risk_only_baseline",
}

PROFIT_TRAILING_REASON_PREFIXES = {
    "momentum": "Momentum trailing stop",
    "relative_strength": "Relative strength profit protection",
    "range_breakout": "Range breakout profit protection",
    "rsi_reversion": "RSI reversion profit protection",
}


@dataclass(frozen=True)
class HoldExitDecision:
    should_exit: bool
    reason: str = ""
    force_market: bool = False


def finite_positive_float(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


def _finite_float_or_default(value, default: float, *, min_value: float = 0.0, allow_min: bool = True) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    if parsed < min_value or (parsed == min_value and not allow_min):
        return default
    return parsed


def normalize_momentum_exit_policy(policy: str | None) -> str:
    """Return a known momentum exit policy name."""
    if not policy:
        return "profit_trailing"
    normalized = str(policy).strip().lower()
    if normalized not in VALID_MOMENTUM_EXIT_POLICIES:
        raise ValueError(
            "momentum exit_policy must be one of: "
            + ", ".join(sorted(VALID_MOMENTUM_EXIT_POLICIES))
        )
    return normalized


def update_high_water_price(position: dict, current_price: float) -> float:
    """Persist and return the best observed price for an open position."""
    high_water = max(
        float(position.get("high_water_price", position.get("entry_price", current_price))),
        float(current_price),
    )
    position["high_water_price"] = high_water
    return high_water


def _profit_trailing_exit(
    *,
    strategy: str,
    entry_price: float,
    current_price: float,
    peak_price: float | None,
    strategy_cfg: dict,
) -> tuple[bool, str]:
    entry = finite_positive_float(entry_price)
    current = finite_positive_float(current_price)
    peak = finite_positive_float(peak_price if peak_price is not None else current_price)
    if entry is None or current is None or peak is None:
        return False, ""

    peak_gain_pct = (peak / entry) - 1.0
    drawdown_from_peak = (current / peak) - 1.0

    activation_pct = _finite_float_or_default(
        strategy_cfg.get("trail_activation_pct", 0.06),
        0.06,
        min_value=0.0,
    )
    trailing_stop_pct = _finite_float_or_default(
        strategy_cfg.get("trailing_stop_pct", 0.04),
        0.04,
        min_value=0.0,
        allow_min=False,
    )
    if peak_gain_pct < activation_pct or drawdown_from_peak > -trailing_stop_pct:
        return False, ""

    prefix = PROFIT_TRAILING_REASON_PREFIXES.get(
        strategy,
        f"{str(strategy).replace('_', ' ').title()} profit protection",
    )
    return (
        True,
        f"{prefix}: {drawdown_from_peak:+.2%} from peak after {peak_gain_pct:+.2%} peak gain",
    )


def evaluate_hold_exit(
    *,
    strategy: str,
    age_days: float,
    entry_price: float,
    current_price: float,
    strategy_cfg: dict,
    peak_price: float | None = None,
) -> HoldExitDecision:
    """
    Return a structured strategy hold/profit-protection exit decision.

    Policies:
      - fixed_hold: existing behavior; exit immediately once hold_days expires.
      - risk_only_baseline: benchmark behavior; hold_days never forces an exit.
      - profit_trailing: trailing protection can exit before hold_days once it
        is armed; after hold_days, losers/flat trades exit and winners can run
        under the same trailing stop plus an optional max_hold_days cap.
      - profit_trailing_enabled: non-momentum strategies can opt into
        high-water profit protection before their fixed hold cap.
    """
    hold_days = strategy_cfg.get("hold_days")
    if not hold_days:
        return HoldExitDecision(False)
    hold_days = float(hold_days)

    if strategy != "momentum":
        if bool(strategy_cfg.get("profit_trailing_enabled", False)):
            should_exit, reason = _profit_trailing_exit(
                strategy=strategy,
                entry_price=entry_price,
                current_price=current_price,
                peak_price=peak_price,
                strategy_cfg=strategy_cfg,
            )
            if should_exit:
                return HoldExitDecision(True, reason, force_market=True)
        if age_days < hold_days:
            return HoldExitDecision(False)
        return HoldExitDecision(True, f"Hold {int(age_days)}d", force_market=False)

    policy = normalize_momentum_exit_policy(strategy_cfg.get("exit_policy"))
    if policy == "risk_only_baseline":
        return HoldExitDecision(False)
    if policy == "fixed_hold":
        if age_days < hold_days:
            return HoldExitDecision(False)
        return HoldExitDecision(True, f"Hold {int(age_days)}d", force_market=True)

    should_exit, reason = _profit_trailing_exit(
        strategy=strategy,
        entry_price=entry_price,
        current_price=current_price,
        peak_price=peak_price,
        strategy_cfg=strategy_cfg,
    )
    if should_exit:
        return HoldExitDecision(True, reason, force_market=True)

    if age_days < hold_days:
        return HoldExitDecision(False)

    pnl_pct = (float(current_price) / float(entry_price)) - 1.0
    profit_floor_pct = float(strategy_cfg.get("profit_floor_pct", 0.0))
    if pnl_pct <= profit_floor_pct:
        return HoldExitDecision(
            True,
            f"Momentum hold expired without profit: {pnl_pct:+.2%} <= {profit_floor_pct:+.2%}",
            force_market=True,
        )

    max_hold_days = strategy_cfg.get("max_hold_days")
    if max_hold_days and age_days >= float(max_hold_days):
        return HoldExitDecision(True, f"Momentum max hold {int(age_days)}d", force_market=True)

    return HoldExitDecision(False)


def should_exit_for_hold(
    *,
    strategy: str,
    age_days: float,
    entry_price: float,
    current_price: float,
    strategy_cfg: dict,
    peak_price: float | None = None,
) -> tuple[bool, str]:
    """Return the legacy tuple form of a hold/profit-protection decision."""
    decision = evaluate_hold_exit(
        strategy=strategy,
        age_days=age_days,
        entry_price=entry_price,
        current_price=current_price,
        peak_price=peak_price,
        strategy_cfg=strategy_cfg,
    )
    return decision.should_exit, decision.reason
