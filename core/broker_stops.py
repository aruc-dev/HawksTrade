"""
HawksTrade - Broker Protective Stop Sync
========================================
Synchronizes broker-side protective sell orders for open tracked positions.
Stocks use stop orders; crypto uses stop-limit orders because Alpaca does not
support plain crypto stop orders.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from typing import Any

from core import alpaca_client as ac
from core.config_loader import get_config
from tracking.trade_log import get_open_trades


CFG = get_config()
log = logging.getLogger("core.broker_stops")


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _float_or_none(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _broker_stops_config() -> dict:
    return dict(CFG.get("broker_stops", {}) or {})


def _enabled() -> bool:
    return bool(_broker_stops_config().get("enabled", True))


def _should_submit_orders(dry_run: bool) -> bool:
    if dry_run:
        return False
    cfg = _broker_stops_config()
    mode = str(CFG.get("mode", "paper") or "paper").strip().lower()
    return mode == "live" or bool(cfg.get("submit_in_paper", False))


def _position_asset_class(position: Any) -> str:
    raw = _enum_value(_value(position, "asset_class", ""))
    return "crypto" if "crypto" in raw else "stock"


def _position_map(positions: Iterable[Any] | None) -> dict[str, Any]:
    mapped: dict[str, Any] = {}
    for pos in positions or []:
        symbol = str(_value(pos, "symbol", "") or "")
        if symbol:
            mapped[ac.normalize_symbol(symbol)] = pos
    return mapped


def _protective_sell_order_symbols(open_orders: Iterable[Any]) -> set[str]:
    symbols: set[str] = set()
    for order in open_orders or []:
        if _enum_value(_value(order, "side", "")) != "sell":
            continue
        order_type = _enum_value(_value(order, "type", ""))
        if order_type not in {"stop", "stop_limit", "trailing_stop"}:
            continue
        symbol = str(_value(order, "symbol", "") or "")
        if symbol:
            symbols.add(ac.normalize_symbol(symbol))
    return symbols


def _open_orders_for_submit() -> tuple[list, bool]:
    try:
        return list(ac.get_open_orders() or []), True
    except Exception as exc:
        log.warning("[BrokerStops] Could not fetch open broker orders: %s", exc)
        return [], False


def _trade_symbol_for_order(trade: dict, position: Any | None, asset_class: str) -> str:
    trade_symbol = str(trade.get("symbol", "") or "")
    position_symbol = str(_value(position, "symbol", "") or "")
    if asset_class == "crypto":
        return trade_symbol or ac.to_crypto_pair_symbol(position_symbol)
    return position_symbol or trade_symbol


def _trade_quantity(trade: dict, position: Any | None) -> float | None:
    position_qty = _float_or_none(_value(position, "qty", None))
    if position_qty and position_qty > 0:
        return position_qty
    trade_qty = _float_or_none(trade.get("qty"))
    if trade_qty and trade_qty > 0:
        return trade_qty
    return None


def _crypto_limit_price(stop_price: float) -> float:
    cfg = _broker_stops_config()
    offset = _float_or_none(cfg.get("crypto_stop_limit_offset_pct"))
    if offset is None:
        offset = 0.005
    offset = max(0.0, min(offset, 0.10))
    return stop_price * (1.0 - offset)


def sync_broker_stops(
    *,
    dry_run: bool = False,
    positions: Iterable[Any] | None = None,
    open_trades: list[dict] | None = None,
) -> dict:
    """Ensure each open tracked position has a broker-side protective sell."""
    summary = {
        "enabled": _enabled(),
        "dry_run": dry_run,
        "submit_orders": False,
        "placed": 0,
        "would_place": 0,
        "existing": 0,
        "skipped": 0,
        "failed": 0,
    }
    if not summary["enabled"]:
        log.info("[BrokerStops] Sync skipped: disabled.")
        return summary

    trades = list(open_trades if open_trades is not None else get_open_trades())
    if not trades:
        log.info("[BrokerStops] Sync skipped: no open trade rows.")
        return summary

    submit_orders = _should_submit_orders(dry_run)
    summary["submit_orders"] = submit_orders
    positions_by_symbol = _position_map(positions)
    existing_sell_symbols: set[str] = set()
    if submit_orders:
        open_orders, open_orders_ok = _open_orders_for_submit()
        if not open_orders_ok:
            summary["failed"] += 1
            log.warning("[BrokerStops] Sync skipped: cannot confirm existing broker stops.")
            return summary
        existing_sell_symbols = _protective_sell_order_symbols(open_orders)

    for trade in trades:
        raw_symbol = str(trade.get("symbol", "") or "")
        normalized = ac.normalize_symbol(raw_symbol)
        if not normalized:
            summary["skipped"] += 1
            continue

        position = positions_by_symbol.get(normalized)
        asset_class = str(trade.get("asset_class", "") or "").strip().lower()
        if asset_class not in {"stock", "crypto"}:
            asset_class = _position_asset_class(position)
        symbol = _trade_symbol_for_order(trade, position, asset_class)
        qty = _trade_quantity(trade, position)
        stop_price = _float_or_none(trade.get("stop_loss"))

        if not symbol or qty is None or stop_price is None or stop_price <= 0:
            log.debug("[BrokerStops] Skipping %s: missing symbol, qty, or stop.", raw_symbol)
            summary["skipped"] += 1
            continue
        if normalized in existing_sell_symbols:
            summary["existing"] += 1
            continue
        if not submit_orders:
            log.info(
                "[BrokerStops] Would place protective %s for %s qty=%s stop=%.6f",
                "stop-limit" if asset_class == "crypto" else "stop",
                symbol,
                qty,
                stop_price,
            )
            summary["would_place"] += 1
            continue

        try:
            if asset_class == "crypto":
                ac.place_stop_limit_order(
                    symbol=symbol,
                    qty=qty,
                    side="sell",
                    stop_price=stop_price,
                    limit_price=_crypto_limit_price(stop_price),
                    strategy="broker_stop",
                    asset_class=asset_class,
                )
            else:
                ac.place_stop_order(
                    symbol=symbol,
                    qty=qty,
                    side="sell",
                    stop_price=stop_price,
                    strategy="broker_stop",
                    asset_class=asset_class,
                )
            summary["placed"] += 1
        except Exception as exc:
            log.error("[BrokerStops] Failed to place protective stop for %s: %s", symbol, exc)
            summary["failed"] += 1

    log.info("[BrokerStops] Sync complete: %s", summary)
    return summary
