"""
HawksTrade - Order Executor
============================
Handles placing, confirming, and logging all orders.
Uses risk_manager checks before every entry.
Writes every trade to the trade log.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict
from pathlib import Path

import yaml

from core import alpaca_client as ac
from core import risk_manager as rm
from tracking.trade_log import log_trade, mark_trade_closed, get_trade_age_days

# ── Setup ───────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent
with open(BASE_DIR / "config" / "config.yaml") as f:
    CFG = yaml.safe_load(f)

MODE        = CFG["mode"]
ORDER_TYPE  = CFG["trading"]["order_type"]
SLIPPAGE    = CFG["trading"]["limit_slippage_pct"]
log         = logging.getLogger("core.order_executor")


def _utc_now():
    return datetime.now(timezone.utc)


# ── Entry Logic ─────────────────────────────────────────────────────────────

def enter_position(symbol: str, strategy: str, asset_class: str = "stock", dry_run: bool = False) -> Optional[dict]:
    """
    Open a new position.
      1. Check risk rules (daily loss, max positions, size)
      2. Calculate qty
      3. Place order (limit or market)
      4. Log the trade
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

        # Risk Check
        check = rm.pre_trade_check(price, symbol)
        if not check["approved"]:
            log.info(f"Entry blocked for {symbol}: {check['reason']}")
            return None

        qty = check["qty"]
        # Kelly override for momentum — uses dynamic rolling 30-trade params
        if strategy == "momentum":
            kelly_qty = rm.kelly_position_size(price=price)
            if kelly_qty > 0:
                qty = kelly_qty

        sl = rm.stop_loss_price(price)
        tp = rm.take_profit_price(price)

        if dry_run:
            log.info(f"DRY RUN: would buy {qty} {symbol} @ {price}")
            return {"symbol": symbol, "status": "dry_run"}

        # Place Order
        if ORDER_TYPE == "market":
            order = ac.place_market_order(symbol, qty, "buy", strategy=strategy)
        else:
            limit_px = round(price * (1 + SLIPPAGE), 4)
            order = ac.place_limit_order(symbol, qty, "buy", limit_px, strategy=strategy)

        # Capture details for logging
        order_id = str(order.id) if hasattr(order, "id") else str(order.get("order_id"))
        trade = {
            "timestamp":   _utc_now().isoformat(),
            "mode":        MODE,
            "symbol":      symbol,
            "strategy":    strategy,
            "asset_class": asset_class,
            "side":        "buy",
            "qty":         qty,
            "entry_price": price,
            "stop_loss":   sl,
            "take_profit": tp,
            "order_id":    order_id,
            "status":      "open",
        }
        log_trade(trade)
        log.info(f"ENTERED {symbol} | strategy={strategy} | qty={qty} | price={price}")
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

        qty = abs(float(position.qty))

        if asset_class == "crypto":
            current_price = ac.get_crypto_latest_price(symbol)
        else:
            current_price = ac.get_stock_latest_price(symbol)

        entry_price = float(position.avg_entry_price)
        pnl_pct     = (current_price - entry_price) / entry_price

        # Retrieve strategy from local open trades if possible
        strategy = "unknown"
        if open_trades_callback:
            open_trades = open_trades_callback()
        else:
            from tracking.trade_log import get_open_trades
            open_trades = get_open_trades()

        normalized_symbol = ac.normalize_symbol(symbol)
        for t in open_trades:
            trade_symbol = t.get("symbol", "")
            if trade_symbol == symbol or ac.normalize_symbol(trade_symbol) == normalized_symbol:
                strategy = t.get("strategy", "unknown")
                break

        if dry_run:
            trade = {
                "timestamp":     _utc_now().isoformat(),
                "mode":          MODE,
                "symbol":        symbol,
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
                f"DRY RUN: would exit {symbol} | strategy={strategy} | reason={reason} | "
                f"entry={entry_price} exit={current_price} pnl={pnl_pct:.2%}"
            )
            return trade

        if ORDER_TYPE == "market":
            order = ac.place_market_order(symbol, qty, "sell", strategy=strategy)
        else:
            limit_px = round(current_price * (1 - SLIPPAGE), 4)
            order = ac.place_limit_order(symbol, qty, "sell", limit_px, strategy=strategy)

        order_id = str(order.id) if hasattr(order, "id") else str(order.get("order_id"))
        trade = {
            "timestamp":     _utc_now().isoformat(),
            "mode":          MODE,
            "symbol":        symbol,
            "strategy":      strategy,
            "asset_class":   asset_class,
            "side":          "sell",
            "qty":           qty,
            "entry_price":   entry_price,
            "exit_price":    current_price,
            "pnl_pct":       round(pnl_pct, 6),
            "exit_reason":   reason,
            "order_id":      order_id,
            "status":        "closed",
        }
        log_trade(trade)
        mark_trade_closed(symbol, current_price, pnl_pct, reason)
        log.info(
            f"EXITED {symbol} | reason={reason} | "
            f"entry={entry_price} exit={current_price} pnl={pnl_pct:.2%}"
        )
        return trade

    except Exception as e:
        log.error(f"Failed to exit {symbol}: {e}", exc_info=True)
        return None
