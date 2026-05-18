#!/usr/bin/env python3
"""Prefetch stock minute bars into the local HawksTrade minute cache."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config_loader import get_config
from core.minute_cache import ensure_month_cached
from screener.pit_universe import PITUniverseBuilder

BASE_DIR = Path(__file__).resolve().parent.parent
log = logging.getLogger("prefetch_minute_bars")


def _parse_date(value: str | None, *, default: datetime) -> datetime:
    if not value or value == "auto":
        return default
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _month_starts(start: datetime, end: datetime) -> list[datetime]:
    current = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
    final = datetime(end.year, end.month, 1, tzinfo=timezone.utc)
    months = []
    while current <= final:
        months.append(current)
        if current.month == 12:
            current = datetime(current.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            current = datetime(current.year, current.month + 1, 1, tzinfo=timezone.utc)
    return months


def _symbols_from_arg(raw: str, cfg: dict, start: datetime, end: datetime) -> list[str]:
    raw = str(raw or "universe").strip()
    if raw == "universe":
        symbols = list(cfg.get("stocks", {}).get("scan_universe", []))
        try:
            symbols = sorted(set(symbols) | set(PITUniverseBuilder().members_between(start, end)))
        except Exception as exc:
            log.warning("Could not load PIT universe, using configured stock universe only: %s", exc)
        return symbols
    symbol_file_prefix = "file:"
    if raw.startswith(symbol_file_prefix):
        path = Path(raw[len(symbol_file_prefix):])
        return [line.strip() for line in path.read_text().splitlines() if line.strip()]
    return [symbol.strip().upper() for symbol in raw.split(",") if symbol.strip()]


def _prefetch_one(symbol: str, month: datetime, cache_dir: Path, force_refresh: bool, retries: int) -> tuple[str, str, bool, str]:
    for attempt in range(retries + 1):
        try:
            ensure_month_cached(symbol, month, cache_dir=cache_dir, force_refresh=force_refresh)
            return symbol, month.strftime("%Y-%m"), True, ""
        except Exception as exc:
            if attempt >= retries:
                return symbol, month.strftime("%Y-%m"), False, str(exc)
            time.sleep(min(30, 2 ** attempt))
    return symbol, month.strftime("%Y-%m"), False, "unreachable retry state"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prefetch Alpaca stock 1-minute bars into data/minute_cache.")
    parser.add_argument("--start", required=True, help="Start date, e.g. 2019-01-01")
    parser.add_argument("--end", default="auto", help="End date; defaults to now - 2 days")
    parser.add_argument("--symbols", default="universe", help="'universe', comma symbols, or file:/path/to/symbols.txt")
    parser.add_argument("--cache-dir", default=str(BASE_DIR / "data" / "minute_cache"))
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--force-refresh", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    end_default = datetime.now(timezone.utc) - timedelta(days=2)
    start = _parse_date(args.start, default=end_default)
    end = _parse_date(args.end, default=end_default)
    if start > end:
        raise SystemExit("--start must be before --end")

    cfg = get_config()
    symbols = _symbols_from_arg(args.symbols, cfg, start, end)
    months = _month_starts(start, end)
    cache_dir = Path(args.cache_dir)
    work = [(symbol, month) for symbol in symbols for month in months]
    log.info("Prefetching %s symbol-months for %s symbols into %s", len(work), len(symbols), cache_dir)

    failures = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(_prefetch_one, symbol, month, cache_dir, args.force_refresh, args.retries): (symbol, month)
            for symbol, month in work
        }
        for idx, future in enumerate(as_completed(futures), start=1):
            symbol, month, ok, error = future.result()
            if ok:
                log.info("[%s/%s] cached %s %s", idx, len(work), symbol, month)
            else:
                log.error("[%s/%s] failed %s %s: %s", idx, len(work), symbol, month, error)
                failures.append((symbol, month, error))

    if failures:
        log.error("Prefetch finished with %s failures", len(failures))
        return 1
    log.info("Prefetch complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
