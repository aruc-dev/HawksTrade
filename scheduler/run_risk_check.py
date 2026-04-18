"""
HawksTrade - Risk Check Runner
================================
Called every 15 minutes during market hours.
Checks all open positions for stop-loss and take-profit triggers.
Also enforces the daily loss limit — closes ALL positions if hit.

Run directly:
  python scheduler/run_risk_check.py [--dry-run]
"""

import sys
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
from core import alpaca_client as ac
from core import risk_manager as rm
from core import order_executor as oe
from core.run_markers import RunScope, run_scope
from core.logging_config import runtime_log_handlers
from tracking.trade_log import get_open_trades

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR  = BASE_DIR / "logs"


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=runtime_log_handlers(LOG_DIR, f"risk_{_utc_now().strftime('%Y%m%d')}.log"),
)
log = logging.getLogger("run_risk_check")

with open(BASE_DIR / "config" / "config.yaml") as f:
    CFG = yaml.safe_load(f)


def _position_asset_class(pos) -> str:
    return (
        "crypto"
        if str(getattr(pos, "asset_class", "")).lower().endswith("crypto")
        else "stock"
    )


def _find_matching_trade(symbol: str, open_trades: list) -> dict | None:
    normalized = ac.normalize_symbol(symbol)
    for trade in reversed(open_trades):
        if ac.normalize_symbol(trade.get("symbol", "")) == normalized and trade.get("side") == "buy":
            return trade
    return None


def run(dry_run: bool = False, marker: RunScope | None = None):
    log.info(f"--- Risk Check | dry_run={'ON' if dry_run else 'OFF'} ---")

    # --- Daily loss limit check ---
    try:
        loss_exceeded = rm.daily_loss_exceeded()
    except Exception as e:
        if marker is not None:
            marker.mark_error(stage="daily_loss_check", error_type=type(e).__name__)
        log.error(f"Daily loss check failed; skipping risk check: {e}", exc_info=True)
        return

    if loss_exceeded:
        log.warning("DAILY LOSS LIMIT EXCEEDED. Closing all positions for protection.")
        try:
            positions = ac.get_all_positions()
        except Exception as e:
            log.error(f"Could not fetch positions for emergency close: {e}", exc_info=True)
            return
        open_trades = get_open_trades()
        for pos in positions:
            symbol      = pos.symbol
            asset_class = (
                "crypto"
                if str(getattr(pos, "asset_class", "")).lower().endswith("crypto")
                else "stock"
            )
            trade = _find_matching_trade(symbol, open_trades)
            exit_symbol = trade.get("symbol", symbol) if trade and asset_class == "crypto" else symbol
            oe.exit_position(exit_symbol, reason="Daily loss limit — emergency close",
                             asset_class=asset_class, dry_run=dry_run)
        log.warning("All positions closed. Bot will not trade again today.")
        if marker is not None:
            marker.mark_status("ok", outcome="emergency_close")
        return

    # --- Per-position stop-loss / take-profit check ---
    open_trades = get_open_trades()
    try:
        positions = ac.get_all_positions()
    except Exception as e:
        if marker is not None:
            marker.mark_error(stage="fetch_positions", error_type=type(e).__name__)
        log.error(f"Could not fetch positions for risk check: {e}", exc_info=True)
        return

    if not positions:
        if open_trades:
            log.warning(
                f"Trade log has {len(open_trades)} open row(s), but Alpaca has no open positions; "
                "skipping stale log rows."
            )
        else:
            log.info("No open positions to check.")
        return

    for pos in positions:
        symbol      = pos.symbol
        asset_class = _position_asset_class(pos)
        trade       = _find_matching_trade(symbol, open_trades)
        price_symbol = trade.get("symbol", symbol) if trade and asset_class == "crypto" else symbol

        try:
            entry_price = float(getattr(pos, "avg_entry_price", None) or (trade or {}).get("entry_price"))
        except (ValueError, TypeError):
            log.warning(f"Invalid entry price for {symbol}, skipping.")
            continue

        try:
            if asset_class == "crypto":
                current_price = ac.get_crypto_latest_price(price_symbol)
            else:
                current_price = ac.get_stock_latest_price(price_symbol)
        except Exception as e:
            log.warning(f"Could not get price for {symbol}: {e}")
            continue

        if current_price <= 0:
            log.warning(f"Zero price returned for {symbol}, skipping.")
            continue

        should_exit, reason = rm.should_exit_position(symbol, entry_price, current_price)
        if should_exit:
            log.info(f"EXIT triggered for {symbol}: {reason}")
            exit_symbol = price_symbol if asset_class == "crypto" else symbol
            oe.exit_position(exit_symbol, reason=reason, asset_class=asset_class, dry_run=dry_run)
        else:
            pnl = (current_price - entry_price) / entry_price
            log.info(f"  {symbol:<12} entry={entry_price:.4f} now={current_price:.4f} "
                     f"P&L={pnl:+.2%} — HOLD")

    log.info("Risk check complete.")
    if marker is not None:
        marker.mark_status("ok", outcome="completed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HawksTrade Risk Check")
    parser.add_argument("--dry-run", action="store_true",
                        help="Log intended exits without submitting orders")
    args = parser.parse_args()
    with run_scope(
        log,
        "run_risk_check",
        dry_run="ON" if args.dry_run else "OFF",
    ) as marker:
        run(dry_run=args.dry_run, marker=marker)
