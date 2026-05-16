"""
HawksTrade - Crypto Correlation Guard
====================================
Blocks new crypto entries that would add highly correlated exposure to existing
or already-planned crypto positions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import math
from typing import Any, Iterable

from core import alpaca_client as ac
from core.config_loader import get_config


log = logging.getLogger("core.correlation_guard")


@dataclass(frozen=True)
class CorrelationDecision:
    allowed: bool
    reason: str
    code: str = "allowed"
    context: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def allow(cls, reason: str = "Crypto correlation guard passed") -> "CorrelationDecision":
        return cls(True, reason)

    @classmethod
    def block(
        cls,
        reason: str,
        *,
        code: str = "crypto_correlation_blocked",
        context: dict[str, Any] | None = None,
    ) -> "CorrelationDecision":
        return cls(False, reason, code=code, context=context or {})


def _guard_config(cfg: dict) -> dict:
    value = ((cfg.get("trading", {}) or {}).get("crypto_correlation_guard", {}) or {})
    return value if isinstance(value, dict) else {}


def _enabled(cfg: dict) -> bool:
    return bool(_guard_config(cfg).get("enabled", True))


def _float_or_default(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _int_or_default(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _crypto_pair(symbol: str) -> str:
    return ac.to_crypto_pair_symbol(str(symbol))


def _bar_value(bar: Any, name: str, default: Any = None) -> Any:
    if isinstance(bar, dict):
        return bar.get(name, default)
    return getattr(bar, name, default)


def _close_series(bars: Iterable[Any]) -> list[float]:
    closes: list[float] = []
    for bar in bars or []:
        try:
            close = float(_bar_value(bar, "close"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(close) and close > 0:
            closes.append(close)
    return closes


def _returns(closes: list[float]) -> list[float]:
    values: list[float] = []
    for previous, current in zip(closes, closes[1:]):
        if previous > 0:
            values.append((current / previous) - 1.0)
    return values


def _pearson(left: list[float], right: list[float]) -> float | None:
    count = min(len(left), len(right))
    if count < 2:
        return None
    left_tail = left[-count:]
    right_tail = right[-count:]
    left_mean = sum(left_tail) / count
    right_mean = sum(right_tail) / count
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left_tail, right_tail))
    left_var = sum((a - left_mean) ** 2 for a in left_tail)
    right_var = sum((b - right_mean) ** 2 for b in right_tail)
    denominator = math.sqrt(left_var * right_var)
    if denominator == 0:
        return None
    return numerator / denominator


def _planned_crypto_symbols(symbols: Iterable[str], candidate: str) -> list[str]:
    candidate_norm = ac.normalize_symbol(candidate)
    planned: list[str] = []
    seen: set[str] = set()
    for symbol in symbols or []:
        pair = _crypto_pair(str(symbol))
        normalized = ac.normalize_symbol(pair)
        if not normalized or normalized == candidate_norm or normalized in seen:
            continue
        seen.add(normalized)
        planned.append(pair)
    return planned


def _bars_for_symbol(bars: dict[str, Iterable[Any]] | None, symbol: str) -> Iterable[Any]:
    if not bars:
        return []
    if symbol in bars:
        return bars[symbol]

    target = ac.normalize_symbol(symbol)
    for key, value in bars.items():
        if ac.normalize_symbol(str(key)) == target:
            return value
    return []


def evaluate_crypto_correlation(
    candidate_symbol: str,
    planned_symbols: Iterable[str],
    *,
    cfg: dict | None = None,
    bars_data: dict[str, Iterable[Any]] | None = None,
) -> CorrelationDecision:
    """Return whether a crypto candidate can be added to planned exposure."""
    effective_cfg = cfg if cfg is not None else get_config()
    if not _enabled(effective_cfg):
        return CorrelationDecision.allow("Crypto correlation guard disabled")

    guard_cfg = _guard_config(effective_cfg)
    threshold = _float_or_default(guard_cfg.get("max_correlation"), 0.85)
    threshold = max(-1.0, min(threshold, 1.0))
    lookback_days = _int_or_default(guard_cfg.get("lookback_days"), 30)
    fail_closed = bool(guard_cfg.get("fail_closed", True))

    candidate = _crypto_pair(candidate_symbol)
    planned = _planned_crypto_symbols(planned_symbols, candidate)
    if not planned:
        return CorrelationDecision.allow("No planned crypto exposure to compare")

    symbols = [candidate] + planned
    try:
        bars = bars_data
        if bars is None:
            bars = ac.get_crypto_bars(symbols, timeframe="1Day", limit=lookback_days + 1)
    except Exception as exc:
        if fail_closed:
            return CorrelationDecision.block(
                f"Could not fetch crypto correlation bars for {candidate}: {exc}",
                code="crypto_correlation_data_unavailable",
                context={"candidate": candidate, "error": str(exc)},
            )
        log.warning("Crypto correlation guard skipped after data fetch failure: %s", exc)
        return CorrelationDecision.allow("Crypto correlation data unavailable; fail_closed disabled")

    candidate_returns = _returns(_close_series(_bars_for_symbol(bars, candidate)))
    if len(candidate_returns) < 2:
        if fail_closed:
            return CorrelationDecision.block(
                f"Insufficient crypto correlation history for {candidate}",
                code="crypto_correlation_insufficient_history",
                context={"candidate": candidate, "lookback_days": lookback_days},
            )
        return CorrelationDecision.allow("Insufficient crypto correlation history; fail_closed disabled")

    for planned_symbol in planned:
        planned_returns = _returns(_close_series(_bars_for_symbol(bars, planned_symbol)))
        correlation = _pearson(candidate_returns, planned_returns)
        if correlation is None:
            if fail_closed:
                return CorrelationDecision.block(
                    f"Insufficient crypto correlation history for {candidate} vs {planned_symbol}",
                    code="crypto_correlation_insufficient_history",
                    context={"candidate": candidate, "planned_symbol": planned_symbol},
                )
            continue
        if correlation >= threshold:
            return CorrelationDecision.block(
                (
                    f"{candidate} correlation {correlation:.2f} with planned "
                    f"{planned_symbol} exceeds {threshold:.2f}"
                ),
                context={
                    "candidate": candidate,
                    "planned_symbol": planned_symbol,
                    "correlation": round(correlation, 4),
                    "max_correlation": threshold,
                },
            )

    return CorrelationDecision.allow("Crypto correlation guard passed")
