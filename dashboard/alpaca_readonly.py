"""Read-only Alpaca client wrapper for the dashboard.

SECURITY CONTRACT:
  This module ONLY imports GET-style functions from core.alpaca_client. It MUST
  NEVER import or re-export any mutation function (place_*, cancel_*, close_*).
  A unit test in tests/test_alpaca_readonly.py enforces this by inspecting the
  module's globals.

  The Alpaca credentials used by the dashboard should be a DEDICATED read-only
  key pair, stored in /etc/hawkstrade-dash/env (mode 600, owned by
  hawkstrade-dash). Even if this wrapper accidentally allowed a mutating call,
  the credential itself should be read-only at the Alpaca account level where
  possible.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

# Explicitly import ONLY the read functions. Do not `from ... import *`.
from core.alpaca_client import (
    get_account,
    get_all_positions,
    get_buying_power,
    get_cash,
    get_position,
    get_portfolio_value,
    normalize_symbol,
)

log = logging.getLogger("dashboard.alpaca_readonly")

_READ_FUNCTIONS = {
    "get_account": get_account,
    "get_all_positions": get_all_positions,
    "get_buying_power": get_buying_power,
    "get_cash": get_cash,
    "get_position": get_position,
    "get_portfolio_value": get_portfolio_value,
    "normalize_symbol": normalize_symbol,
}

# Hard allowlist — referenced by test_alpaca_readonly.py to guarantee nothing
# else ever sneaks in.
ALLOWED_FUNCTIONS = frozenset(_READ_FUNCTIONS)

# Hard denylist — referenced to guarantee these are never imported here.
FORBIDDEN_FUNCTIONS = frozenset({
    "place_market_order",
    "place_limit_order",
    "cancel_order",
    "close_position",
    "close_all_positions",
    "submit_order",
})


def _position_to_dict(pos: Any) -> Dict[str, Any]:
    """Convert an Alpaca position object into a plain dict for JSON response.

    Defensive: falls back to empty strings for missing fields rather than
    raising, so partial/legacy positions don't crash the dashboard.
    """
    def _g(name: str, default: Any = None) -> Any:
        if isinstance(pos, dict):
            return pos.get(name, default)
        return getattr(pos, name, default)

    asset_class = _g("asset_class")
    if asset_class is not None and hasattr(asset_class, "value"):
        asset_class = asset_class.value

    def _f(name: str, default: float = 0.0) -> float:
        try:
            return float(_g(name, default) or default)
        except (TypeError, ValueError):
            return default

    return {
        "symbol": str(_g("symbol", "") or ""),
        "qty": _f("qty"),
        "avg_entry_price": _f("avg_entry_price"),
        "current_price": _f("current_price"),
        "market_value": _f("market_value"),
        "cost_basis": _f("cost_basis"),
        "unrealized_pl": _f("unrealized_pl"),
        "unrealized_plpc": _f("unrealized_plpc"),
        "asset_class": str(asset_class or ""),
        "side": str(_g("side", "") or ""),
    }


def get_positions_as_dicts() -> List[Dict[str, Any]]:
    """Fetch all open positions and return as plain dicts for JSON responses."""
    try:
        positions = get_all_positions() or []
    except Exception as e:
        log.warning("Could not fetch positions from Alpaca: %s", e)
        return []
    return [_position_to_dict(p) for p in positions]


def get_account_summary() -> Dict[str, float]:
    """Portfolio value, cash, buying power. Empty dict on failure."""
    try:
        return {
            "portfolio_value": float(get_portfolio_value()),
            "cash": float(get_cash()),
            "buying_power": float(get_buying_power()),
        }
    except Exception as e:
        log.warning("Could not fetch account summary: %s", e)
        return {}


def alpaca_reachable() -> bool:
    """Fast liveness check — used by /healthz."""
    try:
        acct = get_account()
        return acct is not None
    except Exception:
        return False
