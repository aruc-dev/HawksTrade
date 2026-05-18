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

from __future__ import annotations

import sys
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from typing import List

from core import alpaca_client as ac
from core.config_loader import get_config
from core.correlation_guard import evaluate_crypto_correlation
from core import order_executor as oe
from core import risk_manager as rm
from core.portfolio_construction import build_order_plan, build_portfolio_target
from core.protection_manager import ProtectionManager
from core.risk_pipeline import EntryRiskContext, evaluate_entry_target
from core.trading_models import OrderPlan, RiskDecision, signal_from_strategy_dict
from core.exit_policy import (
    evaluate_hold_exit,
    finite_positive_float,
    normalize_momentum_exit_policy,
)
from core.run_markers import RunScope, run_scope
from core.logging_config import runtime_log_handlers
from core.portfolio import get_open_symbols, print_snapshot
from scheduler.reconcile_trade_log import safe_reconcile
from tracking.trade_log import get_closed_trades, get_open_trades, get_trade_age_days, update_high_water_prices
from strategies.momentum import MomentumStrategy
from strategies.relative_strength import RelativeStrengthStrategy
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

CFG = get_config()

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

def _configured_hold_days(cfg: dict) -> dict:
    return {
        name: strategy_cfg["hold_days"]
        for name, strategy_cfg in cfg.get("strategies", {}).items()
        if strategy_cfg.get("hold_days")
    }


def _closed_trade_counts_by_strategy() -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in get_closed_trades():
        strategy = row.get("strategy")
        if strategy:
            counts[strategy] = counts.get(strategy, 0) + 1
    return counts


def _strategy_uses_profit_protection(strategy: str, strategy_cfg: dict) -> bool:
    if strategy == "momentum":
        return _momentum_exit_policy_for_scan(strategy_cfg) == "profit_trailing"
    return bool(strategy_cfg.get("profit_trailing_enabled", False))


def _configured_policy_aware_hold_strategies(cfg: dict) -> set[str]:
    return {
        name
        for name, strategy_cfg in cfg.get("strategies", {}).items()
        if strategy_cfg.get("hold_days") and _strategy_uses_profit_protection(name, strategy_cfg)
    }


def _momentum_exit_policy_for_scan(strategy_cfg: dict, *, symbol: str | None = None) -> str:
    try:
        return normalize_momentum_exit_policy(strategy_cfg.get("exit_policy"))
    except ValueError as e:
        context = f" for {symbol}" if symbol else ""
        log.warning(
            "Invalid momentum exit_policy%s: %s; using risk_only_baseline for scan hold checks.",
            context,
            e,
        )
        return "risk_only_baseline"


HOLD_DAYS = _configured_hold_days(CFG)
POLICY_AWARE_HOLD_STRATEGIES = _configured_policy_aware_hold_strategies(CFG)

# ── Strategy Registry ─────────────────────────────────────────────────────────

STOCK_STRATEGIES  = [MomentumStrategy(), RelativeStrengthStrategy(), RSIReversionStrategy(), GapUpStrategy()]
CRYPTO_STRATEGIES = [MACrossoverStrategy(), RangeBreakoutStrategy()]


# ── Helpers ───────────────────────────────────────────────────────────────────

class PendingEntryOrderCheckFailed(RuntimeError):
    """Raised when broker buy orders cannot be inspected safely."""


def _already_holding(symbol: str, open_symbols: list) -> bool:
    return any(_symbols_match(symbol, open_symbol) for open_symbol in open_symbols)


def _normalized_symbol_set(symbols) -> set:
    return {ac.normalize_symbol(str(symbol)) for symbol in symbols if str(symbol or "").strip()}


def _symbols_match(left: str, right: str) -> bool:
    return ac.normalize_symbol(left) == ac.normalize_symbol(right)


def _asset_class_value(value) -> str:
    raw = str(getattr(value, "value", value) or "").strip().lower()
    if "crypto" in raw:
        return "crypto"
    if raw in {"stock", "stocks", "equity", "us_equity"}:
        return "stock"
    return ""


def _planned_asset_class(symbol: str, explicit_asset_class=None) -> str:
    asset_class = _asset_class_value(explicit_asset_class)
    if asset_class:
        return asset_class
    normalized = ac.normalize_symbol(str(symbol))
    crypto_symbols = _normalized_symbol_set(CRYPTO_UNIVERSE)
    if normalized in crypto_symbols or "/" in str(symbol):
        return "crypto"
    return "stock"


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


def _pending_entry_symbols() -> dict:
    """Return normalized symbols with open broker buy orders and asset classes."""
    try:
        orders = ac.get_open_orders()
    except Exception as e:
        log.error(f"Could not check pending entry orders; blocking new entries fail-closed: {e}")
        raise PendingEntryOrderCheckFailed("Could not check pending entry orders") from e

    pending = {}
    for order in orders or []:
        if _order_side(order) == "buy":
            symbol = _order_value(order, "symbol")
            if symbol:
                pending[ac.normalize_symbol(str(symbol))] = _planned_asset_class(
                    str(symbol),
                    _order_value(order, "asset_class"),
                )
    return pending


def _coerce_pending_entry_symbols(pending_entries) -> dict:
    if isinstance(pending_entries, dict):
        return {
            ac.normalize_symbol(str(symbol)): _planned_asset_class(str(symbol), asset_class)
            for symbol, asset_class in pending_entries.items()
            if str(symbol or "").strip()
        }
    return {
        ac.normalize_symbol(str(symbol)): _planned_asset_class(str(symbol))
        for symbol in (pending_entries or set())
        if str(symbol or "").strip()
    }


def _planned_asset_classes(open_symbols: list, pending_entries) -> dict:
    planned = {
        ac.normalize_symbol(str(symbol)): _planned_asset_class(str(symbol))
        for symbol in open_symbols
        if str(symbol or "").strip()
    }
    planned.update(_coerce_pending_entry_symbols(pending_entries))
    return planned


def _planned_position_counts(planned_asset_classes: dict) -> tuple[int, int, int]:
    total = len(planned_asset_classes)
    crypto_count = sum(1 for asset_class in planned_asset_classes.values() if asset_class == "crypto")
    stock_count = total - crypto_count
    return total, crypto_count, stock_count


def _planned_symbols_for_asset_class(planned_asset_classes: dict, asset_class: str) -> list[str]:
    return [
        symbol
        for symbol, planned_asset_class in planned_asset_classes.items()
        if planned_asset_class == asset_class
    ]


def _max_positions_planned(planned_symbols: set) -> bool:
    max_positions = int(CFG["trading"]["max_positions"])
    if len(planned_symbols) >= max_positions:
        log.info(f"Max planned positions reached: {len(planned_symbols)}/{max_positions}")
        return True
    return False


OPERATIONAL_GOVERNOR_BLOCK_CODES = {
    "account_lookup_failed",
    "broker_orders_lookup_failed",
    "missing_account_state",
    "missing_broker_orders",
    "order_history_unavailable",
}


def _planned_asset_class_cap_reached(asset_class: str, planned_asset_classes: dict) -> bool:
    if _max_positions_planned(planned_asset_classes):
        return True

    max_total = int(CFG["trading"]["max_positions"])
    max_crypto = int(CFG["trading"].get("max_crypto_positions", max_total))
    min_crypto = int(CFG["trading"].get("min_crypto_positions", 0))
    max_crypto = max(0, min(max_crypto, max_total))
    min_crypto = max(0, min(min_crypto, max_crypto))
    _, crypto_count, stock_count = _planned_position_counts(planned_asset_classes)

    if asset_class == "crypto":
        if max_crypto <= 0:
            log.info("Crypto entries disabled by planned cap (max_crypto_positions=0).")
            return True
        if crypto_count >= max_crypto:
            log.info(f"Max planned crypto positions reached: {crypto_count}/{max_crypto}")
            return True
        return False

    stock_slots_available = max_total - min_crypto
    if stock_count >= stock_slots_available:
        log.info(
            f"Planned stock slots exhausted: {stock_count}/{stock_slots_available} "
            f"({min_crypto} reserved for crypto)"
        )
        return True
    return False


def _register_entry_result(
    result,
    symbol: str,
    open_symbols: list,
    planned_symbols: set,
    new_entry_symbols: set,
    asset_class: str = "stock",
    planned_asset_classes: dict | None = None,
):
    if not result:
        return
    if result.get("status") in {"entry_failed", "order_governor_blocked"}:
        return
    normalized = ac.normalize_symbol(symbol)
    planned_symbols.add(normalized)
    if planned_asset_classes is not None:
        planned_asset_classes[normalized] = _planned_asset_class(symbol, asset_class)
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
    status = result.get("status")
    governor_code = str(result.get("governor_code") or "")
    if status == "pending_exit_check_failed" or (
        status == "order_governor_blocked"
        and governor_code in OPERATIONAL_GOVERNOR_BLOCK_CODES
    ):
        marker.mark_error(
            stage=stage,
            error_type=result.get("error_type", "PendingExitOrderCheckFailed"),
            blocked_exit_symbol=result.get("symbol", ""),
            governor_code=governor_code,
            error=result.get("error", ""),
        )
    elif status in {"exit_failed", "invalid_exit_price", "invalid_entry_price"}:
        marker.mark_error(
            stage=stage,
            error_type=result.get("error_type", "ExitFailed"),
            failed_exit_symbol=result.get("symbol", ""),
            error=result.get("error", ""),
        )


def _mark_unhealthy_entry_result(marker: RunScope | None, result: dict | None, stage: str):
    if marker is None or not result:
        return
    status = result.get("status")
    governor_code = str(result.get("governor_code") or "")
    if status == "entry_failed" or (
        status == "order_governor_blocked"
        and governor_code in OPERATIONAL_GOVERNOR_BLOCK_CODES
    ):
        marker.mark_error(
            stage=stage,
            error_type=result.get("error_type", "EntryFailed"),
            failed_entry_symbol=result.get("symbol", ""),
            governor_code=governor_code,
            error=result.get("error", ""),
        )


def _log_active_protection_locks(locks: list) -> None:
    if not locks:
        return
    log.warning("Active protection locks: %s", len(locks))
    for lock in locks:
        log.warning(
            "Protection lock active | type=%s scope=%s key=%s expires_at=%s reason=%s",
            lock.lock_type,
            lock.scope,
            lock.key,
            lock.expires_at.isoformat(),
            lock.reason,
        )


def _log_entry_risk_block(decision: RiskDecision) -> None:
    target = decision.target
    if decision.code == "duplicate_planned_symbol":
        log.debug(f"Already holding {target.symbol}, skipping entry.")
        return
    if decision.code == "planned_cap_reached":
        return
    if decision.code == "protection_refresh_failed":
        log.warning(
            "Entry blocked because protection refresh failed | symbol=%s strategy=%s",
            target.symbol,
            target.strategy,
        )
        return
    if decision.code == "protection_lock":
        log.warning(
            "Entry blocked by protection lock | symbol=%s strategy=%s lock_type=%s scope=%s key=%s reason=%s",
            target.symbol,
            target.strategy,
            decision.context.get("lock_type", ""),
            decision.context.get("scope", ""),
            decision.context.get("key", ""),
            decision.reason,
        )
        return
    log.info(
        "Entry blocked by risk pipeline | symbol=%s strategy=%s code=%s reason=%s",
        target.symbol,
        target.strategy,
        decision.code,
        decision.reason,
    )


def _crypto_correlation_allows(symbol: str, planned_asset_classes: dict) -> bool:
    planned_crypto_symbols = _planned_symbols_for_asset_class(planned_asset_classes, "crypto")
    decision = evaluate_crypto_correlation(symbol, planned_crypto_symbols, cfg=CFG)
    if decision.allowed:
        return True
    log.warning(
        "Entry blocked by crypto correlation guard | symbol=%s code=%s reason=%s context=%s",
        symbol,
        decision.code,
        decision.reason,
        dict(decision.context or {}),
    )
    return False


def _entry_order_plan_for_signal(
    signal_row: dict,
    strategy,
    asset_class: str,
    planned_symbols: set,
    planned_asset_classes: dict,
    protection_manager: ProtectionManager,
    protection_entries_blocked: bool,
) -> tuple[OrderPlan | None, bool]:
    signal = signal_from_strategy_dict(
        signal_row,
        strategy_name=strategy.name,
        asset_class=asset_class,
    )
    target = build_portfolio_target(signal)
    decision = evaluate_entry_target(
        target,
        EntryRiskContext(
            planned_symbols=planned_symbols,
            planned_asset_classes=planned_asset_classes,
            protection_manager=protection_manager,
            protection_entries_blocked=protection_entries_blocked,
            normalize_symbol=ac.normalize_symbol,
            cap_reached=_planned_asset_class_cap_reached,
        ),
    )
    if not decision.allowed:
        _log_entry_risk_block(decision)
        return None, decision.code == "planned_cap_reached"
    return build_order_plan(decision), False


def _mark_exit_check_exception(marker: RunScope | None, stage: str, symbol: str, exc: Exception):
    info = ac.classify_alpaca_error(exc)
    if marker is not None:
        marker.mark_error(
            stage=stage,
            error_type=type(exc).__name__,
            failed_exit_symbol=symbol,
            error=str(exc),
            error_category=info.category,
            retryable=info.retryable,
            status_code=info.status_code,
        )
    return info


def _mark_alpaca_error(marker: RunScope | None, stage: str, exc: Exception):
    info = ac.classify_alpaca_error(exc)
    if marker is not None:
        marker.mark_error(
            stage=stage,
            error_type=type(exc).__name__,
            error_category=info.category,
            retryable=info.retryable,
            status_code=info.status_code,
        )
    return info


def _mark_strategy_error(marker: RunScope | None, stage: str, strategy_name: str, exc: Exception):
    info = ac.classify_alpaca_error(exc)
    if marker is not None:
        marker.mark_error(
            stage=stage,
            strategy=strategy_name,
            error_type=type(exc).__name__,
            error_category=info.category,
            retryable=info.retryable,
            status_code=info.status_code,
        )
    return info


def _reconcile_trade_log_after_run(marker: RunScope | None, dry_run: bool) -> None:
    if dry_run:
        log.info("Trade-log reconciliation skipped during dry run.")
        return
    summary = safe_reconcile(context="run_scan.post_run", logger=log)
    if summary is None and marker is not None:
        marker.mark_error(
            stage="trade_log_reconciliation",
            error_type="TradeLogReconciliationFailed",
        )


def _prefetched_bars_are_sufficient(bars_data, required: dict[str, int]) -> bool:
    if not bars_data:
        return False
    for symbol, min_count in required.items():
        try:
            bars = bars_data[symbol]
        except Exception:
            return False
        if bars is None or len(bars) < min_count:
            return False
    return True


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


def _check_hold_day_exits(
    open_symbols: list,
    dry_run: bool = False,
    marker: RunScope | None = None,
    market_open: bool = True,
):
    """Exit any swing trade that has been held beyond its target hold_days.

    market_open: when False, stock (non-crypto) exits are skipped so that
    crypto-only overnight scans cannot submit stock sell orders outside
    regular market hours.
    """
    open_trades = get_open_trades()
    observed_prices: dict[str, float] = {}
    for trade in open_trades:
        symbol = str(trade.get("symbol", "") or "")
        strategy = str(trade.get("strategy", "") or "")
        try:
            if not symbol:
                log.warning("Skipping hold-day check for trade row without symbol.")
                continue
            if strategy not in HOLD_DAYS:
                continue

            asset_class = trade.get("asset_class", "stock")
            is_crypto = "crypto" in str(asset_class).lower()
            if not is_crypto and not market_open:
                log.debug(
                    f"Market closed; deferring hold-day check for stock position {symbol}."
                )
                continue

            target_days = HOLD_DAYS[strategy]
            age_days    = get_trade_age_days(symbol)
            strategy_cfg = CFG["strategies"][strategy]
            if strategy == "momentum":
                momentum_policy = _momentum_exit_policy_for_scan(strategy_cfg, symbol=symbol)
                if momentum_policy == "risk_only_baseline":
                    log.debug(
                        "Momentum risk_only_baseline ignores hold_days for %s; "
                        "deferring to risk checks only.",
                        symbol,
                    )
                    continue
                if momentum_policy == "fixed_hold":
                    if age_days < target_days:
                        continue
                    reason = f"Hold {int(age_days)}d"
                    log.info(f"momentum fixed-hold exit for {symbol}: {reason}")
                    result = oe.exit_position(
                        symbol,
                        reason=reason,
                        asset_class=asset_class,
                        dry_run=dry_run,
                        force_market=True,
                    )
                    _mark_unhealthy_exit_result(marker, result, "hold_day_exit")
                    continue

            if strategy in POLICY_AWARE_HOLD_STRATEGIES:
                asset_class = trade.get("asset_class", "stock")
                entry_price = finite_positive_float(trade.get("entry_price"))
                if entry_price is None:
                    log.warning(f"Skipping {strategy} hold check for {symbol}: missing entry price.")
                    continue
                try:
                    current_price = finite_positive_float(_latest_price_for_trade(symbol, asset_class))
                except Exception as e:
                    info = _mark_exit_check_exception(marker, "hold_day_exit", symbol, e)
                    log.error(
                        "Hold-day price fetch failed for %s; deferring exit check: %s "
                        "| category=%s retryable=%s status_code=%s",
                        symbol,
                        e,
                        info.category,
                        info.retryable,
                        info.status_code or "",
                        exc_info=True,
                    )
                    continue
                if current_price is None:
                    log.warning(f"Skipping {strategy} hold check for {symbol}: invalid current price {current_price}.")
                    continue
                observed_prices[symbol] = current_price
                peak_price = None
                high_water_text = str(trade.get("high_water_price") or "").strip()
                if high_water_text:
                    high_water_price = finite_positive_float(high_water_text)
                    if high_water_price is not None:
                        peak_price = max(high_water_price, current_price)
                if peak_price is None:
                    peak_price = _estimate_peak_price_since_entry(symbol, asset_class, current_price, age_days)
                decision = evaluate_hold_exit(
                    strategy=strategy,
                    age_days=age_days,
                    entry_price=entry_price,
                    current_price=current_price,
                    peak_price=peak_price,
                    strategy_cfg=strategy_cfg,
                )
                if not decision.should_exit:
                    if age_days >= target_days:
                        log.info(
                            f"{strategy} hold extended for {symbol}: "
                            f"age={age_days:.1f}d pnl={(current_price / entry_price - 1):+.2%}"
                        )
                    else:
                        log.debug(
                            f"{strategy} profit protection not triggered for {symbol}: "
                            f"age={age_days:.1f}d pnl={(current_price / entry_price - 1):+.2%}"
                        )
                    continue
                log.info(f"{strategy} hold/profit exit for {symbol}: {decision.reason}")
                result = oe.exit_position(
                    symbol,
                    reason=decision.reason,
                    asset_class=asset_class,
                    dry_run=dry_run,
                    force_market=decision.force_market,
                )
                _mark_unhealthy_exit_result(marker, result, "hold_day_exit")
                continue

            if age_days >= target_days:
                log.info(
                    f"Hold period expired for {symbol} ({strategy}): "
                    f"{age_days:.1f}d >= {target_days}d — exiting."
                )
                asset_class = trade.get("asset_class", "stock")
                result = oe.exit_position(symbol, reason="Hold period expired", asset_class=asset_class, dry_run=dry_run)
                _mark_unhealthy_exit_result(marker, result, "hold_day_exit")
        except Exception as e:
            info = _mark_exit_check_exception(marker, "hold_day_exit", symbol, e)
            log.error(
                "Hold-day exit check failed for %s; continuing with remaining positions: %s "
                "| category=%s retryable=%s status_code=%s",
                symbol,
                e,
                info.category,
                info.retryable,
                info.status_code or "",
                exc_info=True,
            )

    if observed_prices and not dry_run:
        update_high_water_prices(observed_prices)


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
        try:
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
            try:
                entry_price = float(trade["entry_price"])
            except (TypeError, ValueError) as e:
                log.error(f"Invalid entry price for strategy exit {symbol}: {trade.get('entry_price')}")
                _mark_exit_check_exception(marker, "strategy_exit", symbol, e)
                continue
            if entry_price <= 0:
                log.warning(f"Skipping strategy exit for {symbol}: non-positive entry price {entry_price}.")
                continue
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
                try:
                    should_exit, reason = strategy.should_exit(strategy_symbol, entry_price)
                except Exception as e:
                    info = _mark_exit_check_exception(marker, "strategy_exit", strategy_symbol, e)
                    log.error(
                        "Strategy exit check failed for %s via %s: %s "
                        "| category=%s retryable=%s status_code=%s",
                        strategy_symbol,
                        strategy.name,
                        e,
                        info.category,
                        info.retryable,
                        info.status_code or "",
                        exc_info=True,
                    )
                    break
                if should_exit:
                    log.info(f"Strategy exit signal for {strategy_symbol}: {reason}")
                    result = oe.exit_position(strategy_symbol, reason=reason, asset_class=asset_class, dry_run=dry_run)
                    _mark_unhealthy_exit_result(marker, result, "strategy_exit")
                    break  # position closed, no need to check further
        except Exception as e:
            info = _mark_exit_check_exception(marker, "strategy_exit", symbol, e)
            log.error(
                "Strategy exit phase failed for %s; continuing with remaining positions: %s "
                "| category=%s retryable=%s status_code=%s",
                symbol,
                e,
                info.category,
                info.retryable,
                info.status_code or "",
                exc_info=True,
            )


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
        info = _mark_alpaca_error(marker, "initial_connection", e)
        log.error(
            "Alpaca connection failed before scan; skipping run: %s "
            "| category=%s retryable=%s status_code=%s",
            e,
            info.category,
            info.retryable,
            info.status_code or "",
            exc_info=True,
        )
        return

    log.info(f"Market open: {market_open} | Open positions: {len(open_symbols)}")
    try:
        closed_trade_counts = _closed_trade_counts_by_strategy()
    except Exception as e:
        closed_trade_counts = {}
        log.warning("Could not precompute closed-trade counts; falling back to per-entry lookup: %s", e)

    # --- Check daily loss limit first ---
    try:
        loss_exceeded = rm.daily_loss_exceeded()
    except Exception as e:
        info = _mark_alpaca_error(marker, "daily_loss_check", e)
        log.error(
            "Daily loss check failed; skipping scan to avoid unsafe trading: %s "
            "| category=%s retryable=%s status_code=%s",
            e,
            info.category,
            info.retryable,
            info.status_code or "",
            exc_info=True,
        )
        return

    if loss_exceeded:
        log.warning("Daily loss limit exceeded. No new trades will be placed today.")
        print_snapshot()
        if marker is not None:
            marker.mark_status("ok", outcome="halted_by_daily_loss_limit")
        return

    try:
        pending_entry_symbols = {} if dry_run else _coerce_pending_entry_symbols(_pending_entry_symbols())
    except PendingEntryOrderCheckFailed as e:
        if marker is not None:
            marker.mark_error(
                stage="pending_entry_order_check",
                error_type="PendingEntryOrderCheckFailed",
                error=str(e),
            )
        log.error("Pending entry order check failed; skipping scan to avoid duplicate entries.", exc_info=True)
        return

    planned_asset_classes = _planned_asset_classes(open_symbols, pending_entry_symbols)
    planned_symbols = set(planned_asset_classes)
    new_entry_symbols = set()
    if pending_entry_symbols:
        log.info(f"Pending entry orders counted as planned positions: {len(pending_entry_symbols)}")

    protection_manager = ProtectionManager.from_config(CFG)
    protection_entries_blocked = False
    if protection_manager.enabled:
        try:
            if dry_run:
                active_locks = protection_manager.active_locks()
            else:
                active_locks = protection_manager.refresh_from_trade_log()
        except Exception as e:
            protection_entries_blocked = True
            if marker is not None:
                marker.mark_error(
                    stage="protection_refresh",
                    error_type=type(e).__name__,
                    error=str(e),
                )
            log.error(
                "Protection refresh failed; blocking new entries fail-closed while exit checks continue: %s",
                e,
                exc_info=True,
            )
        else:
            _log_active_protection_locks(active_locks)

    # --- Pre-fetch regime bars to share across strategies (reduces API calls) ---
    stock_regime_bars = None
    crypto_regime_bars = None
    if run_stocks and market_open:
        try:
            fetched = ac.get_stock_bars(["SPY", "QQQ"], timeframe="1Day", limit=255)
            if _prefetched_bars_are_sufficient(fetched, {"SPY": 252, "QQQ": 51}):
                stock_regime_bars = fetched
            else:
                log.warning("Stock regime prefetch missing required SPY/QQQ history; strategies will fetch live and fail closed if unavailable.")
        except Exception as e:
            log.warning(f"Could not pre-fetch stock regime bars: {e}")

    if run_crypto:
        try:
            fetched = ac.get_crypto_bars(["BTC/USD"], timeframe="1Day", limit=60)
            if _prefetched_bars_are_sufficient(fetched, {"BTC/USD": rm.crypto_regime_required_bars()}):
                crypto_regime_bars = fetched
            else:
                log.warning("Crypto regime prefetch missing required BTC/USD history; strategies will fetch live and fail closed if unavailable.")
        except Exception as e:
            log.warning(f"Could not pre-fetch crypto regime bars: {e}")

    all_strategies = []

    # --- Stock scan (only when market is open) ---
    if run_stocks and market_open:
        log.info("--- Running stock strategies ---")
        stock_universe = get_stock_universe()
        enabled_stock_strategies = _enabled_strategies(STOCK_STRATEGIES)
        for strategy in enabled_stock_strategies:
            try:
                scan_kwargs = {"regime_bars": stock_regime_bars}
                if strategy.name in {"momentum", "relative_strength"}:
                    scan_kwargs["existing_symbols"] = _planned_symbols_for_asset_class(
                        planned_asset_classes,
                        "stock",
                    )
                signals = strategy.scan(stock_universe, **scan_kwargs)
                for sig in signals:
                    if sig["action"] == "buy":
                        plan, stop_strategy = _entry_order_plan_for_signal(
                            sig,
                            strategy,
                            "stock",
                            planned_symbols,
                            planned_asset_classes,
                            protection_manager,
                            protection_entries_blocked,
                        )
                        if stop_strategy:
                            break
                        if plan is None:
                            continue
                        result = oe.enter_position(
                            plan.symbol,
                            strategy=plan.strategy,
                            asset_class=plan.asset_class,
                            dry_run=dry_run,
                            suggested_qty=plan.suggested_qty,
                            atr_stop_price=plan.atr_stop_price,
                            closed_trades_count=closed_trade_counts.get(plan.strategy),
                        )
                        _mark_unhealthy_entry_result(marker, result, "stock_entry")
                        _register_entry_result(
                            result,
                            plan.symbol,
                            open_symbols,
                            planned_symbols,
                            new_entry_symbols,
                            asset_class=plan.asset_class,
                            planned_asset_classes=planned_asset_classes,
                        )
            except Exception as e:
                _mark_strategy_error(marker, "stock_strategy", strategy.name, e)
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
                signals = strategy.scan(CRYPTO_UNIVERSE, regime_bars=crypto_regime_bars)
                for sig in signals:
                    if sig["action"] == "buy":
                        plan, stop_strategy = _entry_order_plan_for_signal(
                            sig,
                            strategy,
                            "crypto",
                            planned_symbols,
                            planned_asset_classes,
                            protection_manager,
                            protection_entries_blocked,
                        )
                        if stop_strategy:
                            break
                        if plan is None:
                            continue
                        if not _crypto_correlation_allows(plan.symbol, planned_asset_classes):
                            continue
                        result = oe.enter_position(
                            plan.symbol,
                            strategy=plan.strategy,
                            asset_class=plan.asset_class,
                            dry_run=dry_run,
                            suggested_qty=plan.suggested_qty,
                            atr_stop_price=plan.atr_stop_price,
                            closed_trades_count=closed_trade_counts.get(plan.strategy),
                        )
                        _mark_unhealthy_entry_result(marker, result, "crypto_entry")
                        _register_entry_result(
                            result,
                            plan.symbol,
                            open_symbols,
                            planned_symbols,
                            new_entry_symbols,
                            asset_class=plan.asset_class,
                            planned_asset_classes=planned_asset_classes,
                        )
            except Exception as e:
                _mark_strategy_error(marker, "crypto_strategy", strategy.name, e)
                log.error(f"Strategy {strategy.name} failed: {e}", exc_info=True)
        all_strategies.extend(enabled_crypto_strategies)

    # --- Strategy-level exit checks ---
    log.info("--- Checking strategy exit conditions ---")
    try:
        open_symbols = get_open_symbols()  # refresh after entries
    except Exception as e:
        info = _mark_alpaca_error(marker, "refresh_open_positions", e)
        log.error(
            "Could not refresh open positions; skipping exit checks: %s "
            "| category=%s retryable=%s status_code=%s",
            e,
            info.category,
            info.retryable,
            info.status_code or "",
            exc_info=True,
        )
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
    _check_hold_day_exits(open_symbols, dry_run=dry_run, marker=marker, market_open=market_open)

    _reconcile_trade_log_after_run(marker, dry_run)

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
