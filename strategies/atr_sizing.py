"""
Shared ATR-risk sizing helpers for strategy signals.
"""

from __future__ import annotations

import math


def _finite_float(value):
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(converted):
        return None
    return converted


def atr_stop_and_qty(
    *,
    symbol: str,
    price: float,
    atr: float,
    atr_multiplier: float,
    portfolio_equity: float,
    risk_per_trade_pct: float,
    min_trade_value: float,
    logger,
    prefix: str,
):
    """Return (atr_stop, atr_risk_qty) or None when sizing is not tradeable."""
    price = _finite_float(price)
    atr = _finite_float(atr)
    atr_multiplier = _finite_float(atr_multiplier)
    portfolio_equity = _finite_float(portfolio_equity)
    risk_per_trade_pct = _finite_float(risk_per_trade_pct)
    min_trade_value = _finite_float(min_trade_value)

    if None in (price, atr, atr_multiplier, portfolio_equity, risk_per_trade_pct, min_trade_value):
        logger.info(f"{prefix} {symbol} skipped: invalid ATR sizing inputs.")
        return None
    if price <= 0 or atr <= 0 or atr_multiplier <= 0 or portfolio_equity <= 0 or risk_per_trade_pct <= 0:
        logger.info(f"{prefix} {symbol} skipped: ATR-risk sizing unavailable.")
        return None

    atr_stop = round(price - atr_multiplier * atr, 4)
    if not math.isfinite(atr_stop) or atr_stop >= price:
        logger.info(f"{prefix} {symbol} skipped: invalid ATR stop {atr_stop}.")
        return None

    risk_dollars = portfolio_equity * risk_per_trade_pct
    risk_per_share = price - atr_stop
    if risk_per_share <= 0:
        logger.info(f"{prefix} {symbol} skipped: invalid ATR risk per share.")
        return None

    atr_risk_qty = round(risk_dollars / risk_per_share, 6)
    notional = atr_risk_qty * price
    if notional < min_trade_value:
        logger.info(
            f"{prefix} {symbol} ATR-risk quantity {atr_risk_qty} "
            f"(${notional:.2f}) is below min ${min_trade_value}. Skipping signal."
        )
        return None

    return atr_stop, atr_risk_qty
