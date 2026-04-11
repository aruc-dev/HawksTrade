"""
HawksTrade - Order Executor
============================
Handles placing, confirming, and logging all orders.
Uses risk_manager checks before every entry.
Writes every trade to the trade log.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from core import alpaca_client as ac
from core import risk_manager as rm
from tracking.trade_log import log_trade, mark_trade_closed

BASE_DIR = Path(__file__).resolve().parent.parent
with open(BASE_DIR / "config" / "config.yaml") as f:
    CFG = yaml.safe_load(f)

ORDER_TYPE = CFG["trading"]["order_type"]
SLIPPAGE   = CFG["trading"]["limit_slippage_pct"]
MODE       = CFG["mode"]

log = logging.getLogger("order_executor")


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def enter_position(symbol: str, strategy: str, asset_class: str = "stock", dry_run: bool = False) -> Optional[dict]:
    """
    Full entry flow:
      1. Get current price
      2. Run pre-trade risk checks
      3. Place order (market or limit)
      4. Log the trade
    Returns trade dict or None on failure.
    """
    try:
        if asset_class == "crypto":
            price = ac.get_crypto_latest_price(symbol)
        else:
            price = ac.get_stock_latest_price(symbol)

        check = rm.pre_trade_check(price, symbol)
        if not check["approved"]:
            log.info(f"Entry blocked for {symbol}: {check['reason']}")
            return None

        qty = check["qty"]
        sl  = rm.stop_loss_price(price)
        tp  = rm.take_profit_price(price)

        if dry_run:
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
                "order_id":    "DRY-RUN",
                "status":      "dry_run",
            }
            log.info(
                f"DRY RUN: would enter {symbol} | strategy={strategy} | "
                f"qty={qty} | price={price} | stop={sl} | target={tp}"
            )
            return trade

        if ORDER_TYPE == "market":
            order = ac.place_market_order(symbol, qty, "buy")
        else:
            limit_px = round(price * (1 + SLIPPAGE), 4)
            order = ac.place_limit_order(symbol, qty, "buy", limit_px)

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
            "order_id":    str(order.id),
            "status":      "open",
        }
        log_trade(trade)
        log.info(f"ENTERED {symbol} | strategy={strategy} | qty={qty} | price={price}")
        return trade

    except Exception as e:
        log.error(f"Failed to enter {symbol}: {e}", exc_info=True)
        return None


def exit_position(symbol: str, reason: str, asset_class: str = "stock", dry_run: bool = False) -> Optional[dict]:
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

        if dry_run:
            trade = {
                "timestamp":     _utc_now().isoformat(),
                "mode":          MODE,
                "symbol":        symbol,
                "strategy":      "exit",
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
                f"DRY RUN: would exit {symbol} | reason={reason} | "
                f"entry={entry_price} exit={current_price} pnl={pnl_pct:.2%}"
            )
            return trade

        if ORDER_TYPE == "market":
            order = ac.place_market_order(symbol, qty, "sell")
        else:
            limit_px = round(current_price * (1 - SLIPPAGE), 4)
            order = ac.place_limit_order(symbol, qty, "sell", limit_px)

        trade = {
            "timestamp":     _utc_now().isoformat(),
            "mode":          MODE,
            "symbol":        symbol,
            "strategy":      "exit",
            "asset_class":   asset_class,
            "side":          "sell",
            "qty":           qty,
            "entry_price":   entry_price,
            "exit_price":    current_price,
            "pnl_pct":       round(pnl_pct, 6),
            "exit_reason":   reason,
            "order_id":      str(order.id),
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
