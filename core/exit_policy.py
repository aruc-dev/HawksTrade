"""
Exit policy helpers shared by live scans and backtests.

The risk manager still owns hard stop-loss and take-profit exits. This module
only decides whether a strategy-specific hold period should force an exit.
"""

VALID_MOMENTUM_EXIT_POLICIES = {
    "fixed_hold",
    "profit_trailing",
    "risk_only_baseline",
}


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


def should_exit_for_hold(
    *,
    strategy: str,
    age_days: float,
    entry_price: float,
    current_price: float,
    strategy_cfg: dict,
    peak_price: float | None = None,
) -> tuple[bool, str]:
    """
    Return whether the strategy hold policy should exit the position.

    Policies:
      - fixed_hold: existing behavior; exit immediately once hold_days expires.
      - risk_only_baseline: benchmark behavior; hold_days never forces an exit.
      - profit_trailing: after hold_days, exit losers/flat trades, let winners
        run under a trailing stop and optional max_hold_days cap.
    """
    hold_days = strategy_cfg.get("hold_days")
    if not hold_days or age_days < hold_days:
        return False, ""

    if strategy != "momentum":
        return True, f"Hold {int(age_days)}d"

    policy = normalize_momentum_exit_policy(strategy_cfg.get("exit_policy"))
    if policy == "risk_only_baseline":
        return False, ""
    if policy == "fixed_hold":
        return True, f"Hold {int(age_days)}d"

    pnl_pct = (float(current_price) / float(entry_price)) - 1.0
    profit_floor_pct = float(strategy_cfg.get("profit_floor_pct", 0.0))
    if pnl_pct <= profit_floor_pct:
        return (
            True,
            f"Momentum hold expired without profit: {pnl_pct:+.2%} <= {profit_floor_pct:+.2%}",
        )

    peak = float(peak_price if peak_price is not None else current_price)
    peak_gain_pct = (peak / float(entry_price)) - 1.0
    drawdown_from_peak = (float(current_price) / peak) - 1.0 if peak > 0 else 0.0

    activation_pct = float(strategy_cfg.get("trail_activation_pct", 0.06))
    trailing_stop_pct = float(strategy_cfg.get("trailing_stop_pct", 0.04))
    if peak_gain_pct >= activation_pct and drawdown_from_peak <= -trailing_stop_pct:
        return (
            True,
            f"Momentum trailing stop: {drawdown_from_peak:+.2%} from peak after {peak_gain_pct:+.2%} peak gain",
        )

    max_hold_days = strategy_cfg.get("max_hold_days")
    if max_hold_days and age_days >= float(max_hold_days):
        return True, f"Momentum max hold {int(age_days)}d"

    return False, ""
