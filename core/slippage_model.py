"""Liquidity-aware slippage estimation for live and simulated orders."""

from __future__ import annotations

import math
from datetime import datetime, time
from typing import Mapping


DEFAULT_CONFIG = {
    "enabled": True,
    "k_stock": 8.0,
    "k_crypto": 15.0,
    "default_stock_volatility_bps": 120.0,
    "default_crypto_volatility_bps": 250.0,
    "default_adv_usd": {
        "stock": 50_000_000.0,
        "crypto": 10_000_000.0,
    },
    "tod_open_window": [["09:30", "09:45"]],
    "tod_close_window": [["15:50", "16:00"]],
    "open_multiplier": 1.5,
    "close_multiplier": 1.5,
    "buy_asymmetry": 1.2,
    "min_stock_bps": 1.0,
    "min_crypto_bps": 5.0,
    "max_bps": 200.0,
    "high_cost_warning_bps": 50.0,
    "per_symbol_overrides": {},
}


def _merged_config(cfg: Mapping | None) -> dict:
    raw = dict(cfg or {})
    merged = dict(DEFAULT_CONFIG)
    for key, value in raw.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            nested = dict(merged[key])
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    return merged


def _asset_class(asset_class: str, symbol: str = "") -> str:
    raw = str(asset_class or "").lower()
    return "crypto" if "crypto" in raw or "/" in str(symbol or "") else "stock"


def _positive(value, default: float | None = None) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) and parsed > 0 else default


def _parse_hhmm(value: str) -> time | None:
    try:
        hour, minute = str(value).split(":", 1)
        return time(int(hour), int(minute))
    except (TypeError, ValueError):
        return None


def _time_of_day(value) -> time | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.time().replace(tzinfo=None)
    if isinstance(value, time):
        return value.replace(tzinfo=None)
    return _parse_hhmm(str(value))


def _in_windows(value, windows) -> bool:
    tod = _time_of_day(value)
    if tod is None:
        return False
    for window in windows or []:
        if not isinstance(window, (list, tuple)) or len(window) != 2:
            continue
        start = _parse_hhmm(window[0])
        end = _parse_hhmm(window[1])
        if start is None or end is None:
            continue
        if start <= tod <= end:
            return True
    return False


def _symbol_multiplier(symbol: str, overrides: Mapping) -> float:
    override = overrides.get(str(symbol or "").upper()) if isinstance(overrides, Mapping) else None
    if isinstance(override, Mapping):
        override = override.get("multiplier")
    return _positive(override, 1.0) or 1.0


def estimate_slippage_bps(
    *,
    symbol: str,
    asset_class: str,
    order_size_usd: float,
    side: str,
    cfg: Mapping | None = None,
    bar_volume_usd: float | None = None,
    adv_usd: float | None = None,
    realised_volatility_bps: float | None = None,
    time_of_day=None,
) -> float:
    """Estimate adverse slippage in basis points for an order."""
    config = _merged_config(cfg)
    klass = _asset_class(asset_class, symbol)
    order_usd = _positive(order_size_usd, 0.0) or 0.0
    if order_usd <= 0:
        return 0.0

    if not bool(config.get("enabled", True)):
        return 0.0

    defaults = config.get("default_adv_usd", {}) if isinstance(config.get("default_adv_usd"), Mapping) else {}
    adv = _positive(adv_usd)
    if adv is None:
        bar_volume = _positive(bar_volume_usd)
        if bar_volume is not None:
            adv = max(bar_volume, bar_volume * (390.0 if klass == "stock" else 1440.0))
        else:
            adv = _positive(defaults.get(klass), 1_000_000.0) or 1_000_000.0

    volatility = _positive(realised_volatility_bps)
    if volatility is None:
        volatility = _positive(
            config.get("default_crypto_volatility_bps" if klass == "crypto" else "default_stock_volatility_bps"),
            100.0,
        ) or 100.0

    k = _positive(config.get("k_crypto" if klass == "crypto" else "k_stock"), 1.0) or 1.0
    participation = min(1.0, max(0.0, order_usd / max(adv, 1.0)))
    liquidity = math.sqrt(participation)
    slippage = k * volatility * liquidity

    if _in_windows(time_of_day, config.get("tod_open_window")):
        slippage *= _positive(config.get("open_multiplier"), 1.0) or 1.0
    if _in_windows(time_of_day, config.get("tod_close_window")):
        slippage *= _positive(config.get("close_multiplier"), 1.0) or 1.0
    if str(side or "").lower() == "buy":
        slippage *= _positive(config.get("buy_asymmetry"), 1.0) or 1.0

    slippage *= _symbol_multiplier(symbol, config.get("per_symbol_overrides", {}))
    min_bps = _positive(config.get("min_crypto_bps" if klass == "crypto" else "min_stock_bps"), 0.0) or 0.0
    max_bps = _positive(config.get("max_bps"), math.inf) or math.inf
    return float(min(max(slippage, min_bps), max_bps))


def realised_slippage_bps(*, side: str, decision_price: float, fill_price: float) -> float:
    """Return positive bps for adverse slippage relative to decision price."""
    decision = _positive(decision_price)
    fill = _positive(fill_price)
    if decision is None or fill is None:
        return 0.0
    if str(side or "").lower() == "sell":
        return ((decision - fill) / decision) * 10000.0
    return ((fill - decision) / decision) * 10000.0
