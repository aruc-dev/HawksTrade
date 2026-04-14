"""
HawksTrade - Trade Logger
==========================
Appends every trade (entry or exit) to data/trades.csv.
Thread-safe via file locking.
"""

import csv
import logging
import os
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable

import yaml

BASE_DIR  = Path(__file__).resolve().parent.parent
with open(BASE_DIR / "config" / "config.yaml") as f:
    CFG = yaml.safe_load(f)

TRADE_LOG = BASE_DIR / CFG["reporting"]["trade_log_file"]
log = logging.getLogger("trade_log")
QTY_EPSILON = Decimal("0.00000001")


def _utc_now():
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_decimal(value, default: Decimal | None = None) -> Decimal | None:
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return default


def _fmt_decimal(value: Decimal) -> str:
    if value.copy_abs() <= QTY_EPSILON:
        return "0"
    return format(value.normalize(), "f")


def _symbols_match(left: str, right: str) -> bool:
    return str(left or "").replace("/", "").upper() == str(right or "").replace("/", "").upper()


def _position_symbol(position) -> str:
    return str(getattr(position, "symbol", "") or "")


def _position_qty(position) -> Decimal:
    return _to_decimal(getattr(position, "qty", None), Decimal("0")) or Decimal("0")


def _position_entry_price(position) -> Decimal | None:
    return _to_decimal(getattr(position, "avg_entry_price", None))


def _position_asset_class(position) -> str:
    raw = str(getattr(position, "asset_class", "") or "").lower()
    return "crypto" if "crypto" in raw else "stock"


def _close_row(row: dict, exit_price: float, pnl_pct: float, reason: str):
    row["status"] = "closed"
    row["exit_price"] = round(exit_price, 6)
    row["pnl_pct"] = round(pnl_pct, 6)
    row["exit_reason"] = reason


def _clear_exit_fields(row: dict):
    row["status"] = "open"
    row["exit_price"] = ""
    row["pnl_pct"] = ""
    row["exit_reason"] = ""

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


def get_closed_trades() -> list:
    """Return all closed trades from the trade log CSV."""
    _ensure_file()
    trades = []
    with open(TRADE_LOG, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("status") == "closed":
                trades.append(row)
    return trades


def mark_trade_closed(
    symbol: str,
    exit_price: float,
    pnl_pct: float,
    reason: str,
    closed_qty: float | str | Decimal | None = None,
):
    """
    Update open buy rows for a symbol after an exit fill.

    When closed_qty is provided, only that quantity is removed from the
    open trade log. A partial exit leaves the residual buy row open so
    data/trades.csv stays aligned with the broker position.
    Rewrites the CSV (safe for small files).
    """
    _ensure_file()

    with open(TRADE_LOG, "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    remaining = _to_decimal(closed_qty) if closed_qty is not None else None
    if remaining is not None and remaining <= QTY_EPSILON:
        log.warning(f"Trade close ignored for {symbol}: closed_qty={closed_qty}")
        return False

    updated = False
    for row in reversed(rows):
        if not (
            _symbols_match(row.get("symbol", ""), symbol)
            and row.get("status") == "open"
            and row.get("side") == "buy"
        ):
            continue

        if remaining is None:
            _close_row(row, exit_price, pnl_pct, reason)
            updated = True
            break

        open_qty = _to_decimal(row.get("qty"), Decimal("0")) or Decimal("0")
        if open_qty <= QTY_EPSILON:
            _close_row(row, exit_price, pnl_pct, reason)
            updated = True
            continue

        if remaining + QTY_EPSILON >= open_qty:
            _close_row(row, exit_price, pnl_pct, reason)
            remaining -= open_qty
            updated = True
            if remaining <= QTY_EPSILON:
                break
        else:
            residual_qty = open_qty - remaining
            row["qty"] = _fmt_decimal(residual_qty)
            _clear_exit_fields(row)
            updated = True
            log.info(
                f"Trade partially closed: {symbol} | closed_qty={_fmt_decimal(remaining)} "
                f"| remaining_qty={row['qty']} | reason={reason}"
            )
            remaining = Decimal("0")
            break

    if updated:
        with open(TRADE_LOG, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    if not updated:
        log.warning(f"No open trade found to close for {symbol}")
        return False

    if remaining is not None and remaining > QTY_EPSILON:
        log.warning(
            f"Closed quantity exceeded open trade log quantity for {symbol}; "
            f"unmatched_qty={_fmt_decimal(remaining)}"
        )
    elif closed_qty is None or (remaining is not None and remaining <= QTY_EPSILON):
        log.info(f"Trade closed: {symbol} | pnl={pnl_pct:.2%} | reason={reason}")
    return True


def reconcile_open_trades_with_positions(positions: Iterable) -> dict:
    """
    Align data/trades.csv open buy rows with broker-reported open positions.

    This repairs drift caused by submitted-but-unfilled exit orders or
    fractional fills. It does not place orders; it only rewrites trades.csv.
    """
    _ensure_file()
    with open(TRADE_LOG, "r") as f:
        rows = list(csv.DictReader(f))

    summary = {
        "positions": 0,
        "updated_open_rows": 0,
        "reopened_rows": 0,
        "created_rows": 0,
        "marked_unfilled_sells": 0,
        "closed_stale_rows": 0,
    }

    broker_positions = [
        p for p in positions
        if _position_symbol(p) and _position_qty(p).copy_abs() > QTY_EPSILON
    ]
    summary["positions"] = len(broker_positions)
    broker_symbols = {_position_symbol(p).replace("/", "").upper() for p in broker_positions}

    for pos in broker_positions:
        broker_symbol = _position_symbol(pos)
        broker_qty = _position_qty(pos).copy_abs()
        broker_entry = _position_entry_price(pos)
        asset_class = _position_asset_class(pos)

        matching_rows = [
            (idx, row) for idx, row in enumerate(rows)
            if _symbols_match(row.get("symbol", ""), broker_symbol)
        ]
        open_buy_rows = [
            (idx, row) for idx, row in matching_rows
            if row.get("status") == "open" and row.get("side") == "buy"
        ]
        buy_rows = [
            (idx, row) for idx, row in matching_rows
            if row.get("side") == "buy"
        ]

        if open_buy_rows:
            keep_idx, keep_row = open_buy_rows[-1]
            keep_row["qty"] = _fmt_decimal(broker_qty)
            if broker_entry is not None:
                keep_row["entry_price"] = _fmt_decimal(broker_entry)
            keep_row["asset_class"] = asset_class or keep_row.get("asset_class", "")
            _clear_exit_fields(keep_row)
            summary["updated_open_rows"] += 1

            for idx, row in open_buy_rows[:-1]:
                _close_row(
                    row,
                    float(broker_entry or _to_decimal(row.get("entry_price"), Decimal("0")) or 0),
                    0.0,
                    "broker reconciliation: consolidated duplicate open row",
                )
                summary["closed_stale_rows"] += 1
            continue

        if buy_rows:
            buy_idx, buy_row = buy_rows[-1]
            original_qty = _to_decimal(buy_row.get("qty"), broker_qty) or broker_qty
            buy_row["qty"] = _fmt_decimal(broker_qty)
            if broker_entry is not None:
                buy_row["entry_price"] = _fmt_decimal(broker_entry)
            buy_row["asset_class"] = asset_class or buy_row.get("asset_class", "")
            _clear_exit_fields(buy_row)
            summary["reopened_rows"] += 1

            implied_sold_qty = original_qty - broker_qty
            if implied_sold_qty < Decimal("0"):
                implied_sold_qty = Decimal("0")
            remaining_sold_qty = implied_sold_qty
            for _, sell_row in [
                (idx, row) for idx, row in matching_rows
                if idx > buy_idx and row.get("side") == "sell" and row.get("status") == "closed"
            ]:
                sell_qty = _to_decimal(sell_row.get("qty"), Decimal("0")) or Decimal("0")
                if remaining_sold_qty + QTY_EPSILON >= sell_qty:
                    remaining_sold_qty -= sell_qty
                    continue
                sell_row["status"] = "submitted"
                sell_row["pnl_pct"] = ""
                sell_row["exit_reason"] = "broker reconciliation: exit order not fully filled"
                summary["marked_unfilled_sells"] += 1
            continue

        now = _utc_now().isoformat()
        display_symbol = broker_symbol
        if asset_class == "crypto" and "/" not in display_symbol and display_symbol.endswith("USD"):
            display_symbol = f"{display_symbol[:-3]}/USD"
        rows.append({
            "timestamp": now,
            "mode": CFG.get("mode", ""),
            "symbol": display_symbol,
            "strategy": "broker_reconciliation",
            "asset_class": asset_class,
            "side": "buy",
            "qty": _fmt_decimal(broker_qty),
            "entry_price": _fmt_decimal(broker_entry or Decimal("0")),
            "exit_price": "",
            "stop_loss": "",
            "take_profit": "",
            "pnl_pct": "",
            "exit_reason": "",
            "order_id": "BROKER-RECONCILE",
            "status": "open",
        })
        summary["created_rows"] += 1

    for row in rows:
        if row.get("status") != "open" or row.get("side") != "buy":
            continue
        normalized = str(row.get("symbol", "")).replace("/", "").upper()
        if normalized and normalized not in broker_symbols:
            _close_row(
                row,
                float(_to_decimal(row.get("entry_price"), Decimal("0")) or 0),
                0.0,
                "broker reconciliation: no broker position",
            )
            summary["closed_stale_rows"] += 1

    with open(TRADE_LOG, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    log.info(f"Trade log reconciliation complete: {summary}")
    return summary


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
    entry_dt = _as_utc(datetime.fromisoformat(latest["timestamp"]))
    delta    = _utc_now() - entry_dt
    return delta.total_seconds() / 86400
