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
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import alpaca_client as ac
from tracking.trade_log import reconcile_open_trades_with_positions


log = logging.getLogger("reconcile_trade_log")


def run(
    positions: Iterable | None = None,
    closed_orders: Iterable | None = None,
) -> dict:
    fetched_positions = positions is None
    if positions is None:
        positions = ac.get_all_positions()
    if closed_orders is None and fetched_positions:
        closed_orders = ac.get_closed_orders()
    elif closed_orders is None:
        closed_orders = []
    positions = list(positions)
    closed_orders = list(closed_orders)
    summary = reconcile_open_trades_with_positions(positions, closed_orders=closed_orders)
    log.info(
        "Reconciled trades.csv with %s broker position(s) and %s closed broker order(s): %s",
        len(positions),
        len(closed_orders),
        summary,
    )
    return summary


def safe_reconcile(
    *,
    positions: Iterable | None = None,
    closed_orders: Iterable | None = None,
    context: str = "manual",
    logger: logging.Logger | None = None,
    raise_on_error: bool = False,
) -> dict | None:
    target_log = logger or log
    try:
        summary = run(positions=positions, closed_orders=closed_orders)
    except Exception as exc:
        info = ac.classify_alpaca_error(exc)
        target_log.warning(
            "Trade-log reconciliation failed context=%s: %s "
            "| category=%s retryable=%s status_code=%s",
            context,
            exc,
            info.category,
            info.retryable,
            info.status_code or "",
            exc_info=True,
        )
        if raise_on_error:
            raise
        return None

    target_log.info("Trade-log reconciliation complete context=%s summary=%s", context, summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reconcile trades.csv with Alpaca open positions")
    parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    result = run()
    print(result)
