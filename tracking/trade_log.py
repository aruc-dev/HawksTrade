"""
HawksTrade - Trade Logger
==========================
Appends every trade (entry or exit) to data/trades.csv.
Thread-safe via file locking.
"""

import csv
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

import yaml

BASE_DIR  = Path(__file__).resolve().parent.parent
with open(BASE_DIR / "config" / "config.yaml") as f:
    CFG = yaml.safe_load(f)

TRADE_LOG = BASE_DIR / CFG["reporting"]["trade_log_file"]
log = logging.getLogger("trade_log")


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

COLUMNS = [
    "timestamp", "mode", "symbol", "strategy", "asset_class",
    "side", "qty", "entry_price", "exit_price", "stop_loss",
    "take_profit", "pnl_pct", "exit_reason", "order_id", "status",
]


def _ensure_file():
    TRADE_LOG.parent.mkdir(parents=True, exist_ok=True)
    if not TRADE_LOG.exists():
        with open(TRADE_LOG, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()
        log.info(f"Trade log created: {TRADE_LOG}")


def log_trade(trade: Dict):
    """Append a single trade row to trades.csv."""
    _ensure_file()
    row = {col: trade.get(col, "") for col in COLUMNS}
    with open(TRADE_LOG, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writerow(row)
    log.info(f"Trade logged: {row['side'].upper()} {row['symbol']} | {row['strategy']}")


def get_open_trades() -> list:
    """Return all trades with status='open'."""
    _ensure_file()
    open_trades = []
    with open(TRADE_LOG, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("status") == "open":
                open_trades.append(row)
    return open_trades


def mark_trade_closed(symbol: str, exit_price: float, pnl_pct: float, reason: str):
    """
    Update the most recent open trade for a symbol to closed.
    Rewrites the CSV (safe for small files).
    """
    _ensure_file()
    rows = []

    with open(TRADE_LOG, "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    updated = False
    for row in reversed(rows):
        if row["symbol"] == symbol and row["status"] == "open" and row["side"] == "buy":
            row["status"]      = "closed"
            row["exit_price"]  = round(exit_price, 6)
            row["pnl_pct"]     = round(pnl_pct, 6)
            row["exit_reason"] = reason
            updated = True
            break

    with open(TRADE_LOG, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    if updated:
        log.info(f"Trade closed: {symbol} | pnl={pnl_pct:.2%} | reason={reason}")


def get_trade_age_days(symbol: str) -> float:
    """Return how many calendar days ago the most recent open trade was entered."""
    _ensure_file()
    entries = [
        row for row in get_open_trades()
        if row["symbol"] == symbol and row["side"] == "buy"
    ]
    if not entries:
        return 0.0
    latest = sorted(entries, key=lambda x: x["timestamp"])[-1]
    entry_dt = datetime.fromisoformat(latest["timestamp"])
    delta    = _utc_now() - entry_dt
    return delta.total_seconds() / 86400
