"""
HawksTrade - Strategy Live Readiness
====================================
Runtime guard that blocks live entries for strategies without configured paper
validation evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import math
from typing import Any, Iterable

from core.config_loader import get_config
from tracking.trade_log import read_trade_rows


log = logging.getLogger("core.strategy_readiness")


@dataclass(frozen=True)
class ReadinessDecision:
    allowed: bool
    reason: str
    code: str = "allowed"
    context: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def allow(cls, reason: str = "Strategy live readiness passed") -> "ReadinessDecision":
        return cls(True, reason)

    @classmethod
    def block(
        cls,
        reason: str,
        *,
        code: str = "strategy_live_readiness_blocked",
        context: dict[str, Any] | None = None,
    ) -> "ReadinessDecision":
        return cls(False, reason, code=code, context=context or {})


def _runtime_mode(mode: str | None, cfg: dict) -> str:
    raw = mode if mode is not None else cfg.get("mode", "paper")
    return str(raw or "paper").strip().lower()


def _strategy_config(cfg: dict, strategy: str) -> dict:
    strategies = cfg.get("strategies", {}) or {}
    value = strategies.get(strategy, {}) or {}
    return value if isinstance(value, dict) else {}


def _readiness_config(cfg: dict, strategy: str) -> dict:
    strategy_cfg = _strategy_config(cfg, strategy)
    value = strategy_cfg.get("live_readiness", {}) or {}
    return value if isinstance(value, dict) else {}


def _float_or_default(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _int_or_default(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _matching_paper_closed_rows(rows: Iterable[dict], strategy: str) -> list[dict]:
    return [
        row for row in rows
        if str(row.get("strategy", "")).strip() == strategy
        and str(row.get("mode", "")).strip().lower() == "paper"
        and str(row.get("side", "")).strip().lower() == "sell"
        and str(row.get("status", "")).strip().lower() == "closed"
    ]


def evaluate_strategy_live_readiness(
    strategy: str,
    *,
    mode: str | None = None,
    cfg: dict | None = None,
    rows: Iterable[dict] | None = None,
    now: datetime | None = None,
) -> ReadinessDecision:
    """Return whether a strategy may open live entries under configured gates."""
    effective_cfg = cfg if cfg is not None else get_config()
    if _runtime_mode(mode, effective_cfg) != "live":
        return ReadinessDecision.allow("Strategy readiness skipped outside live mode")

    readiness = _readiness_config(effective_cfg, strategy)
    if not readiness or not bool(readiness.get("enabled", True)):
        return ReadinessDecision.allow("Strategy live readiness gate not configured")

    min_trades = _int_or_default(readiness.get("min_closed_paper_trades"), 0)
    min_days = _float_or_default(readiness.get("min_paper_days"), 0.0)
    if min_trades <= 0 and min_days <= 0:
        return ReadinessDecision.allow("Strategy live readiness gate has no thresholds")

    trade_rows = list(rows if rows is not None else read_trade_rows())
    qualifying_rows = _matching_paper_closed_rows(trade_rows, strategy)
    closed_count = len(qualifying_rows)
    context = {
        "strategy": strategy,
        "closed_paper_trades": closed_count,
        "min_closed_paper_trades": min_trades,
        "min_paper_days": min_days,
    }

    if closed_count < min_trades:
        return ReadinessDecision.block(
            (
                f"{strategy} needs {min_trades} closed paper trades before live entries; "
                f"found {closed_count}"
            ),
            context=context,
        )

    if min_days > 0:
        timestamps = [
            parsed for parsed in (_parse_timestamp(row.get("timestamp")) for row in qualifying_rows)
            if parsed is not None
        ]
        if not timestamps:
            return ReadinessDecision.block(
                f"{strategy} needs {min_days:g} paper validation days before live entries; no dated paper exits found",
                context=context,
            )
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        age_days = (current - min(timestamps)).total_seconds() / 86400.0
        context["paper_validation_days"] = round(age_days, 2)
        if age_days < min_days:
            return ReadinessDecision.block(
                (
                    f"{strategy} needs {min_days:g} paper validation days before live entries; "
                    f"found {age_days:.1f}"
                ),
                context=context,
            )

    return ReadinessDecision.allow("Strategy live readiness passed")
