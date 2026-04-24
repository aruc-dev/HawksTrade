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
import os
from typing import Any, Dict, List

from alpaca.trading.client import TradingClient

from dashboard.config import cfg

log = logging.getLogger("dashboard.alpaca_readonly")

_trading_client: TradingClient | None = None


def _mode_prefix() -> str:
    return "ALPACA_PAPER" if cfg().mode == "paper" else "ALPACA_LIVE"


def _get_dashboard_credentials() -> tuple[str, str, bool]:
    prefix = _mode_prefix()
    key = os.environ.get(f"{prefix}_API_KEY", "").strip()
    secret = os.environ.get(f"{prefix}_SECRET_KEY", "").strip()
    if not key or not secret:
        raise RuntimeError(
            f"Missing {prefix}_API_KEY or {prefix}_SECRET_KEY for dashboard mode={cfg().mode}"
        )
    return key, secret, cfg().mode == "paper"


def _get_trading_client() -> TradingClient:
    global _trading_client
    if _trading_client is None:
        key, secret, paper = _get_dashboard_credentials()
        _trading_client = TradingClient(key, secret, paper=paper)
    return _trading_client


def normalize_symbol(symbol: str) -> str:
    return str(symbol or "").replace("/", "").upper()


def get_account() -> Any:
    return _get_trading_client().get_account()


def get_all_positions() -> List[Any]:
    return list(_get_trading_client().get_all_positions())


def get_position(symbol: str) -> Any:
    return _get_trading_client().get_open_position(normalize_symbol(symbol))


def get_portfolio_value() -> float:
    return float(get_account().portfolio_value)


def get_cash() -> float:
    return float(get_account().cash)


def get_buying_power() -> float:
    return float(get_account().buying_power)


def _account_value_as_float(acct: Any, field_name: str, default: float = 0.0) -> float:
    try:
        if isinstance(acct, dict):
            value = acct.get(field_name, default)
        else:
            value = getattr(acct, field_name, default)
        return float(value or default)
    except (TypeError, ValueError):
        return default

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


def get_account_summary(account: Any | None = None) -> Dict[str, float]:
    """Portfolio value, cash, buying power. Empty dict on failure."""
    try:
        account = account if account is not None else get_account()
        return {
            "portfolio_value": _account_value_as_float(account, "portfolio_value"),
            "cash": _account_value_as_float(account, "cash"),
            "buying_power": _account_value_as_float(account, "buying_power"),
        }
    except Exception as e:
        log.warning("Could not fetch account summary: %s", e)
        return {}


def alpaca_reachable(account: Any | None = None) -> bool:
    """Fast liveness check — used by /healthz."""
    try:
        acct = account if account is not None else get_account()
        return acct is not None
    except Exception:
        return False
