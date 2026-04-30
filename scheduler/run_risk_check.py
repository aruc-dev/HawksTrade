"""
HawksTrade - Risk Check Runner
================================
Called every 15 minutes during market hours.
Checks all open positions for stop-loss and take-profit triggers.
Also enforces the daily loss limit — closes ALL positions if hit.

Run directly:
  python scheduler/run_risk_check.py [--dry-run]
"""

from __future__ import annotations

import sys
import logging
import argparse
import json
import math
import os
import tempfile
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import alpaca_client as ac
from core.config_loader import get_config
from core import risk_manager as rm
from core import order_executor as oe
from core.run_markers import RunScope, run_scope
from core.logging_config import runtime_log_handlers
from scheduler.reconcile_trade_log import safe_reconcile
from tracking.trade_log import get_open_trades, update_high_water_prices

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR  = BASE_DIR / "logs"
DEFAULT_PRICE_FAILURE_STATE_FILE = BASE_DIR / "data" / "price_fetch_failures.json"
DEFAULT_PRICE_FAILURE_ALERT_THRESHOLD = 3


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=runtime_log_handlers(LOG_DIR, f"risk_{_utc_now().strftime('%Y%m%d')}.log"),
)
log = logging.getLogger("run_risk_check")

CFG = get_config()


def _position_asset_class(pos) -> str:
    return (
        "crypto"
        if str(getattr(pos, "asset_class", "")).lower().endswith("crypto")
        else "stock"
    )


def _find_matching_trade(symbol: str, open_trades: list) -> dict | None:
    normalized = ac.normalize_symbol(symbol)
    for trade in reversed(open_trades):
        if ac.normalize_symbol(trade.get("symbol", "")) == normalized and trade.get("side") == "buy":
            return trade
    return None


def _mark_unhealthy_exit_result(marker: RunScope | None, result: dict | None, stage: str):
    if marker is None or not result:
        return
    if result.get("status") == "pending_exit_check_failed":
        marker.mark_error(
            stage=stage,
            error_type="PendingExitOrderCheckFailed",
            blocked_exit_symbol=result.get("symbol", ""),
        )
    elif result.get("status") in {"exit_failed", "invalid_exit_price", "invalid_entry_price"}:
        marker.mark_error(
            stage=stage,
            error_type=result.get("error_type", "ExitFailed"),
            failed_exit_symbol=result.get("symbol", ""),
            error=result.get("error", ""),
        )


def _emergency_exit_verified(result: dict | None, dry_run: bool) -> bool:
    if not result:
        return False
    expected_status = "dry_run" if dry_run else "closed"
    return result.get("status") == expected_status


def _mark_unverified_emergency_exit(
    marker: RunScope | None,
    result: dict | None,
    symbol: str,
) -> None:
    _mark_unhealthy_exit_result(marker, result, "emergency_exit")
    if marker is not None and marker.status != "error":
        marker.mark_error(
            stage="emergency_exit",
            error_type="EmergencyExitNotVerified",
            failed_exit_symbol=(result or {}).get("symbol", symbol),
            exit_status=(result or {}).get("status", "missing_result"),
        )


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


def _reconcile_trade_log_after_run(
    marker: RunScope | None,
    dry_run: bool,
    *,
    positions: list | None = None,
    context: str = "run_risk_check.post_run",
) -> None:
    if dry_run:
        log.info("Trade-log reconciliation skipped during dry run.")
        return
    closed_orders = None
    if positions is not None:
        try:
            closed_orders = ac.get_closed_orders()
        except Exception as exc:
            info = ac.classify_alpaca_error(exc)
            log.warning(
                "Could not fetch closed broker orders for reconciliation: %s "
                "| category=%s retryable=%s status_code=%s",
                exc,
                info.category,
                info.retryable,
                info.status_code or "",
                exc_info=True,
            )
    summary = safe_reconcile(
        positions=positions,
        closed_orders=closed_orders,
        context=context,
        logger=log,
    )
    if summary is None and marker is not None:
        marker.mark_error(
            stage="trade_log_reconciliation",
            error_type="TradeLogReconciliationFailed",
        )


def _price_failure_state_file() -> Path:
    return Path(os.getenv("HAWKSTRADE_PRICE_FAILURE_STATE_FILE", str(DEFAULT_PRICE_FAILURE_STATE_FILE)))


def _price_failure_alert_threshold() -> int:
    raw = os.getenv("HAWKSTRADE_PRICE_FAILURE_ALERT_THRESHOLD")
    if raw is None:
        return DEFAULT_PRICE_FAILURE_ALERT_THRESHOLD
    try:
        return max(1, int(raw))
    except ValueError:
        log.warning(
            "Invalid HAWKSTRADE_PRICE_FAILURE_ALERT_THRESHOLD=%r; using default %s",
            raw,
            DEFAULT_PRICE_FAILURE_ALERT_THRESHOLD,
        )
        return DEFAULT_PRICE_FAILURE_ALERT_THRESHOLD


def _empty_price_failure_state(threshold: int) -> dict:
    return {
        "version": 1,
        "threshold": threshold,
        "updated_at": _utc_now_iso(),
        "symbols": {},
    }


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _load_price_failure_state(path: Path | None = None) -> dict:
    state_path = path or _price_failure_state_file()
    threshold = _price_failure_alert_threshold()
    if not state_path.exists():
        return _empty_price_failure_state(threshold)

    try:
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read price failure state %s: %s; starting fresh.", state_path, exc)
        return _empty_price_failure_state(threshold)

    if not isinstance(state, dict):
        return _empty_price_failure_state(threshold)
    symbols = state.get("symbols")
    if not isinstance(symbols, dict):
        state["symbols"] = {}
    state["version"] = 1
    state["threshold"] = threshold
    state["updated_at"] = state.get("updated_at") or _utc_now_iso()
    return state


def _save_price_failure_state(state: dict, path: Path | None = None) -> None:
    state_path = path or _price_failure_state_file()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _utc_now_iso()
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{state_path.name}.",
        suffix=".tmp",
        dir=str(state_path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp_name, state_path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _state_symbol_key(symbol: str) -> str:
    return ac.normalize_symbol(symbol)


def _record_price_failure(
    *,
    symbol: str,
    price_symbol: str,
    asset_class: str,
    reason: str,
    exc: Exception | None = None,
    current_price: float | None = None,
) -> dict:
    threshold = _price_failure_alert_threshold()
    state = _load_price_failure_state()
    key = _state_symbol_key(symbol)
    existing = state["symbols"].get(key, {})
    previous_count = _safe_int(existing.get("count"), 0)
    info = ac.classify_alpaca_error(exc) if exc is not None else None
    entry = {
        "symbol": symbol,
        "price_symbol": price_symbol,
        "asset_class": asset_class,
        "count": previous_count + 1,
        "threshold": threshold,
        "status": "nok" if previous_count + 1 >= threshold else "warn",
        "reason": reason,
        "last_failed_at": _utc_now_iso(),
        "last_error": str(exc) if exc is not None else "",
        "error_type": type(exc).__name__ if exc is not None else "",
        "error_category": info.category if info is not None else "",
        "retryable": info.retryable if info is not None else False,
        "status_code": info.status_code if info is not None else None,
        "current_price": current_price,
    }
    state["threshold"] = threshold
    state["symbols"][key] = entry
    _save_price_failure_state(state)
    return entry


def _clear_price_failure(symbol: str) -> None:
    state = _load_price_failure_state()
    key = _state_symbol_key(symbol)
    existing = state["symbols"].pop(key, None)
    if existing is None:
        return
    _save_price_failure_state(state)
    log.info(
        "PRICE_FETCH_RECOVERED symbol=%s previous_count=%s",
        symbol,
        existing.get("count", 0),
    )


def _prune_price_failures_for_positions(positions: list) -> None:
    state = _load_price_failure_state()
    known_symbols = {_state_symbol_key(getattr(pos, "symbol", "")) for pos in positions}
    stale_keys = [key for key in state["symbols"] if key not in known_symbols]
    if not stale_keys:
        return
    for key in stale_keys:
        state["symbols"].pop(key, None)
    _save_price_failure_state(state)
    log.info("PRICE_FETCH_STATE_PRUNED removed=%s", ",".join(stale_keys))


def _mark_price_failure_if_unhealthy(marker: RunScope | None, entry: dict) -> None:
    if marker is None:
        return
    count = _safe_int(entry.get("count"), 0)
    threshold = _safe_int(entry.get("threshold"), _price_failure_alert_threshold())
    if count < threshold:
        return
    marker.mark_error(
        stage="price_fetch",
        error_type="RepeatedPriceFetchFailure",
        price_failure_symbol=entry.get("symbol", ""),
        price_failure_count=count,
        price_failure_threshold=threshold,
        error_category=entry.get("error_category", "") or entry.get("reason", ""),
        retryable=entry.get("retryable", False),
        status_code=entry.get("status_code"),
    )


def _log_price_failure(entry: dict) -> None:
    log.warning(
        "PRICE_FETCH_FAILURE symbol=%s price_symbol=%s asset_class=%s count=%s "
        "threshold=%s status=%s reason=%s category=%s retryable=%s status_code=%s error=%s",
        entry.get("symbol", ""),
        entry.get("price_symbol", ""),
        entry.get("asset_class", ""),
        entry.get("count", 0),
        entry.get("threshold", 0),
        entry.get("status", ""),
        entry.get("reason", ""),
        entry.get("error_category", ""),
        entry.get("retryable", False),
        entry.get("status_code") or "",
        entry.get("last_error", ""),
    )


def run(dry_run: bool = False, marker: RunScope | None = None):
    log.info(f"--- Risk Check | dry_run={'ON' if dry_run else 'OFF'} ---")

    # --- Daily loss limit check ---
    try:
        loss_exceeded = rm.daily_loss_exceeded()
    except Exception as e:
        info = _mark_alpaca_error(marker, "daily_loss_check", e)
        log.error(
            "Daily loss check failed; skipping risk check: %s "
            "| category=%s retryable=%s status_code=%s",
            e,
            info.category,
            info.retryable,
            info.status_code or "",
            exc_info=True,
        )
        return

    if loss_exceeded:
        log.warning("DAILY LOSS LIMIT EXCEEDED. Closing all positions for protection.")
        try:
            positions = ac.get_all_positions()
        except Exception as e:
            info = _mark_alpaca_error(marker, "emergency_fetch_positions", e)
            log.error(
                "Could not fetch positions for emergency close: %s "
                "| category=%s retryable=%s status_code=%s",
                e,
                info.category,
                info.retryable,
                info.status_code or "",
                exc_info=True,
            )
            return
        open_trades = get_open_trades()
        emergency_failed = False
        for pos in positions:
            symbol      = pos.symbol
            asset_class = (
                "crypto"
                if str(getattr(pos, "asset_class", "")).lower().endswith("crypto")
                else "stock"
            )
            trade = _find_matching_trade(symbol, open_trades)
            exit_symbol = trade.get("symbol", symbol) if trade and asset_class == "crypto" else symbol
            result = oe.exit_position(
                exit_symbol,
                reason="Daily loss limit — emergency close",
                asset_class=asset_class,
                dry_run=dry_run,
                force_market=True,
            )
            if not _emergency_exit_verified(result, dry_run):
                emergency_failed = True
                log.error(
                    "Emergency close for %s was not verified; status=%s",
                    exit_symbol,
                    (result or {}).get("status", "missing_result"),
                )
                _mark_unverified_emergency_exit(marker, result, exit_symbol)
        if emergency_failed:
            log.error("Emergency liquidation incomplete. Bot will not trade again today.")
        else:
            log.warning("All emergency exits verified. Bot will not trade again today.")
        _reconcile_trade_log_after_run(
            marker,
            dry_run,
            context="run_risk_check.emergency_close",
        )
        if marker is not None and marker.status != "error":
            marker.mark_status("ok", outcome="emergency_close")
        return

    # --- Per-position stop-loss / take-profit check ---
    open_trades = get_open_trades()
    try:
        positions = ac.get_all_positions()
    except Exception as e:
        info = _mark_alpaca_error(marker, "fetch_positions", e)
        log.error(
            "Could not fetch positions for risk check: %s "
            "| category=%s retryable=%s status_code=%s",
            e,
            info.category,
            info.retryable,
            info.status_code or "",
            exc_info=True,
        )
        return

    if not positions:
        if open_trades:
            log.warning(
                f"Trade log has {len(open_trades)} open row(s), but Alpaca has no open positions; "
                "skipping stale log rows."
            )
            _prune_price_failures_for_positions([])
        else:
            log.info("No open positions to check.")
            _prune_price_failures_for_positions([])
        _reconcile_trade_log_after_run(marker, dry_run, positions=[])
        return

    _prune_price_failures_for_positions(positions)

    observed_prices: dict[str, float] = {}

    for pos in positions:
        symbol      = pos.symbol
        asset_class = _position_asset_class(pos)
        trade       = _find_matching_trade(symbol, open_trades)
        price_symbol = trade.get("symbol", symbol) if trade and asset_class == "crypto" else symbol

        try:
            raw_entry_price = getattr(pos, "avg_entry_price", None)
            entry_price = float(raw_entry_price)
        except (ValueError, TypeError):
            entry_price = 0.0
        if entry_price <= 0:
            try:
                entry_price = float((trade or {}).get("entry_price"))
            except (ValueError, TypeError):
                log.warning(f"Invalid entry price for {symbol}, skipping.")
                continue
        if entry_price <= 0:
            log.warning(f"Non-positive entry price {entry_price} for {symbol}, skipping.")
            continue

        try:
            if asset_class == "crypto":
                current_price = ac.get_crypto_latest_price(price_symbol)
            else:
                current_price = ac.get_stock_latest_price(price_symbol)
        except Exception as e:
            entry = _record_price_failure(
                symbol=symbol,
                price_symbol=price_symbol,
                asset_class=asset_class,
                reason="exception",
                exc=e,
            )
            _log_price_failure(entry)
            _mark_price_failure_if_unhealthy(marker, entry)
            continue

        if current_price <= 0:
            entry = _record_price_failure(
                symbol=symbol,
                price_symbol=price_symbol,
                asset_class=asset_class,
                reason="non_positive_price",
                current_price=current_price,
            )
            _log_price_failure(entry)
            _mark_price_failure_if_unhealthy(marker, entry)
            continue

        _clear_price_failure(symbol)
        observed_prices[symbol] = current_price

        # Use the ATR/custom stop recorded at entry only when it actually differs
        # from what the global stop would have been — i.e. the strategy widened
        # the stop below the global floor.  If the trade log just holds the global
        # stop (no ATR override was applied), pass None so should_exit_position
        # uses the live global percentage and avoids mislabelling exits.
        custom_stop: float | None = None
        if trade:
            try:
                raw_sl = trade.get("stop_loss")
                if raw_sl not in (None, "", "nan"):
                    parsed = float(raw_sl)
                    if math.isfinite(parsed):
                        global_sl_at_entry = rm.stop_loss_price(entry_price)
                        if abs(parsed - global_sl_at_entry) > 0.0001:
                            custom_stop = parsed
            except (ValueError, TypeError):
                pass
        should_exit, reason = rm.should_exit_position(symbol, entry_price, current_price, custom_stop_price=custom_stop)
        if should_exit:
            log.info(f"EXIT triggered for {symbol}: {reason}")
            exit_symbol = price_symbol if asset_class == "crypto" else symbol
            result = oe.exit_position(exit_symbol, reason=reason, asset_class=asset_class, dry_run=dry_run)
            _mark_unhealthy_exit_result(marker, result, "risk_exit")
        else:
            pnl = (current_price - entry_price) / entry_price
            log.info(f"  {symbol:<12} entry={entry_price:.4f} now={current_price:.4f} "
                     f"P&L={pnl:+.2%} — HOLD")

    if observed_prices and not dry_run:
        update_high_water_prices(observed_prices)

    _reconcile_trade_log_after_run(marker, dry_run, positions=positions)

    log.info("Risk check complete.")
    if marker is not None and marker.status != "error":
        marker.mark_status("ok", outcome="completed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HawksTrade Risk Check")
    parser.add_argument("--dry-run", action="store_true",
                        help="Log intended exits without submitting orders")
    args = parser.parse_args()
    with run_scope(
        log,
        "run_risk_check",
        dry_run="ON" if args.dry_run else "OFF",
    ) as marker:
        run(dry_run=args.dry_run, marker=marker)
