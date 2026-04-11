"""
HawksTrade - Risk Manager
==========================
Enforces all risk rules before any order is placed:
  - Max position size as % of portfolio
  - Max number of open positions
  - Daily loss limit (hard stop)
  - Stop-loss / take-profit price calculation
  - Intraday trading gate
"""

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import yaml

from core import alpaca_client as ac

BASE_DIR = Path(__file__).resolve().parent.parent
with open(BASE_DIR / "config" / "config.yaml") as f:
    CFG = yaml.safe_load(f)

T = CFG["trading"]
INTRADAY_ENABLED = CFG["intraday"]["enabled"]

log = logging.getLogger("risk_manager")


# ── Daily P&L Tracking (in-process; reset on restart) ────────────────────────

_session_start_value: Optional[float] = None
_session_date: Optional[date] = None


def _refresh_session():
    global _session_start_value, _session_date
    today = date.today()
    if _session_date != today:
        _session_start_value = ac.get_portfolio_value()
        _session_date = today
        log.info(f"Session start portfolio value: ${_session_start_value:,.2f} on {today}")


def daily_loss_exceeded() -> bool:
    """Returns True if the portfolio has fallen more than daily_loss_limit_pct today."""
    _refresh_session()
    current = ac.get_portfolio_value()
    if not _session_start_value or _session_start_value <= 0:
        log.warning(
            "Invalid session start portfolio value "
            f"({_session_start_value}); daily loss check skipped."
        )
        return False
    loss_pct = (_session_start_value - current) / _session_start_value
    if loss_pct >= T["daily_loss_limit_pct"]:
        log.warning(
            f"DAILY LOSS LIMIT HIT: lost {loss_pct:.1%} "
            f"(limit={T['daily_loss_limit_pct']:.1%}). No new trades today."
        )
        return True
    return False


# ── Position Count ────────────────────────────────────────────────────────────

def max_positions_reached() -> bool:
    positions = ac.get_all_positions()
    count = len(positions)
    if count >= T["max_positions"]:
        log.info(f"Max positions reached: {count}/{T['max_positions']}")
        return True
    return False


# ── Position Sizing ───────────────────────────────────────────────────────────

def calculate_position_size(price: float) -> float:
    """
    Returns the number of shares/units to buy, capped at max_position_pct of portfolio.
    Returns 0 if trade should not proceed.
    """
    if price <= 0:
        log.info(f"Invalid price for position sizing: {price}")
        return 0.0

    portfolio_value = ac.get_portfolio_value()
    cash = ac.get_cash()
    max_value = portfolio_value * T["max_position_pct"]
    affordable = min(max_value, cash)

    if affordable < T["min_trade_value_usd"]:
        log.info(f"Insufficient funds: ${affordable:.2f} < min ${T['min_trade_value_usd']}")
        return 0.0

    qty = affordable / price
    return round(qty, 6)  # supports fractional shares/crypto


# ── Stop-Loss / Take-Profit ───────────────────────────────────────────────────

def stop_loss_price(entry_price: float) -> float:
    return round(entry_price * (1 - T["stop_loss_pct"]), 4)


def take_profit_price(entry_price: float) -> float:
    return round(entry_price * (1 + T["take_profit_pct"]), 4)


# ── Intraday Gate ─────────────────────────────────────────────────────────────

def intraday_allowed() -> bool:
    """Returns True if intraday trading is permitted by config."""
    if not INTRADAY_ENABLED:
        log.debug("Intraday trading is disabled in config.")
    return INTRADAY_ENABLED


# ── Master Pre-Trade Check ────────────────────────────────────────────────────

def pre_trade_check(price: float, symbol: str) -> dict:
    """
    Run all risk checks before entering a trade.
    Returns dict with 'approved' bool, 'qty', and 'reason'.
    """
    result = {"approved": False, "qty": 0.0, "reason": ""}

    if daily_loss_exceeded():
        result["reason"] = "Daily loss limit exceeded"
        return result

    if max_positions_reached():
        result["reason"] = "Max open positions reached"
        return result

    if price <= 0:
        result["reason"] = f"Invalid price for {symbol}: {price}"
        return result

    qty = calculate_position_size(price)
    if qty <= 0:
        result["reason"] = "Insufficient funds or below min trade value"
        return result

    result["approved"] = True
    result["qty"] = qty
    result["reason"] = "OK"
    log.info(f"Pre-trade check PASSED for {symbol}: qty={qty} @ ${price}")
    return result


# ── Exit Check (stop-loss / take-profit) ─────────────────────────────────────

def should_exit_position(symbol: str, entry_price: float, current_price: float) -> tuple:
    """
    Returns (should_exit: bool, reason: str).
    """
    sl = stop_loss_price(entry_price)
    tp = take_profit_price(entry_price)

    if current_price <= sl:
        return True, f"Stop-loss hit: {current_price:.4f} <= {sl:.4f}"
    if current_price >= tp:
        return True, f"Take-profit hit: {current_price:.4f} >= {tp:.4f}"
    return False, ""
