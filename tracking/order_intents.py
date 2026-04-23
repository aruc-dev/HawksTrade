"""
Persistent broker order intents.

Each real broker submission gets a deterministic client_order_id before the
submit call. Persisting the intent first gives retry paths a stable id to reuse
instead of creating duplicate broker orders for the same run/symbol/side/strategy.
"""

from __future__ import annotations

import csv
import hashlib
import re
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path

from tracking.trade_log import locked_trade_log


BASE_DIR = Path(__file__).resolve().parent.parent
ORDER_INTENTS = BASE_DIR / "data" / "order_intents.csv"
TERMINAL_STATUSES = {"canceled", "cancelled", "expired", "rejected", "filled"}

COLUMNS = [
    "timestamp",
    "updated_at",
    "run_id",
    "client_order_id",
    "symbol",
    "normalized_symbol",
    "side",
    "strategy",
    "asset_class",
    "qty",
    "limit_price",
    "status",
    "broker_order_id",
    "error",
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_symbol(symbol: str) -> str:
    return str(symbol or "").replace("/", "").upper()


def _slug(value: str, max_len: int) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "", str(value or "").lower())
    return (text or "x")[:max_len]


def make_client_order_id(run_id: str, symbol: str, side: str, strategy: str, intent_timestamp: str) -> str:
    normalized_symbol = _normalize_symbol(symbol)
    payload = "|".join([run_id, normalized_symbol, side.lower(), strategy, intent_timestamp])
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"ht-{side.lower()[:1]}-{_slug(normalized_symbol, 8)}-{_slug(strategy, 10)}-{digest}"


def _read_rows_unlocked(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, "r", newline="") as f:
        return list(csv.DictReader(f))


def _write_rows_unlocked(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows({col: row.get(col, "") for col in COLUMNS} for row in rows)


def _ensure_file_unlocked(path: Path) -> None:
    if path.exists():
        return
    _write_rows_unlocked(path, [])


def _locked_intents(exclusive: bool = True) -> AbstractContextManager[Path]:
    return locked_trade_log(ORDER_INTENTS, exclusive=exclusive)


def read_order_intents() -> list[dict]:
    with _locked_intents(exclusive=False) as path:
        return _read_rows_unlocked(path)


def get_or_create_order_intent(
    *,
    run_id: str,
    symbol: str,
    side: str,
    strategy: str,
    asset_class: str,
    qty,
    limit_price=None,
) -> tuple[dict, bool]:
    side = side.lower()
    normalized_symbol = _normalize_symbol(symbol)
    strategy = strategy or "unknown"

    with _locked_intents(exclusive=True) as path:
        _ensure_file_unlocked(path)
        rows = _read_rows_unlocked(path)
        for row in reversed(rows):
            if (
                row.get("run_id") == run_id
                and row.get("normalized_symbol") == normalized_symbol
                and row.get("side") == side
                and row.get("strategy") == strategy
                and row.get("status") not in TERMINAL_STATUSES
            ):
                return row, False

        timestamp = _utc_now().isoformat()
        client_order_id = make_client_order_id(run_id, symbol, side, strategy, timestamp)
        row = {
            "timestamp": timestamp,
            "updated_at": timestamp,
            "run_id": run_id,
            "client_order_id": client_order_id,
            "symbol": symbol,
            "normalized_symbol": normalized_symbol,
            "side": side,
            "strategy": strategy,
            "asset_class": asset_class or "",
            "qty": qty,
            "limit_price": "" if limit_price is None else limit_price,
            "status": "intent_created",
            "broker_order_id": "",
            "error": "",
        }
        rows.append(row)
        _write_rows_unlocked(path, rows)
        return row, True


def update_order_intent(client_order_id: str, *, status: str, broker_order_id: str = "", error: str = "") -> bool:
    with _locked_intents(exclusive=True) as path:
        _ensure_file_unlocked(path)
        rows = _read_rows_unlocked(path)
        updated = False
        now = _utc_now().isoformat()
        for row in rows:
            if row.get("client_order_id") != client_order_id:
                continue
            row["updated_at"] = now
            row["status"] = status
            if broker_order_id:
                row["broker_order_id"] = broker_order_id
            row["error"] = error
            updated = True
            break
        if updated:
            _write_rows_unlocked(path, rows)
        return updated


def reconcile_order_intents(
    *,
    open_orders: list | None = None,
    closed_orders: list | None = None,
) -> dict:
    open_orders = list(open_orders or [])
    closed_orders = list(closed_orders or [])
    orders = open_orders + closed_orders
    orders_by_broker_id: dict[str, tuple[str, str]] = {}
    orders_by_client_id: dict[str, tuple[str, str]] = {}

    def _order_value(order, name: str):
        if isinstance(order, dict):
            return order.get(name)
        return getattr(order, name, None)

    def _order_status(order) -> str:
        status = _order_value(order, "status")
        return str(getattr(status, "value", status) or "").lower()

    def _remember(order) -> None:
        status = _order_status(order)
        broker_order_id = str(_order_value(order, "id") or _order_value(order, "order_id") or "")
        client_order_id = str(_order_value(order, "client_order_id") or "")
        if broker_order_id:
            orders_by_broker_id[broker_order_id] = (status, client_order_id)
        if client_order_id:
            orders_by_client_id[client_order_id] = (status, broker_order_id)

    for order in orders:
        _remember(order)

    updated = 0
    with _locked_intents(exclusive=True) as path:
        _ensure_file_unlocked(path)
        rows = _read_rows_unlocked(path)
        now = _utc_now().isoformat()
        for row in rows:
            match = None
            broker_order_id = str(row.get("broker_order_id") or "")
            client_order_id = str(row.get("client_order_id") or "")
            if broker_order_id:
                match = orders_by_broker_id.get(broker_order_id)
            if match is None and client_order_id:
                match = orders_by_client_id.get(client_order_id)
            if match is None:
                continue
            status, matched_broker_order_id = match
            changed = False
            if status and row.get("status") != status:
                row["status"] = status
                changed = True
            if matched_broker_order_id and row.get("broker_order_id") != matched_broker_order_id:
                row["broker_order_id"] = matched_broker_order_id
                changed = True
            if changed:
                row["updated_at"] = now
                updated += 1
        if updated:
            _write_rows_unlocked(path, rows)
    return {
        "updated_rows": updated,
        "open_orders": len(open_orders),
        "closed_orders": len(closed_orders),
    }
