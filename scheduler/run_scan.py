"""
HawksTrade - Main Scanner & Trade Executor
==========================================
Entry point called by Claude scheduled tasks (or manually).

What it does each run:
  1. Checks if market is open (for stock strategies)
  2. Runs all enabled strategies against their respective universes
  3. For each BUY signal: runs pre-trade checks & enters position
  4. For each open position: checks strategy-level exit signals
  5. Checks hold_days expiry on swing trades
  6. Prints portfolio snapshot

Run directly:
  python scheduler/run_scan.py [--crypto-only] [--stocks-only] [--dry-run]

Claude scheduled task will call this script automatically.
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
LOG_DIR.mkdir(exist_ok=True)


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / f"scan_{_utc_now().strftime('%Y%m%d')}.log"),
    ],
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


def _symbols_match(left: str, right: str) -> bool:
    return ac.normalize_symbol(left) == ac.normalize_symbol(right)


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


def _check_hold_day_exits(open_symbols: list, dry_run: bool = False):
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
                oe.exit_position(symbol, reason=reason, asset_class=asset_class, dry_run=dry_run)
                continue

            log.info(
                f"Hold period expired for {symbol} ({strategy}): "
                f"{age_days:.1f}d >= {target_days}d — exiting."
            )
            asset_class = trade.get("asset_class", "stock")
            oe.exit_position(symbol, reason="Hold period expired", asset_class=asset_class, dry_run=dry_run)


def _check_strategy_exits(strategies, open_symbols, dry_run: bool = False):
    """Ask each strategy if any open position should be exited."""
    for symbol in open_symbols:
        open_trades = get_open_trades()
        matching    = [
            t for t in open_trades
            if _symbols_match(t["symbol"], symbol) and t["side"] == "buy"
        ]
        if not matching:
            continue
        entry_price = float(matching[-1]["entry_price"])
        asset_class = matching[-1].get("asset_class", "stock")
        relevant    = [s for s in strategies if _asset_class_matches(s.asset_class, asset_class)]
        for strategy in relevant:
            should_exit, reason = strategy.should_exit(symbol, entry_price)
            if should_exit:
                log.info(f"Strategy exit signal for {symbol}: {reason}")
                oe.exit_position(symbol, reason=reason, asset_class=asset_class, dry_run=dry_run)
                break  # position closed, no need to check further


# ── Main Scan ─────────────────────────────────────────────────────────────────

def run(run_stocks: bool = True, run_crypto: bool = True, dry_run: bool = False):
    log.info("=" * 55)
    log.info(f"HawksTrade scan started | mode={CFG['mode'].upper()} | "
             f"intraday={'ON' if INTRADAY_ON else 'OFF'} | "
             f"dry_run={'ON' if dry_run else 'OFF'}")
    log.info("=" * 55)

    try:
        market_open  = ac.is_market_open()
        open_symbols = get_open_symbols()
    except Exception as e:
        log.error(f"Alpaca connection failed before scan; skipping run: {e}", exc_info=True)
        return

    log.info(f"Market open: {market_open} | Open positions: {len(open_symbols)}")

    # --- Check daily loss limit first ---
    try:
        loss_exceeded = rm.daily_loss_exceeded()
    except Exception as e:
        log.error(f"Daily loss check failed; skipping scan to avoid unsafe trading: {e}", exc_info=True)
        return

    if loss_exceeded:
        log.warning("Daily loss limit exceeded. No new trades will be placed today.")
        print_snapshot()
        return

    all_strategies = []

    # --- Stock scan (only when market is open) ---
    if run_stocks and market_open:
        log.info("--- Running stock strategies ---")
        stock_universe = get_stock_universe()
        for strategy in STOCK_STRATEGIES:
            if not CFG["strategies"].get(strategy.name, {}).get("enabled", False):
                continue
            try:
                signals = strategy.scan(stock_universe)
                for sig in signals:
                    sym = sig["symbol"]
                    if _already_holding(sym, open_symbols):
                        log.debug(f"Already holding {sym}, skipping entry.")
                        continue
                    if sig["action"] == "buy":
                        oe.enter_position(sym, strategy=strategy.name, asset_class="stock", dry_run=dry_run)
            except Exception as e:
                log.error(f"Strategy {strategy.name} failed: {e}", exc_info=True)
        all_strategies.extend(STOCK_STRATEGIES)

    elif run_stocks and not market_open:
        log.info("Market closed. Stock strategies skipped.")

    # --- Crypto scan (24/7) ---
    if run_crypto:
        log.info("--- Running crypto strategies ---")
        for strategy in CRYPTO_STRATEGIES:
            if not CFG["strategies"].get(strategy.name, {}).get("enabled", False):
                continue
            try:
                signals = strategy.scan(CRYPTO_UNIVERSE)
                for sig in signals:
                    sym = sig["symbol"]
                    if _already_holding(sym, open_symbols):
                        log.debug(f"Already holding {sym}, skipping entry.")
                        continue
                    if sig["action"] == "buy":
                        oe.enter_position(sym, strategy=strategy.name, asset_class="crypto", dry_run=dry_run)
            except Exception as e:
                log.error(f"Strategy {strategy.name} failed: {e}", exc_info=True)
        all_strategies.extend(CRYPTO_STRATEGIES)

    # --- Strategy-level exit checks ---
    log.info("--- Checking strategy exit conditions ---")
    try:
        open_symbols = get_open_symbols()  # refresh after entries
    except Exception as e:
        log.error(f"Could not refresh open positions; skipping exit checks: {e}", exc_info=True)
        return
    _check_strategy_exits(all_strategies, open_symbols, dry_run=dry_run)

    # --- Hold-day expiry exits ---
    log.info("--- Checking hold-day expiry ---")
    _check_hold_day_exits(open_symbols, dry_run=dry_run)

    # --- Print snapshot ---
    print_snapshot()
    log.info("Scan complete.")


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
    run(run_stocks=run_stocks, run_crypto=run_crypto, dry_run=args.dry_run)
