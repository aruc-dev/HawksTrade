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
from tracking.trade_log import get_open_trades

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR  = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / f"risk_{_utc_now().strftime('%Y%m%d')}.log"),
    ],
)
log = logging.getLogger("run_risk_check")

with open(BASE_DIR / "config" / "config.yaml") as f:
    CFG = yaml.safe_load(f)


def run(dry_run: bool = False):
    log.info(f"--- Risk Check | dry_run={'ON' if dry_run else 'OFF'} ---")

    # --- Daily loss limit check ---
    try:
        loss_exceeded = rm.daily_loss_exceeded()
    except Exception as e:
        log.error(f"Daily loss check failed; skipping risk check: {e}", exc_info=True)
        return

    if loss_exceeded:
        log.warning("DAILY LOSS LIMIT EXCEEDED. Closing all positions for protection.")
        try:
            positions = ac.get_all_positions()
        except Exception as e:
            log.error(f"Could not fetch positions for emergency close: {e}", exc_info=True)
            return
        for pos in positions:
            symbol      = pos.symbol
            asset_class = (
                "crypto"
                if str(getattr(pos, "asset_class", "")).lower().endswith("crypto")
                else "stock"
            )
            oe.exit_position(symbol, reason="Daily loss limit — emergency close",
                             asset_class=asset_class, dry_run=dry_run)
        log.warning("All positions closed. Bot will not trade again today.")
        return

    # --- Per-position stop-loss / take-profit check ---
    open_trades = get_open_trades()
    if not open_trades:
        log.info("No open trades to check.")
        return

    for trade in open_trades:
        symbol      = trade["symbol"]
        asset_class = trade.get("asset_class", "stock")

        try:
            entry_price = float(trade["entry_price"])
        except (ValueError, TypeError):
            log.warning(f"Invalid entry price for {symbol}, skipping.")
            continue

        try:
            if asset_class == "crypto":
                current_price = ac.get_crypto_latest_price(symbol)
            else:
                current_price = ac.get_stock_latest_price(symbol)
        except Exception as e:
            log.warning(f"Could not get price for {symbol}: {e}")
            continue

        if current_price <= 0:
            log.warning(f"Zero price returned for {symbol}, skipping.")
            continue

        should_exit, reason = rm.should_exit_position(symbol, entry_price, current_price)
        if should_exit:
            log.info(f"EXIT triggered for {symbol}: {reason}")
            oe.exit_position(symbol, reason=reason, asset_class=asset_class, dry_run=dry_run)
        else:
            pnl = (current_price - entry_price) / entry_price
            log.info(f"  {symbol:<12} entry={entry_price:.4f} now={current_price:.4f} "
                     f"P&L={pnl:+.2%} — HOLD")

    log.info("Risk check complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HawksTrade Risk Check")
    parser.add_argument("--dry-run", action="store_true",
                        help="Log intended exits without submitting orders")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
