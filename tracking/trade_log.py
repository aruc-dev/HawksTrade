"""
HawksTrade - Trade Logger
==========================
Appends every trade (entry or exit) to data/trades.csv.
Thread-safe via file locking.
"""

from __future__ import annotations

import csv
import contextlib
import logging
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None
    import msvcrt

import yaml

BASE_DIR  = Path(__file__).resolve().parent.parent
with open(BASE_DIR / "config" / "config.yaml") as f:
    CFG = yaml.safe_load(f)

TRADE_LOG = BASE_DIR / CFG["reporting"]["trade_log_file"]
log = logging.getLogger("trade_log")
QTY_EPSILON = Decimal("0.00000001")
ACTIVE_ENTRY_STATUSES = {"open", "partially_filled"}
PENDING_EXIT_STATUSES = {"submitted", "partially_filled"}


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


def _order_value(order, name: str, default=None):
    if isinstance(order, dict):
        return order.get(name, default)
    return getattr(order, name, default)


def _order_status(order) -> str:
    status = _order_value(order, "status", "") or ""
    return str(getattr(status, "value", status)).strip().lower()


def _order_side(order) -> str:
    side = _order_value(order, "side", "") or ""
    return str(getattr(side, "value", side)).strip().lower()


def _order_id(order) -> str:
    return str(_order_value(order, "id", _order_value(order, "order_id", "")) or "")


def _order_filled_qty(order) -> Decimal:
    return _to_decimal(_order_value(order, "filled_qty", None), Decimal("0")) or Decimal("0")


def _order_filled_avg_price(order) -> Decimal | None:
    return _to_decimal(_order_value(order, "filled_avg_price", None))


def _order_filled_at_iso(order) -> str | None:
    filled_at = _order_value(order, "filled_at", None)
    if filled_at in (None, ""):
        return None
    if isinstance(filled_at, datetime):
        return _as_utc(filled_at).isoformat()
    return str(filled_at)


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


def _apply_close_to_matching_buy_rows(
    rows: list[dict],
    symbol: str,
    *,
    exit_price: float,
    pnl_pct: float,
    reason: str,
    closed_qty: Decimal | None = None,
) -> tuple[bool, Decimal | None, bool]:
    remaining = closed_qty
    updated = False
    partial_close_logged = False

    for row in reversed(rows):
        if not (
            _symbols_match(row.get("symbol", ""), symbol)
            and row.get("status") in ACTIVE_ENTRY_STATUSES
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
            partial_close_logged = True
            remaining = Decimal("0")
            break

    return updated, remaining, partial_close_logged

COLUMNS = [
    "timestamp", "mode", "symbol", "strategy", "asset_class",
    "side", "qty", "entry_price", "exit_price", "stop_loss",
    "take_profit", "pnl_pct", "exit_reason", "order_id", "status",
]


def _lock_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.lock")


def _lock_file(lock_file, exclusive: bool):
    if fcntl is not None:
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(lock_file.fileno(), operation)
    else:  # pragma: no cover - Windows fallback
        lock_file.seek(0, 2)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        operation = msvcrt.LK_LOCK if exclusive else msvcrt.LK_RLCK
        msvcrt.locking(lock_file.fileno(), operation, 1)


def _unlock_file(lock_file):
    if fcntl is not None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    else:  # pragma: no cover - Windows fallback
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


@contextlib.contextmanager
def locked_trade_log(path: Path | None = None, *, exclusive: bool = True) -> Iterator[Path]:
    """
    Hold an advisory cross-process lock for a CSV file path.

    Every runtime reader/writer of the target CSV should use this context so
    append and rewrite operations cannot interleave with report/health reads.
    """
    trade_log_path = Path(path or TRADE_LOG)
    trade_log_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path(trade_log_path)
    with open(lock_path, "a+b") as lock_file:
        _lock_file(lock_file, exclusive=exclusive)
        try:
            yield trade_log_path
        finally:
            _unlock_file(lock_file)


def _ensure_file(path: Path | None = None):
    trade_log_path = Path(path or TRADE_LOG)
    trade_log_path.parent.mkdir(parents=True, exist_ok=True)
    if not trade_log_path.exists():
        with open(trade_log_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()
        log.info(f"Trade log created: {trade_log_path}")


def _read_rows_unlocked(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, "r", newline="") as f:
        return list(csv.DictReader(f))


def read_trade_rows(path: Path | None = None) -> list[dict]:
    """Return all trade-log rows under a shared file lock."""
    with locked_trade_log(path, exclusive=False) as trade_log_path:
        return _read_rows_unlocked(trade_log_path)


def log_trade(trade: Dict):
    """Append a single trade row to trades.csv."""
    row = {col: trade.get(col, "") for col in COLUMNS}
    with locked_trade_log(exclusive=True) as trade_log_path:
        _ensure_file(trade_log_path)
        with open(trade_log_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writerow(row)
    log.info(f"Trade logged: {row['side'].upper()} {row['symbol']} | {row['strategy']}")


def get_open_trades() -> list:
    """Return buy rows that represent broker-confirmed open exposure."""
    return [
        row for row in read_trade_rows()
        if row.get("side") == "buy" and row.get("status") in ACTIVE_ENTRY_STATUSES
    ]


def get_closed_trades() -> list:
    """Return all closed trades from the trade log CSV."""
    return [row for row in read_trade_rows() if row.get("status") == "closed"]


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
    with locked_trade_log(exclusive=True) as trade_log_path:
        rows = _read_rows_unlocked(trade_log_path)

        remaining = _to_decimal(closed_qty) if closed_qty is not None else None
        if remaining is not None and remaining <= QTY_EPSILON:
            log.warning(f"Trade close ignored for {symbol}: closed_qty={closed_qty}")
            return False

        updated, remaining, partial_close_logged = _apply_close_to_matching_buy_rows(
            rows,
            symbol,
            exit_price=exit_price,
            pnl_pct=pnl_pct,
            reason=reason,
            closed_qty=remaining,
        )
        if partial_close_logged:
            closed_amount = _to_decimal(closed_qty, Decimal("0")) or Decimal("0")
            log.info(
                f"Trade partially closed: {symbol} | closed_qty={_fmt_decimal(closed_amount)} "
                f"| reason={reason}"
            )

        if updated:
            with open(trade_log_path, "w", newline="") as f:
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
    elif not partial_close_logged:
        # Only emit "Trade closed" for full exits; partial exits already logged above.
        log.info(f"Trade closed: {symbol} | pnl={pnl_pct:.2%} | reason={reason}")
    return True


def reconcile_open_trades_with_positions(
    positions: Iterable,
    closed_orders: Iterable | None = None,
) -> dict:
    """
    Align data/trades.csv open buy rows with broker-reported open positions.

    This repairs drift caused by submitted-but-unfilled exit orders or
    later-filled submitted exits. It does not place orders; it only rewrites
    trades.csv.
    """
    with locked_trade_log(exclusive=True) as trade_log_path:
        rows = _read_rows_unlocked(trade_log_path)

        summary = {
            "positions": 0,
            "updated_open_rows": 0,
            "reopened_rows": 0,
            "created_rows": 0,
            "marked_unfilled_sells": 0,
            "closed_filled_sells": 0,
            "closed_stale_rows": 0,
        }

        filled_sell_orders = {}
        for order in closed_orders or []:
            if _order_side(order) != "sell" or _order_status(order) != "filled":
                continue
            order_id = _order_id(order)
            if not order_id:
                continue
            filled_qty = _order_filled_qty(order)
            if filled_qty <= QTY_EPSILON:
                continue
            filled_sell_orders[order_id] = order

        for row in rows:
            if row.get("side") != "sell" or row.get("status") not in PENDING_EXIT_STATUSES:
                continue
            order = filled_sell_orders.pop(str(row.get("order_id", "") or ""), None)
            if order is None:
                continue

            fill_price = _order_filled_avg_price(order)
            entry_price = _to_decimal(row.get("entry_price"), Decimal("0")) or Decimal("0")
            if fill_price is None:
                fill_price = _to_decimal(row.get("exit_price"), entry_price) or entry_price
            filled_qty = _order_filled_qty(order)
            pnl_pct = 0.0
            if entry_price > 0:
                pnl_pct = float((fill_price - entry_price) / entry_price)

            filled_at = _order_filled_at_iso(order)
            if filled_at:
                row["timestamp"] = filled_at
            row["status"] = "closed"
            row["qty"] = _fmt_decimal(filled_qty)
            row["exit_price"] = _fmt_decimal(fill_price)
            row["pnl_pct"] = round(pnl_pct, 6)

            reason = row.get("exit_reason", "") or "broker reconciliation: exit fill confirmed"
            row["exit_reason"] = reason
            _apply_close_to_matching_buy_rows(
                rows,
                row.get("symbol", ""),
                exit_price=float(fill_price),
                pnl_pct=pnl_pct,
                reason=reason,
                closed_qty=filled_qty,
            )
            summary["closed_filled_sells"] += 1

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
                if row.get("status") in ACTIVE_ENTRY_STATUSES and row.get("side") == "buy"
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
                    duplicate_exit_price = (
                        broker_entry
                        if broker_entry is not None
                        else _to_decimal(row.get("entry_price"), Decimal("0")) or Decimal("0")
                    )
                    _close_row(
                        row,
                        float(duplicate_exit_price),
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
            if row.get("status") not in ACTIVE_ENTRY_STATUSES or row.get("side") != "buy":
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

        with open(trade_log_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    log.info(f"Trade log reconciliation complete: {summary}")
    return summary


def get_trade_age_days(symbol: str) -> float:
    """Return how many calendar days ago the most recent open trade was entered."""
    entries = [
        row for row in get_open_trades()
        if _symbols_match(row.get("symbol", ""), symbol) and row.get("side") == "buy"
    ]
    if not entries:
        return 0.0
    latest = sorted(entries, key=lambda x: x["timestamp"])[-1]
    entry_dt = _as_utc(datetime.fromisoformat(latest["timestamp"]))
    delta    = _utc_now() - entry_dt
    return delta.total_seconds() / 86400
