"""
Pure risk-decision helpers for entry order planning.

Broker/account checks still live in order_executor and risk_manager during this
migration step. This module centralizes the scan-level entry gates that were
previously embedded directly in scheduler/run_scan.py.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Set
from dataclasses import dataclass, field
from typing import Any

from core.trading_models import PortfolioTarget, RiskDecision


def _identity_symbol(symbol: str) -> str:
    return str(symbol)


@dataclass(frozen=True)
class EntryRiskContext:
    planned_symbols: Set[str] = field(default_factory=set)
    planned_asset_classes: Mapping[str, str] = field(default_factory=dict)
    protection_manager: Any | None = None
    protection_entries_blocked: bool = False
    normalize_symbol: Callable[[str], str] = _identity_symbol
    cap_reached: Callable[[str, Mapping[str, str]], bool] | None = None


def evaluate_entry_target(target: PortfolioTarget, context: EntryRiskContext) -> RiskDecision:
    if target.side != "buy":
        return RiskDecision.block(
            target,
            code="unsupported_action",
            reason=f"Unsupported entry action: {target.side}",
        )

    if not str(target.symbol or "").strip():
        return RiskDecision.block(
            target,
            code="missing_symbol",
            reason="Missing target symbol",
        )

    normalized = context.normalize_symbol(target.symbol)
    if normalized in context.planned_symbols:
        return RiskDecision.block(
            target,
            code="duplicate_planned_symbol",
            reason=f"{target.symbol} is already held or planned",
            context={"normalized_symbol": normalized},
        )

    if context.cap_reached is not None and context.cap_reached(
        target.asset_class,
        context.planned_asset_classes,
    ):
        return RiskDecision.block(
            target,
            code="planned_cap_reached",
            reason=f"Planned {target.asset_class} position cap reached",
        )

    if context.protection_entries_blocked:
        return RiskDecision.block(
            target,
            code="protection_refresh_failed",
            reason="Protection refresh failed; new entries are blocked fail-closed",
        )

    if context.protection_manager is not None:
        decision = context.protection_manager.evaluate_entry(target.symbol, target.strategy)
        if not decision.allowed:
            lock = decision.lock
            lock_context = {}
            if lock is not None:
                lock_context = {
                    "lock_type": lock.lock_type,
                    "scope": lock.scope,
                    "key": lock.key,
                }
            return RiskDecision.block(
                target,
                code="protection_lock",
                reason=decision.reason,
                context=lock_context,
            )

    return RiskDecision.allow(target)
