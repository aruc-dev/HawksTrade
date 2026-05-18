"""
Central pre-trade order governor.

The governor performs broker-state checks that belong immediately before order
submission. Strategy and risk sizing still live elsewhere; this module blocks
orders that are structurally unsafe to submit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Mapping, Sequence

from core import alpaca_client as ac
from tracking import order_intents


TERMINAL_ORDER_STATUSES = {
    "canceled",
    "cancelled",
    "expired",
    "filled",
    "rejected",
    "replaced",
}


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: str
    qty: float
    order_type: str
    asset_class: str = "stock"
    strategy: str = "unknown"
    price: float | None = None
    limit_price: float | None = None
    position_qty: float | None = None
    parent_intent_id: str | None = None

    @property
    def normalized_symbol(self) -> str:
        return ac.normalize_symbol(self.symbol)

    @property
    def notional(self) -> float | None:
        price = self.limit_price if self.limit_price is not None else self.price
        try:
            qty = float(self.qty)
            px = float(price)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(qty) or not math.isfinite(px) or qty <= 0 or px <= 0:
            return None
        return qty * px


@dataclass(frozen=True)
class GovernorDecision:
    status: str
    allowed: bool
    reason: str
    code: str = ""
    warnings: tuple[str, ...] = ()
    context: Mapping[str, object] = field(default_factory=dict)

    @classmethod
    def allow(cls, reason: str = "OK", *, warnings: Sequence[str] = (), context: Mapping[str, object] | None = None):
        status = "warn" if warnings else "allow"
        return cls(status=status, allowed=True, reason=reason, warnings=tuple(warnings), context=context or {})

    @classmethod
    def block(cls, reason: str, *, code: str, context: Mapping[str, object] | None = None):
        return cls(status="block", allowed=False, reason=reason, code=code, context=context or {})


def _cfg_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _cfg_int(value, default: int | None) -> int | None:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else None


def _cfg_float(value, default: float | None) -> float | None:
    if value in (None, ""):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _value(obj, name: str, default=None):
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _lower_value(obj, name: str) -> str:
    raw = _value(obj, name, "")
    return str(getattr(raw, "value", raw) or "").lower()


def _positive_float(value) -> float | None:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


def _active_orders(orders: Iterable) -> list:
    active = []
    for order in orders or []:
        status = _lower_value(order, "status")
        if status not in TERMINAL_ORDER_STATUSES:
            active.append(order)
    return active


def _same_symbol(left: str, right: str) -> bool:
    return ac.normalize_symbol(str(left or "")) == ac.normalize_symbol(str(right or ""))


def _parse_timestamp(value) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _has_account_state(account_state) -> bool:
    if account_state is None:
        return False
    return any(
        _positive_float(_value(account_state, name)) is not None
        for name in ("portfolio_value", "cash", "buying_power")
    )


class OrderGovernor:
    """Evaluate broker-order safety gates before order submission."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        max_active_orders: int | None = 50,
        max_orders_per_window: int | None = 60,
        order_rate_window_seconds: int = 60,
        max_daily_orders: int | None = 500,
        max_notional_usd: float | None = None,
        max_notional_pct: float | None = None,
        order_history_provider: Callable[[], list[dict]] | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ):
        self.enabled = enabled
        self.max_active_orders = max_active_orders
        self.max_orders_per_window = max_orders_per_window
        self.order_rate_window_seconds = max(1, int(order_rate_window_seconds or 60))
        self.max_daily_orders = max_daily_orders
        self.max_notional_usd = max_notional_usd
        self.max_notional_pct = max_notional_pct
        self.order_history_provider = order_history_provider or order_intents.read_order_intents
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    @classmethod
    def from_config(
        cls,
        cfg: Mapping,
        *,
        order_history_provider: Callable[[], list[dict]] | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> "OrderGovernor":
        raw = cfg.get("order_governor", {}) if isinstance(cfg, Mapping) else {}
        if not isinstance(raw, Mapping):
            raw = {}
        return cls(
            enabled=_cfg_bool(raw.get("enabled"), True),
            max_active_orders=_cfg_int(raw.get("max_active_orders"), 50),
            max_orders_per_window=_cfg_int(raw.get("max_orders_per_window"), 60),
            order_rate_window_seconds=_cfg_int(raw.get("order_rate_window_seconds"), 60) or 60,
            max_daily_orders=_cfg_int(raw.get("max_daily_orders"), 500),
            max_notional_usd=_cfg_float(raw.get("max_notional_usd"), None),
            max_notional_pct=_cfg_float(raw.get("max_notional_pct"), None),
            order_history_provider=order_history_provider,
            now_provider=now_provider,
        )

    def evaluate(self, order_intent: OrderIntent, account_state, broker_orders) -> GovernorDecision:
        if not self.enabled:
            return GovernorDecision.allow("Order governor disabled")

        validation = self._validate_intent(order_intent)
        if validation is not None:
            return validation
        if not _has_account_state(account_state):
            return GovernorDecision.block("Missing account state for order governor", code="missing_account_state")
        if broker_orders is None:
            return GovernorDecision.block("Missing broker open-order state for order governor", code="missing_broker_orders")

        active_orders = _active_orders(broker_orders)
        side = order_intent.side.lower()

        duplicate = self._duplicate_active_order(order_intent, active_orders)
        if duplicate is not None:
            order_id = _value(duplicate, "id", _value(duplicate, "order_id", "unknown"))
            code = "duplicate_pending_exit" if side == "sell" else "duplicate_pending_entry"
            return GovernorDecision.block(
                f"Pending {side} order already exists for {order_intent.symbol}",
                code=code,
                context={"order_id": order_id, "symbol": order_intent.symbol, "side": side},
            )

        if side == "sell":
            return self._evaluate_exit(order_intent)

        return self._evaluate_entry(order_intent, account_state, active_orders)

    def _validate_intent(self, order_intent: OrderIntent) -> GovernorDecision | None:
        side = str(order_intent.side or "").lower()
        if side not in {"buy", "sell"}:
            return GovernorDecision.block(f"Invalid order side: {order_intent.side}", code="invalid_side")
        order_type = str(order_intent.order_type or "").lower()
        if order_type not in {"market", "limit"}:
            return GovernorDecision.block(f"Invalid order type: {order_intent.order_type}", code="invalid_order_type")
        qty = _positive_float(order_intent.qty)
        if qty is None:
            return GovernorDecision.block(f"Invalid quantity for {order_intent.symbol}: {order_intent.qty}", code="invalid_qty")
        if order_type == "limit" and _positive_float(order_intent.limit_price) is None:
            return GovernorDecision.block(
                f"Invalid limit price for {order_intent.symbol}: {order_intent.limit_price}",
                code="invalid_limit_price",
            )
        return None

    def _duplicate_active_order(self, order_intent: OrderIntent, active_orders: Sequence):
        for order in active_orders:
            if _lower_value(order, "side") != order_intent.side.lower():
                continue
            if _same_symbol(_value(order, "symbol", ""), order_intent.symbol):
                return order
        return None

    def _evaluate_exit(self, order_intent: OrderIntent) -> GovernorDecision:
        position_qty = _positive_float(order_intent.position_qty)
        if position_qty is None:
            return GovernorDecision.block(
                f"Missing open-position quantity for exit {order_intent.symbol}",
                code="missing_position_qty",
            )
        qty = float(order_intent.qty)
        if qty > position_qty + 1e-9:
            return GovernorDecision.block(
                f"Exit quantity {qty} exceeds open position {position_qty} for {order_intent.symbol}",
                code="oversell_qty",
                context={"qty": qty, "position_qty": position_qty},
            )
        return GovernorDecision.allow("Exit order passed governor checks")

    def _evaluate_entry(self, order_intent: OrderIntent, account_state, active_orders: Sequence) -> GovernorDecision:
        if self.max_active_orders is not None and len(active_orders) >= self.max_active_orders:
            return GovernorDecision.block(
                f"Max active broker orders reached: {len(active_orders)}/{self.max_active_orders}",
                code="max_active_orders",
                context={"active_orders": len(active_orders), "limit": self.max_active_orders},
            )

        notional_result = self._check_entry_notional(order_intent, account_state)
        if notional_result is not None:
            return notional_result

        history_result = self._check_entry_history_limits()
        if history_result is not None:
            return history_result

        return GovernorDecision.allow("Entry order passed governor checks")

    def _check_entry_notional(self, order_intent: OrderIntent, account_state) -> GovernorDecision | None:
        notional = order_intent.notional
        if self.max_notional_usd is None and self.max_notional_pct is None:
            return None
        if notional is None:
            return GovernorDecision.block(
                f"Could not calculate order notional for {order_intent.symbol}",
                code="missing_notional",
            )
        max_allowed = self.max_notional_usd
        if self.max_notional_pct is not None:
            portfolio_value = _positive_float(_value(account_state, "portfolio_value"))
            if portfolio_value is None:
                return GovernorDecision.block(
                    "Missing portfolio value for max-notional order governor check",
                    code="missing_portfolio_value",
                )
            pct_limit = portfolio_value * self.max_notional_pct
            max_allowed = min(max_allowed, pct_limit) if max_allowed is not None else pct_limit
        if max_allowed is not None and notional > max_allowed + 1e-9:
            return GovernorDecision.block(
                f"Order notional ${notional:,.2f} exceeds governor limit ${max_allowed:,.2f}",
                code="max_notional",
                context={"notional": notional, "limit": max_allowed},
            )
        return None

    def _check_entry_history_limits(self) -> GovernorDecision | None:
        if self.max_orders_per_window is None and self.max_daily_orders is None:
            return None
        try:
            history = self.order_history_provider() or []
        except Exception as exc:
            return GovernorDecision.block(
                f"Could not load order history for governor checks: {exc}",
                code="order_history_unavailable",
            )

        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
        logical_orders: dict[str, datetime] = {}
        for idx, row in enumerate(history):
            ts = _parse_timestamp(row.get("timestamp"))
            if ts is None:
                continue
            logical_id = str(row.get("parent_intent_id") or row.get("client_order_id") or idx)
            existing = logical_orders.get(logical_id)
            if existing is None or ts < existing:
                logical_orders[logical_id] = ts
        timestamps = list(logical_orders.values())

        if self.max_orders_per_window is not None:
            cutoff = now - timedelta(seconds=self.order_rate_window_seconds)
            recent_count = sum(1 for ts in timestamps if ts >= cutoff)
            if recent_count >= self.max_orders_per_window:
                return GovernorDecision.block(
                    f"Order rate limit reached: {recent_count}/{self.max_orders_per_window} "
                    f"in {self.order_rate_window_seconds}s",
                    code="order_rate_limit",
                    context={"recent_orders": recent_count, "limit": self.max_orders_per_window},
                )

        if self.max_daily_orders is not None:
            daily_count = sum(1 for ts in timestamps if ts.date() == now.date())
            if daily_count >= self.max_daily_orders:
                return GovernorDecision.block(
                    f"Daily order limit reached: {daily_count}/{self.max_daily_orders}",
                    code="daily_order_limit",
                    context={"daily_orders": daily_count, "limit": self.max_daily_orders},
                )
        return None
