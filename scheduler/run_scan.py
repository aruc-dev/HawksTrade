"""
HawksTrade - Main Scanner & Trade Executor
==========================================
Entry point called by OS schedulers or manually.

What it does each run:
  1. Checks if market is open (for stock strategies)
  2. Runs all enabled strategies against their respective universes
  3. For each BUY signal: runs pre-trade checks & enters position
  4. For each open position: checks strategy-level exit signals
  5. Checks hold_days expiry on swing trades
  6. Prints portfolio snapshot

Run directly:
  python3 scheduler/run_scan.py [--crypto-only] [--stocks-only] [--dry-run]

See scheduler/README.md for launchd, cron, and Windows Task Scheduler setup.
"""

import sys
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from typing import List

import yaml
from core import alpaca_client as ac
from core import order_executor as oe
from core import risk_manager as rm
from core.exit_policy import should_exit_for_hold
from core.run_markers import RunScope, run_scope
from core.logging_config import runtime_log_handlers
from core.portfolio import get_open_symbols, print_snapshot
from tracking.trade_log import get_open_trades, get_trade_age_days
from strategies.momentum import MomentumStrategy
from strategies.rsi_reversion import RSIReversionStrategy
from strategies.gap_up import GapUpStrategy
from strategies.ma_crossover import MACrossoverStrategy
from strategies.range_breakout import RangeBreakoutStrategy
from screener.universe_builder import UniverseBuilder

# ── Logging ──────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR  = BASE_DIR / "logs"


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=runtime_log_handlers(LOG_DIR, f"scan_{_utc_now().strftime('%Y%m%d')}.log"),
)
log = logging.getLogger("run_scan")

# ── Config ────────────────────────────────────────────────────────────────────

with open(BASE_DIR / "config" / "config.yaml") as f:
    CFG = yaml.safe_load(f)

_screener = None

def get_stock_universe() -> List[str]:
    """Returns dynamic universe if screener enabled, else static config list."""
    global _screener
    if not CFG.get("screener", {}).get("enabled", False):
        return CFG["stocks"]["scan_universe"]
    if _screener is None:
        _screener = UniverseBuilder(CFG, alpaca_client=ac)
    return _screener.get_universe()

CRYPTO_UNIVERSE = CFG["crypto"]["scan_universe"]
INTRADAY_ON     = CFG["intraday"]["enabled"]

# Hold-day limits per strategy
HOLD_DAYS = {
    "momentum":       CFG["strategies"]["momentum"]["hold_days"],
    "gap_up":         CFG["strategies"]["gap_up"]["hold_days"],
    "range_breakout": CFG["strategies"]["range_breakout"]["hold_days"],
}

# ── Strategy Registry ─────────────────────────────────────────────────────────

STOCK_STRATEGIES  = [MomentumStrategy(), RSIReversionStrategy(), GapUpStrategy()]
CRYPTO_STRATEGIES = [MACrossoverStrategy(), RangeBreakoutStrategy()]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _already_holding(symbol: str, open_symbols: list) -> bool:
    return any(_symbols_match(symbol, open_symbol) for open_symbol in open_symbols)


def _normalized_symbol_set(symbols) -> set:
    return {ac.normalize_symbol(str(symbol)) for symbol in symbols if str(symbol or "").strip()}


def _symbols_match(left: str, right: str) -> bool:
    return ac.normalize_symbol(left) == ac.normalize_symbol(right)


def _strategy_enabled(strategy) -> bool:
    return CFG["strategies"].get(strategy.name, {}).get("enabled", False)


def _enabled_strategies(strategies) -> list:
    return [strategy for strategy in strategies if _strategy_enabled(strategy)]


def _order_value(order, name: str, default=None):
    if isinstance(order, dict):
        return order.get(name, default)
    return getattr(order, name, default)


def _order_side(order) -> str | None:
    side = _order_value(order, "side")
    if side is None:
        return None
    return str(getattr(side, "value", side)).lower()


def _pending_entry_symbols() -> set:
    """Return symbols with open broker buy orders so entries are not duplicated."""
    try:
        orders = ac.get_open_orders()
    except Exception as e:
        log.warning(f"Could not check pending entry orders; continuing with position snapshot only: {e}")
        return set()

    symbols = []
    for order in orders or []:
        if _order_side(order) == "buy":
            symbol = _order_value(order, "symbol")
            if symbol:
                symbols.append(symbol)
    return _normalized_symbol_set(symbols)


def _max_positions_planned(planned_symbols: set) -> bool:
    max_positions = int(CFG["trading"]["max_positions"])
    if len(planned_symbols) >= max_positions:
        log.info(f"Max planned positions reached: {len(planned_symbols)}/{max_positions}")
        return True
    return False


def _register_entry_result(result, symbol: str, open_symbols: list, planned_symbols: set, new_entry_symbols: set):
    if not result:
        return
    normalized = ac.normalize_symbol(symbol)
    planned_symbols.add(normalized)
    if result.get("status") not in {"open", "partially_filled", "dry_run"}:
        return
    new_entry_symbols.add(normalized)
    if not _already_holding(symbol, open_symbols):
        open_symbols.append(symbol)


def _asset_class_matches(strategy_asset_class: str, position_asset_class: str) -> bool:
    """Normalize singular/plural stock naming used across config and trade logs."""
    aliases = {
        "stock": "stock",
        "stocks": "stock",
        "crypto": "crypto",
    }
    strategy_class = aliases.get(strategy_asset_class, strategy_asset_class)
    position_class = aliases.get(position_asset_class, position_asset_class)
    return strategy_class in (position_class, "both")


def _latest_price_for_trade(symbol: str, asset_class: str) -> float:
    if asset_class == "crypto":
        return ac.get_crypto_latest_price(symbol)
    return ac.get_stock_latest_price(symbol)


def _mark_unhealthy_exit_result(marker: RunScope | None, result: dict | None, stage: str):
    if marker is None or not result:
        return
    if result.get("status") == "pending_exit_check_failed":
        marker.mark_error(
            stage=stage,
            error_type="PendingExitOrderCheckFailed",
            blocked_exit_symbol=result.get("symbol", ""),
        )


def _estimate_peak_price_since_entry(symbol: str, asset_class: str, current_price: float, age_days: float) -> float:
    """Estimate high-water price for trailing exits from recent daily bars."""
    limit = max(int(age_days) + 5, 15)
    try:
        if asset_class == "crypto":
            bars_data = ac.get_crypto_bars([symbol], timeframe="1Day", limit=limit)
        else:
            bars_data = ac.get_stock_bars([symbol], timeframe="1Day", limit=limit)
        bars = bars_data[symbol] if bars_data else None
        if not bars:
            return current_price
        highs = [float(getattr(bar, "high", current_price)) for bar in bars[-limit:]]
        return max([current_price] + highs)
    except Exception as e:
        log.warning(f"Could not estimate trailing peak for {symbol}; using current price: {e}")
        return current_price


def _check_hold_day_exits(open_symbols: list, dry_run: bool = False, marker: RunScope | None = None):
    """Exit any swing trade that has been held beyond its target hold_days."""
    open_trades = get_open_trades()
    for trade in open_trades:
        symbol   = trade["symbol"]
        strategy = trade["strategy"]
        if strategy not in HOLD_DAYS:
            continue
        target_days = HOLD_DAYS[strategy]
        age_days    = get_trade_age_days(symbol)
        if age_days >= target_days:
            if strategy == "momentum":
                asset_class = trade.get("asset_class", "stock")
                entry_price = float(trade.get("entry_price") or 0)
                if entry_price <= 0:
                    log.warning(f"Skipping momentum hold check for {symbol}: missing entry price.")
                    continue
                current_price = _latest_price_for_trade(symbol, asset_class)
                if current_price <= 0:
                    log.warning(f"Skipping momentum hold check for {symbol}: invalid current price {current_price}.")
                    continue
                peak_price = _estimate_peak_price_since_entry(symbol, asset_class, current_price, age_days)
                should_exit, reason = should_exit_for_hold(
                    strategy=strategy,
                    age_days=age_days,
                    entry_price=entry_price,
                    current_price=current_price,
                    peak_price=peak_price,
                    strategy_cfg=CFG["strategies"][strategy],
                )
                if not should_exit:
                    log.info(
                        f"Momentum hold extended for {symbol}: "
                        f"age={age_days:.1f}d pnl={(current_price / entry_price - 1):+.2%}"
                    )
                    continue
                log.info(f"Momentum hold exit for {symbol}: {reason}")
                result = oe.exit_position(symbol, reason=reason, asset_class=asset_class, dry_run=dry_run)
                _mark_unhealthy_exit_result(marker, result, "hold_day_exit")
                continue

            log.info(
                f"Hold period expired for {symbol} ({strategy}): "
                f"{age_days:.1f}d >= {target_days}d — exiting."
            )
            asset_class = trade.get("asset_class", "stock")
            result = oe.exit_position(symbol, reason="Hold period expired", asset_class=asset_class, dry_run=dry_run)
            _mark_unhealthy_exit_result(marker, result, "hold_day_exit")


def _check_strategy_exits(
    strategies,
    open_symbols,
    dry_run: bool = False,
    skip_symbols=None,
    marker: RunScope | None = None,
):
    """Ask each strategy if any open position should be exited."""
    skip_symbols = skip_symbols or set()
    open_trades = get_open_trades()
    for symbol in open_symbols:
        normalized_symbol = ac.normalize_symbol(symbol)
        if normalized_symbol in skip_symbols:
            log.info(f"Skipping same-scan strategy exit for new entry {symbol}.")
            continue
        matching    = [
            t for t in open_trades
            if _symbols_match(t["symbol"], symbol) and t["side"] == "buy"
        ]
        if not matching:
            continue
        trade = matching[-1]
        entry_price = float(trade["entry_price"])
        asset_class = trade.get("asset_class", "stock")
        trade_strategy = trade.get("strategy", "")
        if not CFG["strategies"].get(trade_strategy, {}).get("enabled", False):
            log.debug(f"Strategy {trade_strategy} disabled; skipping strategy exit for {symbol}.")
            continue
        strategy_symbol = trade.get("symbol", symbol) if asset_class == "crypto" else symbol
        relevant = [
            s for s in strategies
            if s.name == trade_strategy and _asset_class_matches(s.asset_class, asset_class)
        ]
        for strategy in relevant:
            should_exit, reason = strategy.should_exit(strategy_symbol, entry_price)
            if should_exit:
                log.info(f"Strategy exit signal for {strategy_symbol}: {reason}")
                result = oe.exit_position(strategy_symbol, reason=reason, asset_class=asset_class, dry_run=dry_run)
                _mark_unhealthy_exit_result(marker, result, "strategy_exit")
                break  # position closed, no need to check further


# ── Main Scan ─────────────────────────────────────────────────────────────────

def run(
    run_stocks: bool = True,
    run_crypto: bool = True,
    dry_run: bool = False,
    marker: RunScope | None = None,
):
    log.info("=" * 55)
    log.info(f"HawksTrade scan started | mode={CFG['mode'].upper()} | "
             f"intraday={'ON' if INTRADAY_ON else 'OFF'} | "
             f"dry_run={'ON' if dry_run else 'OFF'}")
    log.info("=" * 55)

    try:
        market_open  = ac.is_market_open()
        open_symbols = get_open_symbols()
    except Exception as e:
        if marker is not None:
            marker.mark_error(stage="initial_connection", error_type=type(e).__name__)
        log.error(f"Alpaca connection failed before scan; skipping run: {e}", exc_info=True)
        return

    log.info(f"Market open: {market_open} | Open positions: {len(open_symbols)}")

    # --- Check daily loss limit first ---
    try:
        loss_exceeded = rm.daily_loss_exceeded()
    except Exception as e:
        if marker is not None:
            marker.mark_error(stage="daily_loss_check", error_type=type(e).__name__)
        log.error(f"Daily loss check failed; skipping scan to avoid unsafe trading: {e}", exc_info=True)
        return

    if loss_exceeded:
        log.warning("Daily loss limit exceeded. No new trades will be placed today.")
        print_snapshot()
        if marker is not None:
            marker.mark_status("ok", outcome="halted_by_daily_loss_limit")
        return

    pending_entry_symbols = set() if dry_run else _pending_entry_symbols()
    planned_symbols = _normalized_symbol_set(open_symbols) | pending_entry_symbols
    new_entry_symbols = set()
    if pending_entry_symbols:
        log.info(f"Pending entry orders counted as planned positions: {len(pending_entry_symbols)}")

    all_strategies = []

    # --- Stock scan (only when market is open) ---
    if run_stocks and market_open:
        log.info("--- Running stock strategies ---")
        stock_universe = get_stock_universe()
        enabled_stock_strategies = _enabled_strategies(STOCK_STRATEGIES)
        for strategy in enabled_stock_strategies:
            try:
                signals = strategy.scan(stock_universe)
                for sig in signals:
                    sym = sig["symbol"]
                    normalized = ac.normalize_symbol(sym)
                    if normalized in planned_symbols:
                        log.debug(f"Already holding {sym}, skipping entry.")
                        continue
                    if _max_positions_planned(planned_symbols):
                        break
                    if sig["action"] == "buy":
                        result = oe.enter_position(sym, strategy=strategy.name, asset_class="stock", dry_run=dry_run)
                        _register_entry_result(result, sym, open_symbols, planned_symbols, new_entry_symbols)
            except Exception as e:
                log.error(f"Strategy {strategy.name} failed: {e}", exc_info=True)
        all_strategies.extend(enabled_stock_strategies)

    elif run_stocks and not market_open:
        log.info("Market closed. Stock strategies skipped.")

    # --- Crypto scan (24/7) ---
    if run_crypto:
        log.info("--- Running crypto strategies ---")
        enabled_crypto_strategies = _enabled_strategies(CRYPTO_STRATEGIES)
        for strategy in enabled_crypto_strategies:
            try:
                signals = strategy.scan(CRYPTO_UNIVERSE)
                for sig in signals:
                    sym = sig["symbol"]
                    normalized = ac.normalize_symbol(sym)
                    if normalized in planned_symbols:
                        log.debug(f"Already holding {sym}, skipping entry.")
                        continue
                    if _max_positions_planned(planned_symbols):
                        break
                    if sig["action"] == "buy":
                        result = oe.enter_position(sym, strategy=strategy.name, asset_class="crypto", dry_run=dry_run)
                        _register_entry_result(result, sym, open_symbols, planned_symbols, new_entry_symbols)
            except Exception as e:
                log.error(f"Strategy {strategy.name} failed: {e}", exc_info=True)
        all_strategies.extend(enabled_crypto_strategies)

    # --- Strategy-level exit checks ---
    log.info("--- Checking strategy exit conditions ---")
    try:
        open_symbols = get_open_symbols()  # refresh after entries
    except Exception as e:
        if marker is not None:
            marker.mark_error(stage="refresh_open_positions", error_type=type(e).__name__)
        log.error(f"Could not refresh open positions; skipping exit checks: {e}", exc_info=True)
        return
    skip_symbols = set() if INTRADAY_ON else new_entry_symbols
    _check_strategy_exits(
        all_strategies,
        open_symbols,
        dry_run=dry_run,
        skip_symbols=skip_symbols,
        marker=marker,
    )

    # --- Hold-day expiry exits ---
    log.info("--- Checking hold-day expiry ---")
    _check_hold_day_exits(open_symbols, dry_run=dry_run, marker=marker)

    # --- Print snapshot ---
    print_snapshot()
    log.info("Scan complete.")
    if marker is not None and marker.status != "error":
        marker.mark_status("ok", outcome="completed")


# ── CLI Entry ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HawksTrade Scanner")
    parser.add_argument("--crypto-only", action="store_true",
                        help="Run crypto strategies only")
    parser.add_argument("--stocks-only", action="store_true",
                        help="Run stock strategies only")
    parser.add_argument("--dry-run", action="store_true",
                        help="Log intended entries/exits without submitting orders")
    args = parser.parse_args()

    run_stocks = not args.crypto_only
    run_crypto = not args.stocks_only
    if run_stocks and run_crypto:
        scan_kind = "full"
    elif run_crypto:
        scan_kind = "crypto"
    elif run_stocks:
        scan_kind = "stock"
    else:
        scan_kind = "unknown"
    with run_scope(
        log,
        "run_scan",
        mode=CFG["mode"].upper(),
        intraday="ON" if INTRADAY_ON else "OFF",
        dry_run="ON" if args.dry_run else "OFF",
        run_stocks=run_stocks,
        run_crypto=run_crypto,
        scan_kind=scan_kind,
    ) as marker:
        run(run_stocks=run_stocks, run_crypto=run_crypto, dry_run=args.dry_run, marker=marker)
