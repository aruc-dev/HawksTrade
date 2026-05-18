"""Configurable entry execution policies."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Mapping, Sequence


SINGLE_LEG_POLICY = "single_leg_marketable_limit"
TWO_LEG_POLICY = "two_leg_passive_aggressive"


@dataclass(frozen=True)
class OrderLeg:
    leg_number: int
    role: str
    qty: float
    order_type: str
    limit_price: float | None
    timeout_seconds: float = 0.0


@dataclass(frozen=True)
class ExecutionPlan:
    policy_name: str
    bucket: str
    parent_intent_id: str
    legs: tuple[OrderLeg, ...]


def _cfg_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _cfg_float(value, default: float) -> float:
    if value in (None, ""):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _policy_cfg(cfg: Mapping | None) -> Mapping:
    raw = (cfg or {}).get("execution_policy", {}) if isinstance(cfg, Mapping) else {}
    return raw if isinstance(raw, Mapping) else {}


def _asset_cfg(policy_cfg: Mapping, asset_class: str) -> Mapping:
    per_asset = policy_cfg.get("per_asset_class", {})
    if not isinstance(per_asset, Mapping):
        return {}
    raw = per_asset.get(asset_class) or per_asset.get("stock") or {}
    return raw if isinstance(raw, Mapping) else {}


def _offset_price(price: float, side: str, offset_bps: float) -> float:
    offset = offset_bps / 10000.0
    if side == "sell":
        return price * (1.0 - offset)
    return price * (1.0 + offset)


def _bucket_value(key: str) -> float:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    return int(digest, 16) / float(16**12 - 1)


def choose_policy_bucket(
    *,
    run_id: str,
    symbol: str,
    strategy: str,
    asset_class: str,
    policy_cfg: Mapping,
) -> str:
    """Return deterministic A/B bucket for a logical entry intent."""

    ab_cfg = policy_cfg.get("ab_test", {})
    if not isinstance(ab_cfg, Mapping) or not _cfg_bool(ab_cfg.get("enabled"), False):
        return "policy"
    fraction = min(1.0, max(0.0, _cfg_float(ab_cfg.get("fraction"), 0.5)))
    key = "|".join([run_id, symbol.upper(), strategy, asset_class])
    return "policy" if _bucket_value(key) < fraction else "control"


def build_entry_execution_plan(
    *,
    symbol: str,
    strategy: str,
    asset_class: str,
    qty: float,
    side: str,
    price: float,
    order_type: str,
    expected_slippage_bps: float,
    cfg: Mapping | None,
    run_id: str,
) -> ExecutionPlan:
    """Build the order legs for an entry without submitting broker orders."""

    side = side.lower()
    policy_cfg = _policy_cfg(cfg)
    parent_intent_id = "|".join([run_id, symbol.upper().replace("/", ""), side, strategy])
    fallback_leg = OrderLeg(
        leg_number=1,
        role="single",
        qty=float(qty),
        order_type=order_type,
        limit_price=_offset_price(price, side, expected_slippage_bps) if order_type == "limit" else None,
    )
    if (
        side != "buy"
        or order_type != "limit"
        or not _cfg_bool(policy_cfg.get("enabled"), False)
        or str(policy_cfg.get("policy", SINGLE_LEG_POLICY)) != TWO_LEG_POLICY
    ):
        return ExecutionPlan(SINGLE_LEG_POLICY, "control", parent_intent_id, (fallback_leg,))

    bucket = choose_policy_bucket(
        run_id=run_id,
        symbol=symbol,
        strategy=strategy,
        asset_class=asset_class,
        policy_cfg=policy_cfg,
    )
    if bucket == "control":
        return ExecutionPlan(SINGLE_LEG_POLICY, bucket, parent_intent_id, (fallback_leg,))

    asset_cfg = _asset_cfg(policy_cfg, asset_class)
    leg1_fraction = min(1.0, max(0.0, _cfg_float(asset_cfg.get("leg1_fraction"), 0.5)))
    leg1_qty = round(float(qty) * leg1_fraction, 8)
    if leg1_qty <= 0 or leg1_qty >= float(qty):
        return ExecutionPlan(SINGLE_LEG_POLICY, bucket, parent_intent_id, (fallback_leg,))
    leg2_qty = max(0.0, float(qty) - leg1_qty)
    leg1_offset = _cfg_float(asset_cfg.get("leg1_offset_bps"), 0.0)
    leg2_offset_raw = asset_cfg.get("leg2_offset_bps", "model")
    leg2_offset = expected_slippage_bps if str(leg2_offset_raw).lower() == "model" else _cfg_float(leg2_offset_raw, expected_slippage_bps)
    timeout = max(0.0, _cfg_float(asset_cfg.get("leg1_timeout_seconds"), 90.0))

    legs = (
        OrderLeg(
            leg_number=1,
            role="passive",
            qty=leg1_qty,
            order_type="limit",
            limit_price=_offset_price(price, side, leg1_offset),
            timeout_seconds=timeout,
        ),
        OrderLeg(
            leg_number=2,
            role="aggressive",
            qty=leg2_qty,
            order_type="limit",
            limit_price=_offset_price(price, side, leg2_offset),
            timeout_seconds=0.0,
        ),
    )
    return ExecutionPlan(TWO_LEG_POLICY, bucket, parent_intent_id, legs)


def _bar_value(bar, name: str) -> float | None:
    if bar is None:
        return None
    value = bar.get(name) if isinstance(bar, Mapping) else getattr(bar, name, None)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def simulate_backtest_entry_price(
    *,
    symbol: str,
    strategy: str,
    asset_class: str,
    qty: float,
    side: str,
    price: float,
    expected_slippage_bps: float,
    cfg: Mapping | None,
    run_id: str,
    next_bar,
) -> tuple[float, str]:
    """Return a deterministic two-leg simulated entry price and policy name.

    The passive leg fills when the next bar range touches the passive limit.
    Any residual quantity fills at the aggressive leg's model-derived limit.
    """

    plan = build_entry_execution_plan(
        symbol=symbol,
        strategy=strategy,
        asset_class=asset_class,
        qty=qty,
        side=side,
        price=price,
        order_type="limit",
        expected_slippage_bps=expected_slippage_bps,
        cfg=cfg,
        run_id=run_id,
    )
    if plan.policy_name != TWO_LEG_POLICY or len(plan.legs) < 2:
        return _offset_price(price, side, expected_slippage_bps), plan.policy_name

    low = _bar_value(next_bar, "low")
    high = _bar_value(next_bar, "high")
    leg1, leg2 = plan.legs
    filled = []
    if low is not None and high is not None and leg1.limit_price is not None and low <= leg1.limit_price <= high:
        filled.append((leg1.qty, leg1.limit_price))
        residual = max(0.0, qty - leg1.qty)
    else:
        residual = qty
    if residual > 0:
        filled.append((residual, leg2.limit_price or _offset_price(price, side, expected_slippage_bps)))
    total_qty = sum(item[0] for item in filled)
    if total_qty <= 0:
        return _offset_price(price, side, expected_slippage_bps), plan.policy_name
    avg_price = sum(item[0] * item[1] for item in filled) / total_qty
    return avg_price, plan.policy_name


def policy_names(plans: Sequence[ExecutionPlan]) -> list[str]:
    return [plan.policy_name for plan in plans]
