#!/usr/bin/env python3
"""
Reconcile data/trades.csv with current Alpaca paper/live positions.

This is a read-only broker operation: it fetches open positions and rewrites
the local CSV so status dashboards that depend on trades.csv see the same
open quantities as Alpaca.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import alpaca_client as ac
from tracking.trade_log import reconcile_open_trades_with_positions


log = logging.getLogger("reconcile_trade_log")


def run() -> dict:
    positions = ac.get_all_positions()
    summary = reconcile_open_trades_with_positions(positions)
    log.info("Reconciled trades.csv with %s broker position(s): %s", len(positions), summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reconcile trades.csv with Alpaca open positions")
    parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    result = run()
    print(result)
