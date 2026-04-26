"""
HawksTrade - Portfolio Tracker
================================
Reads live positions from Alpaca and merges with local trade log
to produce a full portfolio snapshot including unrealised P&L.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict

import yaml
import pandas as pd

from core import alpaca_client as ac
from core.config_loader import get_config_path
from tracking.trade_log import locked_trade_log

BASE_DIR = Path(__file__).resolve().parent.parent
with open(get_config_path()) as f:
    CFG = yaml.safe_load(f)

TRADE_LOG = BASE_DIR / CFG["reporting"]["trade_log_file"]
log = logging.getLogger("portfolio")


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_snapshot() -> Dict:
    """
    Returns a full portfolio snapshot dict:
      - account summary
      - list of open positions with live P&L
    """
    try:
        account    = ac.get_account()
        positions  = ac.get_all_positions()

        pos_list = []
        for p in positions:
            pos_list.append({
                "symbol":          p.symbol,
                "qty":             float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "current_price":   float(p.current_price),
                "market_value":    float(p.market_value),
                "unrealized_pnl":  float(p.unrealized_pl),
                "unrealized_pnl_pct": float(p.unrealized_plpc),
            })

        snapshot = {
            "timestamp":       _utc_now().isoformat(),
            "mode":            CFG["mode"],
            "portfolio_value": float(account.portfolio_value),
            "cash":            float(account.cash),
            "buying_power":    float(account.buying_power),
            "positions":       pos_list,
            "position_count":  len(pos_list),
        }
        return snapshot

    except Exception as e:
        log.error(f"Failed to get portfolio snapshot: {e}", exc_info=True)
        return {}


def get_open_symbols() -> List[str]:
    """Returns list of currently held symbols."""
    positions = ac.get_all_positions()
    return [p.symbol for p in positions]


def print_snapshot():
    """Pretty-print the current portfolio to stdout / logs."""
    snap = get_snapshot()
    if not snap:
        log.warning("Could not retrieve portfolio snapshot.")
        return

    log.info("=" * 55)
    log.info(f"PORTFOLIO SNAPSHOT  [{snap['timestamp']}]  mode={snap['mode'].upper()}")
    log.info(f"  Portfolio Value : ${snap['portfolio_value']:>12,.2f}")
    log.info(f"  Cash            : ${snap['cash']:>12,.2f}")
    log.info(f"  Buying Power    : ${snap['buying_power']:>12,.2f}")
    log.info(f"  Open Positions  : {snap['position_count']}")
    log.info("-" * 55)
    for p in snap["positions"]:
        log.info(
            f"  {p['symbol']:<10} qty={p['qty']:>8.4f}  "
            f"entry=${p['avg_entry_price']:>10.4f}  "
            f"now=${p['current_price']:>10.4f}  "
            f"P&L={p['unrealized_pnl_pct']:>+.2%}"
        )
    log.info("=" * 55)


def load_trade_history() -> pd.DataFrame:
    """Load the full local trade history CSV."""
    with locked_trade_log(TRADE_LOG, exclusive=False) as trade_log_path:
        if not trade_log_path.exists():
            return pd.DataFrame()
        return pd.read_csv(trade_log_path)
