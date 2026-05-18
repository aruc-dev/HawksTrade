"""
HawksTrade - Order Executor
============================
Handles placing, confirming, and logging all orders.
Uses risk_manager checks before every entry.
Writes every trade to the trade log.
"""

from __future__ import annotations

import logging
import math
import os
import json
import time
from datetime import datetime, timezone
from typing import Optional, List, Dict
from pathlib import Path


from core import alpaca_client as ac
from core.execution_policy import (
    SINGLE_LEG_POLICY,
    TWO_LEG_POLICY,
    ExecutionPlan,
    OrderLeg,
    build_entry_execution_plan,
)
from core.order_governor import GovernorDecision, OrderGovernor, OrderIntent
from core import risk_manager as rm
from core.config_loader import get_config
from core.portfolio_construction import construct_entry_size
from core.sample_size_governor import effective_risk_for, scale_quantity
from core.slippage_model import estimate_slippage_bps, realised_slippage_bps
from core.strategy_readiness import ReadinessDecision, evaluate_strategy_live_readiness
from tracking import order_intents
from tracking.trade_log import log_trade, mark_trade_closed, get_trade_age_days, get_closed_trades

# ── Setup ───────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent
CFG = get_config()

MODE        = CFG["mode"]
ORDER_TYPE  = CFG["trading"]["order_type"]
SLIPPAGE    = CFG["trading"]["limit_slippage_pct"]
log         = logging.getLogger("core.order_executor")


def _utc_now():
    return datetime.now(timezone.utc)


def _symbols_match(left: str, right: str) -> bool:
    return ac.normalize_symbol(left) == ac.normalize_symbol(right)


def _order_value(order, name: str, default=None):
    if isinstance(order, dict):
        return order.get(name, default)
    return getattr(order, name, default)


def _order_status(order) -> str | None:
    status = _order_value(order, "status")
    if status is None:
        return None
    return str(getattr(status, "value", status)).lower()


def _broker_order_id(order) -> str:
    return str(_order_value(order, "id", _order_value(order, "order_id", "")) or "")


def _current_run_id() -> str:
    return os.getenv("HAWKSTRADE_RUN_ID") or "manual"


def _min_trade_value_usd() -> float:
    try:
        return max(0.0, float(CFG.get("trading", {}).get("min_trade_value_usd", 0) or 0))
    except (TypeError, ValueError):
        return 0.0


def _finite_positive(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _finite_nonnegative(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _sample_size_scale_input(sizing, check: dict) -> float:
    if sizing.source == "pre_trade":
        return _finite_positive(check.get("base_cap_qty")) or sizing.capped_qty
    return sizing.requested_qty


def _expected_slippage_bps(symbol: str, asset_class: str, side: str, price: float, qty: float) -> float:
    cfg = CFG.get("slippage_model", {}) or {}
    if not bool(cfg.get("enabled", False)):
        return float(SLIPPAGE) * 10000.0
    estimate = estimate_slippage_bps(
        symbol=symbol,
        asset_class=asset_class,
        side=side,
        order_size_usd=float(price) * float(qty),
        cfg=cfg,
        time_of_day=_utc_now(),
    )
    high_cost = _finite_positive(cfg.get("high_cost_warning_bps")) or 50.0
    if estimate > high_cost:
        log.warning(
            "High expected slippage for %s %s: %.2f bps (qty=%s price=%.4f)",
            side,
            symbol,
            estimate,
            qty,
            price,
        )
    return estimate


def _marketable_limit_price(symbol: str, asset_class: str, side: str, price: float, qty: float) -> tuple[float, float]:
    expected_bps = _expected_slippage_bps(symbol, asset_class, side, price, qty)
    offset = max(0.0, expected_bps) / 10000.0
    if side == "sell":
        return price * (1 - offset), expected_bps
    return price * (1 + offset), expected_bps


def _execution_policy_poll_interval() -> float:
    raw = (CFG.get("execution_policy", {}) or {}).get("poll_interval_seconds", 5.0)
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        return 5.0
    return max(0.0, parsed) if math.isfinite(parsed) else 5.0


def _latency_budget_ms(asset_class: str) -> float:
    budget_cfg = CFG.get("latency_budget", {}) or {}
    key = "crypto_decision_to_submit_ms" if asset_class == "crypto" else "stock_decision_to_submit_ms"
    try:
        parsed = float(budget_cfg.get(key, 2000 if asset_class == "crypto" else 500))
    except (TypeError, ValueError):
        parsed = 2000.0 if asset_class == "crypto" else 500.0
    return parsed if math.isfinite(parsed) and parsed > 0 else (2000.0 if asset_class == "crypto" else 500.0)


def _latency_payload(
    *,
    signal_received: float,
    governor_evaluated: float,
    order_submitted: float,
    broker_ack: float,
) -> dict[str, float]:
    return {
        "governor": round(max(0.0, governor_evaluated - signal_received) * 1000.0, 3),
        "submit": round(max(0.0, order_submitted - governor_evaluated) * 1000.0, 3),
        "ack": round(max(0.0, broker_ack - order_submitted) * 1000.0, 3),
        "total": round(max(0.0, broker_ack - signal_received) * 1000.0, 3),
    }


def _latency_json(payload: dict[str, float]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _warn_latency_breach(symbol: str, asset_class: str, payload: dict[str, float]) -> None:
    budget_ms = _latency_budget_ms(asset_class)
    total_ms = float(payload.get("total", 0.0) or 0.0)
    if total_ms > budget_ms * 5:
        log.warning(
            "LATENCY_BUDGET_BREACH symbol=%s asset_class=%s total_ms=%.3f budget_ms=%.3f",
            symbol,
            asset_class,
            total_ms,
            budget_ms,
        )


def _order_matches_id(order, order_id: str) -> bool:
    if not order_id:
        return False
    return _broker_order_id(order) == order_id


def _refresh_order_status(order, order_id: str):
    for provider in (ac.get_open_orders, ac.get_closed_orders):
        try:
            for candidate in provider() or []:
                if _order_matches_id(candidate, order_id):
                    return candidate
        except Exception as exc:
            log.debug("Could not refresh order status for %s: %s", order_id, exc)
    return order


def _cancel_order_safely(order_id: str) -> None:
    if not order_id:
        return
    try:
        ac.cancel_order(order_id)
    except Exception as exc:
        log.warning("Could not cancel passive entry leg %s: %s", order_id, exc)


def _wait_for_entry_leg(order, requested_qty: float, timeout_seconds: float):
    if timeout_seconds <= 0:
        return order
    order_id = _broker_order_id(order)
    deadline = time.monotonic() + timeout_seconds
    poll_interval = _execution_policy_poll_interval()
    current = order
    while time.monotonic() < deadline:
        filled_qty = _entry_fill_qty(current, requested_qty)
        if filled_qty >= requested_qty or (_order_status(current) or "") == "filled":
            return current
        if poll_interval > 0:
            time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))
        current = _refresh_order_status(current, order_id)
    return _refresh_order_status(current, order_id)


def _submit_entry_leg(
    *,
    symbol: str,
    strategy: str,
    asset_class: str,
    leg: OrderLeg,
    parent_intent_id: str,
    execution_policy: str,
):
    intent = _create_order_intent(
        symbol,
        "buy",
        strategy,
        asset_class,
        leg.qty,
        limit_price=leg.limit_price,
        parent_intent_id=parent_intent_id,
        leg_number=leg.leg_number,
        execution_policy=execution_policy,
    )
    try:
        if leg.order_type == "market":
            order = ac.place_market_order(
                symbol,
                leg.qty,
                "buy",
                strategy=strategy,
                asset_class=asset_class,
                client_order_id=intent["client_order_id"] if intent else None,
            )
        else:
            order = ac.place_limit_order(
                symbol,
                leg.qty,
                "buy",
                leg.limit_price,
                strategy=strategy,
                asset_class=asset_class,
                client_order_id=intent["client_order_id"] if intent else None,
            )
    except Exception as exc:
        _mark_order_intent_failed(intent, exc)
        raise
    _mark_order_intent_submitted(intent, order)
    return order


def _combined_entry_order(orders: list, requested_qty: float) -> dict:
    order_ids = [_broker_order_id(order) for order in orders if _broker_order_id(order)]
    filled_parts = []
    for order in orders:
        filled_qty = _order_filled_qty(order)
        if filled_qty <= 0:
            continue
        filled_parts.append((filled_qty, _order_filled_avg_price(order, 0.0)))
    filled_qty = sum(part[0] for part in filled_parts)
    if filled_qty > 0:
        avg_price = sum(qty * price for qty, price in filled_parts) / filled_qty
    else:
        avg_price = ""
    statuses = [(_order_status(order) or "") for order in orders]
    if filled_qty >= requested_qty or any(status == "filled" for status in statuses):
        status = "filled" if filled_qty >= requested_qty else "partially_filled"
    elif filled_qty > 0:
        status = "partially_filled"
    else:
        status = statuses[-1] if statuses else "submitted"
    return {
        "id": ",".join(order_ids),
        "order_id": ",".join(order_ids),
        "status": status,
        "filled_qty": filled_qty,
        "filled_avg_price": avg_price,
    }


def _execute_entry_plan(
    *,
    symbol: str,
    strategy: str,
    asset_class: str,
    plan: ExecutionPlan,
) -> dict:
    if plan.policy_name != TWO_LEG_POLICY:
        return _submit_entry_leg(
            symbol=symbol,
            strategy=strategy,
            asset_class=asset_class,
            leg=plan.legs[0],
            parent_intent_id=plan.parent_intent_id,
            execution_policy=plan.policy_name,
        )

    submitted_orders = []
    passive = plan.legs[0]
    passive_order = _submit_entry_leg(
        symbol=symbol,
        strategy=strategy,
        asset_class=asset_class,
        leg=passive,
        parent_intent_id=plan.parent_intent_id,
        execution_policy=plan.policy_name,
    )
    passive_order = _wait_for_entry_leg(passive_order, passive.qty, passive.timeout_seconds)
    submitted_orders.append(passive_order)
    passive_filled_qty = _entry_fill_qty(passive_order, passive.qty)
    requested_qty = sum(leg.qty for leg in plan.legs)
    residual_qty = max(0.0, requested_qty - passive_filled_qty)
    if residual_qty <= 1e-9:
        return _combined_entry_order(submitted_orders, requested_qty)

    _cancel_order_safely(_broker_order_id(passive_order))
    aggressive_template = plan.legs[1]
    aggressive = OrderLeg(
        leg_number=aggressive_template.leg_number,
        role=aggressive_template.role,
        qty=residual_qty,
        order_type=aggressive_template.order_type,
        limit_price=aggressive_template.limit_price,
        timeout_seconds=0.0,
    )
    aggressive_order = _submit_entry_leg(
        symbol=symbol,
        strategy=strategy,
        asset_class=asset_class,
        leg=aggressive,
        parent_intent_id=plan.parent_intent_id,
        execution_policy=plan.policy_name,
    )
    submitted_orders.append(aggressive_order)
    return _combined_entry_order(submitted_orders, requested_qty)


def _create_order_intent(
    symbol: str,
    side: str,
    strategy: str,
    asset_class: str,
    qty,
    limit_price=None,
    *,
    parent_intent_id: str = "",
    leg_number: int | str = "",
    execution_policy: str = "",
) -> dict | None:
    if MODE == "backtest":
        return None
    intent, created = order_intents.get_or_create_order_intent(
        run_id=_current_run_id(),
        symbol=symbol,
        side=side,
        strategy=strategy,
        asset_class=asset_class,
        qty=qty,
        limit_price=limit_price,
        parent_intent_id=parent_intent_id,
        leg_number=leg_number,
        execution_policy=execution_policy,
    )
    action = "created" if created else "reused"
    log.info(f"Order intent {action}: {intent['client_order_id']} | {side} {symbol} | strategy={strategy}")
    return intent


def _mark_order_intent_submitted(intent: dict | None, order) -> None:
    if not intent:
        return
    status = _order_status(order) or "submitted"
    order_intents.update_order_intent(
        intent["client_order_id"],
        status=status,
        broker_order_id=_broker_order_id(order),
    )


def _mark_order_intent_failed(intent: dict | None, exc: Exception) -> None:
    if not intent:
        return
    order_intents.update_order_intent(
        intent["client_order_id"],
        status="submit_failed",
        error=f"{type(exc).__name__}: {exc}",
    )


def _order_filled_qty(order) -> float:
    raw_qty = _order_value(order, "filled_qty", None)
    if raw_qty in (None, ""):
        return 0.0
    try:
        return abs(float(str(raw_qty)))
    except (TypeError, ValueError):
        return 0.0


def _order_filled_avg_price(order, fallback: float) -> float:
    raw_price = _order_value(order, "filled_avg_price", None)
    if raw_price in (None, ""):
        return fallback
    try:
        filled_avg_price = float(str(raw_price))
    except (TypeError, ValueError):
        return fallback
    return filled_avg_price if filled_avg_price > 0 else fallback


def _entry_fill_qty(order, requested_qty: float) -> float:
    """Return the broker-confirmed entry quantity that should count as exposure."""
    status = _order_status(order)
    filled_qty = _order_filled_qty(order)
    if status is None:
        return requested_qty
    if status == "filled":
        return filled_qty or requested_qty
    if filled_qty >= requested_qty:
        return filled_qty
    if status == "partially_filled" or filled_qty > 0:
        return filled_qty
    return 0.0


def _entry_log_status(order, requested_qty: float, filled_qty: float) -> str:
    status = _order_status(order)
    if status is None or status == "filled" or filled_qty >= requested_qty:
        return "open"
    if status == "partially_filled" or filled_qty > 0:
        return "partially_filled"
    return "submitted"


def _exit_fill_qty(order, requested_qty: float) -> float:
    """
    Return the quantity that is safe to remove from trades.csv.

    Alpaca limit exits can be accepted but not filled immediately. In that case
    the broker position still exists and the local trade log must stay open.
    """
    status = _order_status(order)
    filled_qty = _order_filled_qty(order)
    if status is None:
        return requested_qty
    if status == "filled":
        return filled_qty or requested_qty
    if status == "partially_filled":
        return filled_qty
    return 0.0


def _exit_log_status(order, requested_qty: float, filled_qty: float) -> str:
    status = _order_status(order)
    if status is None or status == "filled":
        return "closed"
    if status == "partially_filled" or (0 < filled_qty < requested_qty):
        return "partially_filled"
    return "submitted"


def _submitted_order_is_transient(order) -> bool:
    return (_order_status(order) or "") in {
        "accepted",
        "accepted_for_bidding",
        "new",
        "pending_new",
        "pending_replace",
        "pending_review",
    }


def _entry_failure_result(symbol: str, strategy: str, asset_class: str, exc: Exception) -> dict:
    return {
        "timestamp": _utc_now().isoformat(),
        "mode": MODE,
        "symbol": symbol,
        "strategy": strategy,
        "asset_class": asset_class,
        "side": "buy",
        "qty": "",
        "entry_price": "",
        "order_id": "",
        "status": "entry_failed",
        "error_type": type(exc).__name__,
        "error": str(exc),
    }


def _entry_governor_block_result(
    symbol: str,
    strategy: str,
    asset_class: str,
    qty,
    price,
    order_type: str,
    decision: GovernorDecision,
    limit_price=None,
) -> dict:
    result = {
        "timestamp": _utc_now().isoformat(),
        "mode": MODE,
        "symbol": symbol,
        "strategy": strategy,
        "asset_class": asset_class,
        "side": "buy",
        "qty": qty,
        "entry_price": price,
        "order_id": "",
        "status": "order_governor_blocked",
        "order_type": order_type,
        "governor_code": decision.code,
        "error_type": "OrderGovernorBlocked",
        "error": decision.reason,
    }
    if limit_price is not None:
        result["limit_price"] = limit_price
    return result


def _entry_readiness_block_result(
    symbol: str,
    strategy: str,
    asset_class: str,
    decision: ReadinessDecision,
) -> dict:
    return {
        "timestamp": _utc_now().isoformat(),
        "mode": MODE,
        "symbol": symbol,
        "strategy": strategy,
        "asset_class": asset_class,
        "side": "buy",
        "qty": "",
        "entry_price": "",
        "order_id": "",
        "status": "strategy_readiness_blocked",
        "readiness_code": decision.code,
        "error_type": "StrategyReadinessBlocked",
        "error": decision.reason,
    }


def _exit_governor_block_result(
    *,
    symbol: str,
    strategy: str,
    asset_class: str,
    reason: str,
    qty,
    entry_price,
    current_price,
    pnl_pct,
    order_type: str,
    decision: GovernorDecision,
    limit_price=None,
) -> dict:
    if decision.code == "duplicate_pending_exit":
        status = "pending_exit"
    elif decision.code in {
        "account_lookup_failed",
        "broker_orders_lookup_failed",
        "missing_account_state",
        "missing_broker_orders",
    }:
        status = "pending_exit_check_failed"
    else:
        status = "order_governor_blocked"
    result = {
        "timestamp": _utc_now().isoformat(),
        "mode": MODE,
        "symbol": symbol,
        "strategy": strategy,
        "asset_class": asset_class,
        "side": "sell",
        "qty": qty,
        "entry_price": entry_price,
        "exit_price": current_price,
        "pnl_pct": round(pnl_pct, 6) if pnl_pct not in ("", None) else "",
        "exit_reason": reason,
        "order_id": "",
        "status": status,
        "order_type": order_type,
        "governor_code": decision.code,
        "error_type": "OrderGovernorBlocked",
        "error": decision.reason,
    }
    if limit_price is not None:
        result["limit_price"] = limit_price
    return result


def _evaluate_order_governor(order_intent: OrderIntent) -> GovernorDecision:
    if MODE == "backtest":
        return GovernorDecision.allow("Order governor skipped in backtest mode")
    governor = OrderGovernor.from_config(CFG)
    if not governor.enabled:
        return GovernorDecision.allow("Order governor disabled")
    try:
        account_state = ac.get_account()
    except Exception as exc:
        return GovernorDecision.block(
            f"Could not fetch account state for order governor: {exc}",
            code="account_lookup_failed",
        )
    try:
        broker_orders = ac.get_open_orders()
    except Exception as exc:
        return GovernorDecision.block(
            f"Could not fetch open broker orders for order governor: {exc}",
            code="broker_orders_lookup_failed",
        )
    return governor.evaluate(order_intent, account_state, broker_orders)


def _log_governor_decision(symbol: str, side: str, decision: GovernorDecision) -> None:
    if decision.allowed:
        if decision.status == "warn":
            log.warning(
                "Order governor allowed %s %s with warnings: %s",
                side,
                symbol,
                "; ".join(decision.warnings) or decision.reason,
            )
        return
    log.warning(
        "Order governor blocked %s %s: code=%s reason=%s context=%s",
        side,
        symbol,
        decision.code,
        decision.reason,
        dict(decision.context or {}),
    )


def _effective_entry_stop_loss(entry_price: float, atr_stop_price: float | None) -> float:
    global_sl = rm.stop_loss_price(entry_price)
    if atr_stop_price is None:
        return global_sl
    try:
        custom_stop = float(atr_stop_price)
    except (TypeError, ValueError):
        return global_sl
    if not math.isfinite(custom_stop) or custom_stop <= 0 or custom_stop >= entry_price:
        return global_sl

    return custom_stop if custom_stop < global_sl else global_sl


def _closed_trades_for_strategy(strategy: str) -> list[dict]:
    try:
        return get_closed_trades(strategy=strategy)
    except TypeError as exc:
        if "unexpected keyword argument" not in str(exc) or "strategy" not in str(exc):
            raise
        return [row for row in get_closed_trades() if row.get("strategy") == strategy]


def _exit_failure_result(
    symbol: str,
    strategy: str,
    asset_class: str,
    reason: str,
    status: str,
    error_type: str,
    error: str,
    qty="",
    entry_price="",
    current_price="",
) -> dict:
    result = {
        "timestamp": _utc_now().isoformat(),
        "mode": MODE,
        "symbol": symbol,
        "strategy": strategy,
        "asset_class": asset_class,
        "side": "sell",
        "qty": qty,
        "entry_price": entry_price,
        "exit_price": current_price,
        "pnl_pct": "",
        "exit_reason": reason,
        "order_id": "",
        "status": status,
        "error_type": error_type,
        "error": error,
    }
    if entry_price not in ("", None) and current_price not in ("", None):
        try:
            entry = float(entry_price)
            current = float(current_price)
            if entry > 0 and current > 0:
                result["pnl_pct"] = round((current - entry) / entry, 6)
        except (TypeError, ValueError):
            pass
    return result


# ── Entry Logic ─────────────────────────────────────────────────────────────

def enter_position(
    symbol: str,
    strategy: str,
    asset_class: str = "stock",
    dry_run: bool = False,
    suggested_qty: Optional[float] = None,
    atr_stop_price: Optional[float] = None,
    closed_trades_count: Optional[int] = None,
) -> Optional[dict]:
    """
    Open a new position.
      1. Check risk rules (daily loss, max positions, size)
      2. Calculate qty (ATR-risk qty > Kelly > portfolio-pct, whichever applies)
      3. Place order (limit or market)
      4. Log the trade

    suggested_qty: ATR-risk-based quantity from the strategy signal; takes
        priority over Kelly when provided and positive.
    atr_stop_price: volatility-adjusted stop; written to the trade log so the
        live risk check can use it as the effective stop price.
    closed_trades_count: optional precomputed strategy sample size for callers
        that may submit multiple entry attempts in one scan.
    """
    try:
        t_signal_received = time.monotonic()
        readiness = evaluate_strategy_live_readiness(strategy, mode=MODE, cfg=CFG)
        if not readiness.allowed:
            log.warning(
                "Strategy live readiness blocked %s entry for %s: %s context=%s",
                strategy,
                symbol,
                readiness.reason,
                dict(readiness.context or {}),
            )
            return _entry_readiness_block_result(symbol, strategy, asset_class, readiness)

        # Get latest price
        if asset_class == "crypto":
            price = ac.get_crypto_latest_price(symbol)
        else:
            price = ac.get_stock_latest_price(symbol)

        if price <= 0:
            log.warning(f"Invalid price for {symbol}: {price}. Skipping entry.")
            return None

        # Risk Check (asset-class-aware for crypto reservation/cap enforcement)
        check = rm.pre_trade_check(price, symbol, asset_class=asset_class)
        if not check["approved"]:
            log.info(f"Entry blocked for {symbol}: {check['reason']}")
            return None

        sizing = construct_entry_size(
            price=price,
            strategy=strategy,
            pre_trade_qty=check["qty"],
            suggested_qty=suggested_qty,
            kelly_sizer=rm.kelly_position_size,
            capper=rm.cap_position_qty,
        )
        tier = effective_risk_for(
            strategy,
            CFG,
            closed_trades=(
                int(closed_trades_count)
                if closed_trades_count is not None
                else len(_closed_trades_for_strategy(strategy))
            ),
        )
        base_position_cap_pct = float(CFG.get("trading", {}).get("max_position_pct", 0.08) or 0.08)
        base_cap_qty = _finite_positive(check.get("base_cap_qty")) or check["qty"]
        cash_qty = _finite_nonnegative(check.get("cash_qty"))
        scale_input_qty = _sample_size_scale_input(sizing, check)
        capped_qty = scale_quantity(
            scale_input_qty,
            tier,
            base_position_cap_pct=base_position_cap_pct,
            base_cap_qty=base_cap_qty,
            cash_qty=cash_qty,
        )
        if not math.isfinite(capped_qty) or capped_qty <= 0:
            log.info(f"Entry blocked for {symbol}: capped quantity is zero.")
            return None
        min_trade_value = _min_trade_value_usd()
        scaled_notional = capped_qty * price
        if min_trade_value > 0 and scaled_notional + 1e-9 < min_trade_value:
            log.info(
                "Entry blocked for %s: scaled notional $%.2f is below min trade value $%.2f "
                "(qty=%s price=%.4f).",
                symbol,
                scaled_notional,
                min_trade_value,
                capped_qty,
                price,
            )
            return None
        if sizing.capped:
            log.info(
                "Entry max-position cap for %s: requested=%s base_capped=%s",
                symbol,
                sizing.requested_qty,
                sizing.capped_qty,
            )
        if tier.risk_multiplier < 1.0 or tier.position_cap_pct < base_position_cap_pct or tier.override:
            log.info(
                "Sample-size risk tier for %s/%s: tier=%s closed_trades=%s "
                "risk_multiplier=%.2f position_cap=%.2f%% base_qty=%s scaled_qty=%s",
                strategy,
                symbol,
                tier.name,
                tier.closed_trades,
                tier.risk_multiplier,
                tier.position_cap_pct * 100,
                scale_input_qty,
                capped_qty,
            )
        qty = capped_qty
        order_type = "market" if ORDER_TYPE == "market" else "limit"
        expected_slippage = _expected_slippage_bps(symbol, asset_class, "buy", price, qty)
        policy_plan = build_entry_execution_plan(
            symbol=symbol,
            strategy=strategy,
            asset_class=asset_class,
            qty=qty,
            side="buy",
            price=price,
            order_type=order_type,
            expected_slippage_bps=expected_slippage,
            cfg=CFG,
            run_id=_current_run_id(),
        )
        limit_px = policy_plan.legs[-1].limit_price if order_type == "limit" else None

        if dry_run:
            log.info(f"DRY RUN: would buy {qty} {symbol} @ {price}")
            return {
                "symbol": symbol,
                "status": "dry_run",
                "expected_slippage_bps": round(expected_slippage, 4),
                "execution_policy": policy_plan.policy_name,
            }

        governor_decision = _evaluate_order_governor(OrderIntent(
            symbol=symbol,
            side="buy",
            qty=qty,
            order_type=order_type,
            asset_class=asset_class,
            strategy=strategy,
            price=price,
            limit_price=limit_px,
            parent_intent_id=policy_plan.parent_intent_id,
        ))
        t_governor_evaluated = time.monotonic()
        _log_governor_decision(symbol, "buy", governor_decision)
        if not governor_decision.allowed:
            return _entry_governor_block_result(
                symbol,
                strategy,
                asset_class,
                qty,
                price,
                order_type,
                governor_decision,
                limit_price=limit_px,
            )

        # Place Order
        t_order_submitted = time.monotonic()
        order = _execute_entry_plan(
            symbol=symbol,
            strategy=strategy,
            asset_class=asset_class,
            plan=policy_plan,
        )
        t_broker_ack = time.monotonic()
        latency = _latency_payload(
            signal_received=t_signal_received,
            governor_evaluated=t_governor_evaluated,
            order_submitted=t_order_submitted,
            broker_ack=t_broker_ack,
        )
        _warn_latency_breach(symbol, asset_class, latency)

        # Capture details for logging
        order_id = str(order.id) if hasattr(order, "id") else str(order.get("order_id"))
        filled_qty = _entry_fill_qty(order, qty)
        action_status = _entry_log_status(order, qty, filled_qty)
        logged_qty = filled_qty if filled_qty > 0 else qty
        entry_price = _order_filled_avg_price(order, price) if filled_qty > 0 else price
        sl = _effective_entry_stop_loss(entry_price, atr_stop_price)
        tp = rm.take_profit_price(entry_price)
        trade = {
            "timestamp":        _utc_now().isoformat(),
            "mode":             MODE,
            "symbol":           symbol,
            "strategy":         strategy,
            "asset_class":      asset_class,
            "side":             "buy",
            "qty":              logged_qty,
            "entry_price":      entry_price,
            "stop_loss":        sl,
            "take_profit":      tp,
            "high_water_price": entry_price,
            "risk_tier":        tier.audit_label,
            "order_id":         order_id,
            "status":           action_status,
            "order_type":        order_type,
            "limit_price":       limit_px if limit_px is not None else "",
            "decision_price":    price,
            "arrival_price":     price,
            "expected_slippage_bps": round(expected_slippage, 4),
            "realised_slippage_bps": (
                round(realised_slippage_bps(side="buy", decision_price=price, fill_price=entry_price), 4)
                if filled_qty > 0 else ""
            ),
            "execution_policy":  policy_plan.policy_name,
            "latency_ms":        _latency_json(latency),
        }
        log_trade(trade)
        if action_status == "open":
            log.info(f"ENTERED {symbol} | strategy={strategy} | qty={logged_qty} | price={entry_price}")
            try:
                from core.broker_stops import sync_broker_stops

                sync_broker_stops(
                    positions=[
                        {
                            "symbol": symbol,
                            "qty": logged_qty,
                            "asset_class": asset_class,
                        }
                    ],
                    open_trades=[trade],
                )
            except Exception as exc:
                log.warning("Broker protective stop sync failed after entry for %s: %s", symbol, exc)
        elif action_status == "partially_filled":
            log.warning(
                f"Entry order partially filled for {symbol}; logged filled exposure only | "
                f"strategy={strategy} | filled_qty={filled_qty} requested_qty={qty}"
            )
        else:
            entry_log = log.info if _submitted_order_is_transient(order) else log.warning
            entry_log(
                f"Entry order submitted for {symbol} but not filled yet; "
                f"trade log status=submitted | strategy={strategy} | requested_qty={qty}"
            )
        return trade

    except Exception as e:
        log.error(f"Failed to enter {symbol}: {e}", exc_info=True)
        return _entry_failure_result(symbol, strategy, asset_class, e)


def exit_position(
    symbol: str,
    reason: str,
    asset_class: str = "stock",
    dry_run: bool = False,
    open_trades_callback=None,
    force_market: bool = False,
    limit_price: float | None = None,
) -> Optional[dict]:
    """
    Close an open position fully.
      1. Check position exists
      2. Place sell order
      3. Log the trade

    force_market: use a market sell even when the default order_type is limit;
        intended for liquidation exits where fill certainty matters most.
    """
    strategy = "unknown"
    trade_symbol = symbol
    qty = ""
    entry_price = ""
    current_price = ""
    try:
        t_signal_received = time.monotonic()
        position = ac.get_position(symbol)
        if not position:
            log.info(f"No open position for {symbol}, skipping exit.")
            return None

        qty = float(position.qty)
        if qty <= 0:
            log.error(
                f"exit_position called on non-long position for {symbol} (qty={qty}); "
                "HawksTrade is long-only. Skipping exit."
            )
            return None

        if asset_class == "crypto":
            current_price = float(ac.get_crypto_latest_price(symbol))
        else:
            current_price = float(ac.get_stock_latest_price(symbol))
        if current_price <= 0:
            log.error(f"Invalid current price for exit {symbol}: {current_price}. Skipping exit.")
            return _exit_failure_result(
                symbol=symbol,
                strategy=strategy,
                asset_class=asset_class,
                reason=reason,
                status="invalid_exit_price",
                error_type="InvalidExitPrice",
                error=f"Invalid current price: {current_price}",
                qty=qty,
                entry_price=getattr(position, "avg_entry_price", ""),
                current_price=current_price,
            )

        entry_price = float(position.avg_entry_price)
        if entry_price <= 0:
            log.error(f"Invalid entry price for exit {symbol}: {entry_price}. Skipping exit.")
            return _exit_failure_result(
                symbol=symbol,
                strategy=strategy,
                asset_class=asset_class,
                reason=reason,
                status="invalid_entry_price",
                error_type="InvalidEntryPrice",
                error=f"Invalid entry price: {entry_price}",
                qty=qty,
                entry_price=entry_price,
                current_price=current_price,
            )
        pnl_pct     = (current_price - entry_price) / entry_price

        # Retrieve strategy and canonical symbol from local open trades if possible.
        if open_trades_callback:
            open_trades = open_trades_callback()
        else:
            from tracking.trade_log import get_open_trades
            open_trades = get_open_trades()

        matched_open_trade = None
        for t in reversed(open_trades):
            if _symbols_match(t["symbol"], symbol):
                matched_open_trade = t
                strategy = t.get("strategy", "unknown")
                trade_symbol = t.get("symbol") or symbol
                break
        entry_risk_tier = matched_open_trade.get("risk_tier", "") if matched_open_trade else ""

        order_symbol = trade_symbol if asset_class == "crypto" else symbol

        if limit_price is not None:
            try:
                limit_price = float(limit_price)
            except (TypeError, ValueError):
                limit_price = 0.0
            if limit_price <= 0:
                log.error(f"Invalid limit price for exit {symbol}: {limit_price}. Skipping exit.")
                return _exit_failure_result(
                    symbol=symbol,
                    strategy=strategy,
                    asset_class=asset_class,
                    reason=reason,
                    status="invalid_limit_price",
                    error_type="InvalidLimitPrice",
                    error=f"Invalid limit price: {limit_price}",
                    qty=qty,
                    entry_price=entry_price,
                    current_price=current_price,
                )

        order_type = "market" if force_market or (limit_price is None and ORDER_TYPE == "market") else "limit"
        expected_slippage = _expected_slippage_bps(order_symbol, asset_class, "sell", current_price, qty)

        if dry_run:
            trade = {
                "timestamp":     _utc_now().isoformat(),
                "mode":          MODE,
                "symbol":        trade_symbol,
                "strategy":      strategy,
                "asset_class":   asset_class,
                "side":          "sell",
                "qty":           qty,
                "entry_price":   entry_price,
                "exit_price":    current_price,
                "pnl_pct":       round(pnl_pct, 6),
                "exit_reason":   reason,
                "order_id":      "DRY-RUN",
                "status":        "dry_run",
                "order_type":     order_type,
                "risk_tier":      entry_risk_tier,
                "decision_price": current_price,
                "arrival_price":  current_price,
                "expected_slippage_bps": round(expected_slippage, 4),
                "realised_slippage_bps": "",
                "execution_policy": SINGLE_LEG_POLICY,
            }
            if limit_price is not None:
                trade["limit_price"] = limit_price
            log.info(
                f"DRY RUN: would {order_type}-exit {trade_symbol} | strategy={strategy} | reason={reason} | "
                f"entry={entry_price} exit={current_price} pnl={pnl_pct:.2%}"
            )
            return trade

        limit_px = limit_price if order_type == "limit" else None
        if order_type == "limit" and limit_px is None:
            limit_px, expected_slippage = _marketable_limit_price(order_symbol, asset_class, "sell", current_price, qty)
        governor_decision = _evaluate_order_governor(OrderIntent(
            symbol=order_symbol,
            side="sell",
            qty=qty,
            order_type=order_type,
            asset_class=asset_class,
            strategy=strategy,
            price=current_price,
            limit_price=limit_px,
            position_qty=qty,
        ))
        t_governor_evaluated = time.monotonic()
        _log_governor_decision(order_symbol, "sell", governor_decision)
        if not governor_decision.allowed:
            return _exit_governor_block_result(
                symbol=trade_symbol,
                strategy=strategy,
                asset_class=asset_class,
                reason=reason,
                qty=qty,
                entry_price=entry_price,
                current_price=current_price,
                pnl_pct=pnl_pct,
                order_type=order_type,
                decision=governor_decision,
                limit_price=limit_price,
            )

        t_order_submitted = time.monotonic()
        if order_type == "market":
            intent = _create_order_intent(order_symbol, "sell", strategy, asset_class, qty)
            try:
                order = ac.place_market_order(
                    order_symbol,
                    qty,
                    "sell",
                    strategy=strategy,
                    asset_class=asset_class,
                    client_order_id=intent["client_order_id"] if intent else None,
                )
            except Exception as e:
                _mark_order_intent_failed(intent, e)
                raise
        else:
            intent = _create_order_intent(order_symbol, "sell", strategy, asset_class, qty, limit_price=limit_px)
            try:
                order = ac.place_limit_order(
                    order_symbol,
                    qty,
                    "sell",
                    limit_px,
                    strategy=strategy,
                    asset_class=asset_class,
                    client_order_id=intent["client_order_id"] if intent else None,
                )
            except Exception as e:
                _mark_order_intent_failed(intent, e)
                raise
        _mark_order_intent_submitted(intent, order)
        t_broker_ack = time.monotonic()
        latency = _latency_payload(
            signal_received=t_signal_received,
            governor_evaluated=t_governor_evaluated,
            order_submitted=t_order_submitted,
            broker_ack=t_broker_ack,
        )
        _warn_latency_breach(trade_symbol, asset_class, latency)

        order_id = str(order.id) if hasattr(order, "id") else str(order.get("order_id"))
        filled_qty = _exit_fill_qty(order, qty)
        action_status = _exit_log_status(order, qty, filled_qty)
        logged_qty = filled_qty if filled_qty > 0 else qty
        exit_price = _order_filled_avg_price(order, current_price) if filled_qty > 0 else current_price
        pnl_pct = (exit_price - entry_price) / entry_price
        trade = {
            "timestamp":     _utc_now().isoformat(),
            "mode":          MODE,
            "symbol":        trade_symbol,
            "strategy":      strategy,
            "asset_class":   asset_class,
            "side":          "sell",
            "qty":           logged_qty,
            "entry_price":   entry_price,
            "exit_price":    exit_price,
            "pnl_pct":       round(pnl_pct, 6),
            "exit_reason":   reason,
            "order_id":      order_id,
            "status":        action_status,
            "order_type":     order_type,
            "risk_tier":      entry_risk_tier,
            "limit_price":     limit_px if limit_px is not None else "",
            "decision_price":  current_price,
            "arrival_price":   current_price,
            "expected_slippage_bps": round(expected_slippage, 4),
            "realised_slippage_bps": (
                round(realised_slippage_bps(side="sell", decision_price=current_price, fill_price=exit_price), 4)
                if filled_qty > 0 else ""
            ),
            "execution_policy":  SINGLE_LEG_POLICY,
            "latency_ms":     _latency_json(latency),
        }
        if limit_price is not None:
            trade["limit_price"] = limit_price
        log_trade(trade)
        if filled_qty > 0:
            mark_trade_closed(trade_symbol, exit_price, pnl_pct, reason, closed_qty=filled_qty)
            log.info(
                f"EXITED {trade_symbol} | reason={reason} | qty={filled_qty} | "
                f"entry={entry_price} exit={exit_price} pnl={pnl_pct:.2%}"
            )
        else:
            exit_log = log.info if _submitted_order_is_transient(order) else log.warning
            exit_log(
                f"Exit order submitted for {trade_symbol} but not filled yet; "
                f"leaving trade log open | reason={reason} | status={_order_status(order)}"
            )
        return trade

    except Exception as e:
        log.error(f"Failed to exit {symbol}: {e}", exc_info=True)
        return _exit_failure_result(
            symbol=trade_symbol,
            strategy=strategy,
            asset_class=asset_class,
            reason=reason,
            status="exit_failed",
            error_type=type(e).__name__,
            error=str(e),
            qty=qty,
            entry_price=entry_price,
            current_price=current_price,
        )
