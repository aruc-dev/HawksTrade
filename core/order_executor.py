"""
HawksTrade - Order Executor
============================
Handles placing, confirming, and logging all orders.
Uses risk_manager checks before every entry.
Writes every trade to the trade log.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional, List, Dict
from pathlib import Path


from core import alpaca_client as ac
from core import risk_manager as rm
from core.config_loader import get_config
from tracking import order_intents
from tracking.trade_log import log_trade, mark_trade_closed, get_trade_age_days

# ── Setup ───────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent
CFG = get_config()

MODE        = CFG["mode"]
ORDER_TYPE  = CFG["trading"]["order_type"]
SLIPPAGE    = CFG["trading"]["limit_slippage_pct"]
log         = logging.getLogger("core.order_executor")


class PendingExitOrderCheckFailed(RuntimeError):
    """Raised when open sell orders cannot be inspected safely."""


def _utc_now():
    return datetime.now(timezone.utc)


def _symbols_match(left: str, right: str) -> bool:
    return ac.normalize_symbol(left) == ac.normalize_symbol(right)


def _order_value(order, name: str, default=None):
    if isinstance(order, dict):
        return order.get(name, default)
    return getattr(order, name, default)


def _order_status(order) -> str | None:
    status = _order_value(order, "status")
    if status is None:
        return None
    return str(getattr(status, "value", status)).lower()


def _order_side(order) -> str | None:
    side = _order_value(order, "side")
    if side is None:
        return None
    return str(getattr(side, "value", side)).lower()


def _broker_order_id(order) -> str:
    return str(_order_value(order, "id", _order_value(order, "order_id", "")) or "")


def _current_run_id() -> str:
    return os.getenv("HAWKSTRADE_RUN_ID") or "manual"


def _create_order_intent(symbol: str, side: str, strategy: str, asset_class: str, qty, limit_price=None) -> dict | None:
    if MODE == "backtest":
        return None
    intent, created = order_intents.get_or_create_order_intent(
        run_id=_current_run_id(),
        symbol=symbol,
        side=side,
        strategy=strategy,
        asset_class=asset_class,
        qty=qty,
        limit_price=limit_price,
    )
    action = "created" if created else "reused"
    log.info(f"Order intent {action}: {intent['client_order_id']} | {side} {symbol} | strategy={strategy}")
    return intent


def _mark_order_intent_submitted(intent: dict | None, order) -> None:
    if not intent:
        return
    status = _order_status(order) or "submitted"
    order_intents.update_order_intent(
        intent["client_order_id"],
        status=status,
        broker_order_id=_broker_order_id(order),
    )


def _mark_order_intent_failed(intent: dict | None, exc: Exception) -> None:
    if not intent:
        return
    order_intents.update_order_intent(
        intent["client_order_id"],
        status="submit_failed",
        error=f"{type(exc).__name__}: {exc}",
    )


def _has_pending_exit_order(symbol: str) -> bool:
    try:
        orders = ac.get_open_orders()
    except Exception as e:
        log.error(f"Could not check pending exit orders for {symbol}; blocking exit fail-closed: {e}")
        raise PendingExitOrderCheckFailed(f"Could not check pending exit orders for {symbol}") from e

    for order in orders or []:
        order_symbol = _order_value(order, "symbol", "")
        if _order_side(order) == "sell" and _symbols_match(str(order_symbol), symbol):
            order_id = _order_value(order, "id", _order_value(order, "order_id", "unknown"))
            log.warning(f"Pending sell order already exists for {symbol}; skipping duplicate exit. order_id={order_id}")
            return True
    return False


def _order_filled_qty(order) -> float:
    raw_qty = _order_value(order, "filled_qty", None)
    if raw_qty in (None, ""):
        return 0.0
    try:
        return abs(float(str(raw_qty)))
    except (TypeError, ValueError):
        return 0.0


def _order_filled_avg_price(order, fallback: float) -> float:
    raw_price = _order_value(order, "filled_avg_price", None)
    if raw_price in (None, ""):
        return fallback
    try:
        filled_avg_price = float(str(raw_price))
    except (TypeError, ValueError):
        return fallback
    return filled_avg_price if filled_avg_price > 0 else fallback


def _entry_fill_qty(order, requested_qty: float) -> float:
    """Return the broker-confirmed entry quantity that should count as exposure."""
    status = _order_status(order)
    filled_qty = _order_filled_qty(order)
    if status is None:
        return requested_qty
    if status == "filled":
        return filled_qty or requested_qty
    if filled_qty >= requested_qty:
        return filled_qty
    if status == "partially_filled" or filled_qty > 0:
        return filled_qty
    return 0.0


def _entry_log_status(order, requested_qty: float, filled_qty: float) -> str:
    status = _order_status(order)
    if status is None or status == "filled" or filled_qty >= requested_qty:
        return "open"
    if status == "partially_filled" or filled_qty > 0:
        return "partially_filled"
    return "submitted"


def _exit_fill_qty(order, requested_qty: float) -> float:
    """
    Return the quantity that is safe to remove from trades.csv.

    Alpaca limit exits can be accepted but not filled immediately. In that case
    the broker position still exists and the local trade log must stay open.
    """
    status = _order_status(order)
    filled_qty = _order_filled_qty(order)
    if status is None:
        return requested_qty
    if status == "filled":
        return filled_qty or requested_qty
    if status == "partially_filled":
        return filled_qty
    return 0.0


def _exit_log_status(order, requested_qty: float, filled_qty: float) -> str:
    status = _order_status(order)
    if status is None or status == "filled":
        return "closed"
    if status == "partially_filled" or (0 < filled_qty < requested_qty):
        return "partially_filled"
    return "submitted"


def _submitted_order_is_transient(order) -> bool:
    return (_order_status(order) or "") in {
        "accepted",
        "accepted_for_bidding",
        "new",
        "pending_new",
        "pending_replace",
        "pending_review",
    }


# ── Entry Logic ─────────────────────────────────────────────────────────────

def enter_position(
    symbol: str,
    strategy: str,
    asset_class: str = "stock",
    dry_run: bool = False,
    suggested_qty: Optional[float] = None,
    atr_stop_price: Optional[float] = None,
) -> Optional[dict]:
    """
    Open a new position.
      1. Check risk rules (daily loss, max positions, size)
      2. Calculate qty (ATR-risk qty > Kelly > portfolio-pct, whichever applies)
      3. Place order (limit or market)
      4. Log the trade

    suggested_qty: ATR-risk-based quantity from the strategy signal; takes
        priority over Kelly when provided and positive.
    atr_stop_price: volatility-adjusted stop; written to the trade log so the
        live risk check can use it as the effective stop price.
    """
    try:
        # Get latest price
        if asset_class == "crypto":
            price = ac.get_crypto_latest_price(symbol)
        else:
            price = ac.get_stock_latest_price(symbol)

        if price <= 0:
            log.warning(f"Invalid price for {symbol}: {price}. Skipping entry.")
            return None

        # Risk Check (asset-class-aware for crypto reservation/cap enforcement)
        check = rm.pre_trade_check(price, symbol, asset_class=asset_class)
        if not check["approved"]:
            log.info(f"Entry blocked for {symbol}: {check['reason']}")
            return None

        qty = check["qty"]
        if suggested_qty and suggested_qty > 0:
            # ATR-risk sizing from signal takes priority
            qty = suggested_qty
        elif strategy == "momentum":
            # Kelly override for momentum — uses dynamic rolling 30-trade params
            kelly_qty = rm.kelly_position_size(price=price)
            if kelly_qty > 0:
                qty = kelly_qty
        capped_qty = rm.cap_position_qty(price, qty)
        if capped_qty <= 0:
            log.info(f"Entry blocked for {symbol}: capped quantity is zero.")
            return None
        if capped_qty < qty:
            log.info(f"Entry size capped for {symbol}: requested={qty} capped={capped_qty}")
        qty = capped_qty

        if dry_run:
            log.info(f"DRY RUN: would buy {qty} {symbol} @ {price}")
            return {"symbol": symbol, "status": "dry_run"}

        # Place Order
        if ORDER_TYPE == "market":
            intent = _create_order_intent(symbol, "buy", strategy, asset_class, qty)
            try:
                order = ac.place_market_order(
                    symbol,
                    qty,
                    "buy",
                    strategy=strategy,
                    client_order_id=intent["client_order_id"] if intent else None,
                )
            except Exception as e:
                _mark_order_intent_failed(intent, e)
                raise
        else:
            limit_px = price * (1 + SLIPPAGE)
            intent = _create_order_intent(symbol, "buy", strategy, asset_class, qty, limit_price=limit_px)
            try:
                order = ac.place_limit_order(
                    symbol,
                    qty,
                    "buy",
                    limit_px,
                    strategy=strategy,
                    asset_class=asset_class,
                    client_order_id=intent["client_order_id"] if intent else None,
                )
            except Exception as e:
                _mark_order_intent_failed(intent, e)
                raise
        _mark_order_intent_submitted(intent, order)

        # Capture details for logging
        order_id = str(order.id) if hasattr(order, "id") else str(order.get("order_id"))
        filled_qty = _entry_fill_qty(order, qty)
        action_status = _entry_log_status(order, qty, filled_qty)
        logged_qty = filled_qty if filled_qty > 0 else qty
        entry_price = _order_filled_avg_price(order, price) if filled_qty > 0 else price
        global_sl = rm.stop_loss_price(entry_price)
        # Use ATR stop when it widens the stop below the global floor; otherwise global governs.
        sl = atr_stop_price if (atr_stop_price is not None and atr_stop_price < global_sl) else global_sl
        tp = rm.take_profit_price(entry_price)
        trade = {
            "timestamp":   _utc_now().isoformat(),
            "mode":        MODE,
            "symbol":      symbol,
            "strategy":    strategy,
            "asset_class": asset_class,
            "side":        "buy",
            "qty":         logged_qty,
            "entry_price": entry_price,
            "stop_loss":   sl,
            "take_profit": tp,
            "order_id":    order_id,
            "status":      action_status,
        }
        log_trade(trade)
        if action_status == "open":
            log.info(f"ENTERED {symbol} | strategy={strategy} | qty={logged_qty} | price={entry_price}")
        elif action_status == "partially_filled":
            log.warning(
                f"Entry order partially filled for {symbol}; logged filled exposure only | "
                f"strategy={strategy} | filled_qty={filled_qty} requested_qty={qty}"
            )
        else:
            entry_log = log.info if _submitted_order_is_transient(order) else log.warning
            entry_log(
                f"Entry order submitted for {symbol} but not filled yet; "
                f"trade log status=submitted | strategy={strategy} | requested_qty={qty}"
            )
        return trade

    except Exception as e:
        log.error(f"Failed to enter {symbol}: {e}", exc_info=True)
        return None


def exit_position(symbol: str, reason: str, asset_class: str = "stock", dry_run: bool = False, open_trades_callback=None) -> Optional[dict]:
    """
    Close an open position fully.
      1. Check position exists
      2. Place sell order
      3. Log the trade
    """
    try:
        position = ac.get_position(symbol)
        if not position:
            log.info(f"No open position for {symbol}, skipping exit.")
            return None

        qty = float(position.qty)
        if qty <= 0:
            log.error(
                f"exit_position called on non-long position for {symbol} (qty={qty}); "
                "HawksTrade is long-only. Skipping exit."
            )
            return None

        if asset_class == "crypto":
            current_price = ac.get_crypto_latest_price(symbol)
        else:
            current_price = ac.get_stock_latest_price(symbol)

        entry_price = float(position.avg_entry_price)
        pnl_pct     = (current_price - entry_price) / entry_price

        # Retrieve strategy and canonical symbol from local open trades if possible.
        strategy = "unknown"
        if open_trades_callback:
            open_trades = open_trades_callback()
        else:
            from tracking.trade_log import get_open_trades
            open_trades = get_open_trades()

        trade_symbol = symbol
        for t in reversed(open_trades):
            if _symbols_match(t["symbol"], symbol):
                strategy = t.get("strategy", "unknown")
                trade_symbol = t.get("symbol") or symbol
                break

        order_symbol = trade_symbol if asset_class == "crypto" else symbol

        if dry_run:
            trade = {
                "timestamp":     _utc_now().isoformat(),
                "mode":          MODE,
                "symbol":        trade_symbol,
                "strategy":      strategy,
                "asset_class":   asset_class,
                "side":          "sell",
                "qty":           qty,
                "entry_price":   entry_price,
                "exit_price":    current_price,
                "pnl_pct":       round(pnl_pct, 6),
                "exit_reason":   reason,
                "order_id":      "DRY-RUN",
                "status":        "dry_run",
            }
            log.info(
                f"DRY RUN: would exit {trade_symbol} | strategy={strategy} | reason={reason} | "
                f"entry={entry_price} exit={current_price} pnl={pnl_pct:.2%}"
            )
            return trade

        try:
            has_pending_exit = _has_pending_exit_order(order_symbol)
        except PendingExitOrderCheckFailed as e:
            return {
                "timestamp": _utc_now().isoformat(),
                "mode": MODE,
                "symbol": trade_symbol,
                "strategy": strategy,
                "asset_class": asset_class,
                "side": "sell",
                "qty": qty,
                "entry_price": entry_price,
                "exit_price": current_price,
                "pnl_pct": round(pnl_pct, 6),
                "exit_reason": reason,
                "order_id": "",
                "status": "pending_exit_check_failed",
                "error": str(e),
            }

        if has_pending_exit:
            return {
                "timestamp": _utc_now().isoformat(),
                "mode": MODE,
                "symbol": trade_symbol,
                "strategy": strategy,
                "asset_class": asset_class,
                "side": "sell",
                "qty": qty,
                "entry_price": entry_price,
                "exit_price": current_price,
                "pnl_pct": round(pnl_pct, 6),
                "exit_reason": reason,
                "order_id": "",
                "status": "pending_exit",
            }

        if ORDER_TYPE == "market":
            intent = _create_order_intent(order_symbol, "sell", strategy, asset_class, qty)
            try:
                order = ac.place_market_order(
                    order_symbol,
                    qty,
                    "sell",
                    strategy=strategy,
                    client_order_id=intent["client_order_id"] if intent else None,
                )
            except Exception as e:
                _mark_order_intent_failed(intent, e)
                raise
        else:
            limit_px = current_price * (1 - SLIPPAGE)
            intent = _create_order_intent(order_symbol, "sell", strategy, asset_class, qty, limit_price=limit_px)
            try:
                order = ac.place_limit_order(
                    order_symbol,
                    qty,
                    "sell",
                    limit_px,
                    strategy=strategy,
                    asset_class=asset_class,
                    client_order_id=intent["client_order_id"] if intent else None,
                )
            except Exception as e:
                _mark_order_intent_failed(intent, e)
                raise
        _mark_order_intent_submitted(intent, order)

        order_id = str(order.id) if hasattr(order, "id") else str(order.get("order_id"))
        filled_qty = _exit_fill_qty(order, qty)
        action_status = _exit_log_status(order, qty, filled_qty)
        logged_qty = filled_qty if filled_qty > 0 else qty
        trade = {
            "timestamp":     _utc_now().isoformat(),
            "mode":          MODE,
            "symbol":        trade_symbol,
            "strategy":      strategy,
            "asset_class":   asset_class,
            "side":          "sell",
            "qty":           logged_qty,
            "entry_price":   entry_price,
            "exit_price":    current_price,
            "pnl_pct":       round(pnl_pct, 6),
            "exit_reason":   reason,
            "order_id":      order_id,
            "status":        action_status,
        }
        log_trade(trade)
        if filled_qty > 0:
            mark_trade_closed(trade_symbol, current_price, pnl_pct, reason, closed_qty=filled_qty)
            log.info(
                f"EXITED {trade_symbol} | reason={reason} | qty={filled_qty} | "
                f"entry={entry_price} exit={current_price} pnl={pnl_pct:.2%}"
            )
        else:
            exit_log = log.info if _submitted_order_is_transient(order) else log.warning
            exit_log(
                f"Exit order submitted for {trade_symbol} but not filled yet; "
                f"leaving trade log open | reason={reason} | status={_order_status(order)}"
            )
        return trade

    except Exception as e:
        log.error(f"Failed to exit {symbol}: {e}", exc_info=True)
        return None
