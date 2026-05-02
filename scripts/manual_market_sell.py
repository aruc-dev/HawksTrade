#!/usr/bin/env python3
"""
Interactively sell selected open positions at market.

This script is intentionally standalone: it lists current Alpaca positions,
prompts for one position number at a time, and routes the selected exit through
core.order_executor.exit_position so trades.csv, realized P/L, and order
intents are updated through the same path as automated exits.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence


BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core import alpaca_client as ac  # noqa: E402
from core import order_executor as oe  # noqa: E402
from core.logging_config import runtime_log_handlers  # noqa: E402
from scheduler.reconcile_trade_log import safe_reconcile  # noqa: E402


LOG_DIR = BASE_DIR / "logs"
MANUAL_SELL_REASON = "manually triggered market sell"
EXIT_CHOICE = "x"

log = logging.getLogger("manual_market_sell")


@dataclass(frozen=True)
class PositionChoice:
    symbol: str
    qty: float
    asset_class: str
    avg_entry_price: float | None = None
    current_price: float | None = None
    market_value: float | None = None
    unrealized_pl: float | None = None
    unrealized_plpc: float | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _float_attr(position, name: str) -> float | None:
    raw = getattr(position, name, None)
    if raw in (None, ""):
        return None
    try:
        return float(str(raw))
    except (TypeError, ValueError):
        return None


def _position_asset_class(position) -> str:
    raw = str(getattr(position, "asset_class", "") or "").lower()
    if "crypto" in raw:
        return "crypto"
    return "stock"


def _position_choice(position) -> PositionChoice | None:
    symbol = str(getattr(position, "symbol", "") or "").strip()
    qty = _float_attr(position, "qty")
    if not symbol or qty is None or qty <= 0:
        return None
    return PositionChoice(
        symbol=symbol,
        qty=qty,
        asset_class=_position_asset_class(position),
        avg_entry_price=_float_attr(position, "avg_entry_price"),
        current_price=_float_attr(position, "current_price"),
        market_value=_float_attr(position, "market_value"),
        unrealized_pl=_float_attr(position, "unrealized_pl"),
        unrealized_plpc=_float_attr(position, "unrealized_plpc"),
    )


def _fetch_positions() -> list[PositionChoice]:
    choices = []
    for position in ac.get_all_positions() or []:
        choice = _position_choice(position)
        if choice is not None:
            choices.append(choice)
    return choices


def _format_qty(value: float) -> str:
    return f"{value:.8f}".rstrip("0").rstrip(".")


def _format_money(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:,.4f}" if abs(value) < 1 else f"${value:,.2f}"


def _format_signed_money(value: float | None) -> str:
    if value is None:
        return "n/a"
    prefix = "+" if value >= 0 else "-"
    return f"{prefix}${abs(value):,.2f}"


def _format_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.2%}"


def _position_line(index: int, position: PositionChoice) -> str:
    return (
        f"{index}) {position.symbol} "
        f"qty={_format_qty(position.qty)} "
        f"entry={_format_money(position.avg_entry_price)} "
        f"now={_format_money(position.current_price)} "
        f"value={_format_money(position.market_value)} "
        f"P/L={_format_signed_money(position.unrealized_pl)} ({_format_pct(position.unrealized_plpc)})"
    )


def _print_menu(positions: Sequence[PositionChoice], output_fn: Callable[[str], None]) -> None:
    if positions:
        output_fn("\nOpen positions:")
        for index, position in enumerate(positions, start=1):
            output_fn(_position_line(index, position))
    else:
        output_fn("\nNo open positions.")
    output_fn("X) Exit")


def _parse_selection(raw: str, positions: Sequence[PositionChoice]) -> int | None:
    try:
        index = int(raw)
    except ValueError:
        return None
    if index < 1 or index > len(positions):
        return None
    return index - 1


def _result_message(result: dict | None) -> str:
    if not result:
        return "No sell order was placed; no open long position was found."

    symbol = result.get("symbol", "selected position")
    status = result.get("status", "unknown")
    order_id = result.get("order_id") or "n/a"
    qty = result.get("qty", "n/a")
    pnl_pct = result.get("pnl_pct", "")
    pnl_suffix = ""
    try:
        if pnl_pct != "":
            pnl_suffix = f" | P/L={float(pnl_pct):+.2%}"
    except (TypeError, ValueError):
        pnl_suffix = ""

    if status == "dry_run":
        return f"DRY RUN: would submit market sell for {symbol} | qty={qty}{pnl_suffix}"
    if status == "closed":
        return f"Market sell filled for {symbol} | qty={qty} | order_id={order_id}{pnl_suffix}"
    if status in {"submitted", "partially_filled"}:
        return (
            f"Market sell submitted for {symbol} | qty={qty} | status={status} | "
            f"order_id={order_id}. Trade log will reconcile after broker fill confirmation."
        )
    if status == "pending_exit":
        return f"Skipped {symbol}; a pending sell order already exists."
    if status == "pending_exit_check_failed":
        return f"Skipped {symbol}; could not verify pending sell orders: {result.get('error', '')}"
    if status.startswith("invalid_") or status == "exit_failed":
        return f"Sell failed for {symbol} | status={status} | error={result.get('error', '')}"
    return f"Sell result for {symbol} | status={status} | order_id={order_id}{pnl_suffix}"


def run_interactive(
    *,
    dry_run: bool = False,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> int:
    log.info("Manual market sell session started | dry_run=%s", int(dry_run))
    while True:
        try:
            positions = _fetch_positions()
        except Exception as exc:
            info = ac.classify_alpaca_error(exc)
            log.error(
                "Failed to fetch open positions: %s | category=%s retryable=%s status_code=%s",
                exc,
                info.category,
                info.retryable,
                info.status_code or "",
                exc_info=True,
            )
            output_fn(f"Failed to fetch open positions: {exc}")
            return 1

        _print_menu(positions, output_fn)
        raw = input_fn("Enter position number: ").strip()
        if raw.lower() == EXIT_CHOICE:
            log.info("Manual market sell session exited by user")
            output_fn("Exiting.")
            return 0

        selected_index = _parse_selection(raw, positions)
        if selected_index is None:
            output_fn("Invalid selection. Enter a position number or X to exit.")
            continue

        selected = positions[selected_index]
        log.info(
            "Manual market sell selected | symbol=%s qty=%s asset_class=%s dry_run=%s",
            selected.symbol,
            selected.qty,
            selected.asset_class,
            int(dry_run),
        )
        result = oe.exit_position(
            selected.symbol,
            MANUAL_SELL_REASON,
            asset_class=selected.asset_class,
            dry_run=dry_run,
            force_market=True,
        )
        message = _result_message(result)
        output_fn(message)
        log.info(message)

        if not dry_run:
            safe_reconcile(context="manual_market_sell.post_exit", logger=log)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=runtime_log_handlers(LOG_DIR, f"manual_market_sell_{_utc_now().strftime('%Y%m%d')}.log"),
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "List open Alpaca positions and sell selected positions at market with "
            f"exit reason '{MANUAL_SELL_REASON}'."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the flow and executor result without submitting a sell order.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging()
    return run_interactive(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
