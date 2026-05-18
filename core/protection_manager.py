"""
Entry-only trading protections and lockouts.

Protections are disabled by default. When enabled, this module derives active
locks from recent closed trades and persists them so scans, reports, and health
checks can show why entries are blocked.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core import alpaca_client as ac
from core.config_loader import get_config
from tracking import trade_log


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_LOCK_FILE = BASE_DIR / "data" / "protection_locks.json"

STOPLOSS_REASON_TERMS = (
    "stop-loss",
    "stoploss",
    "max-loss",
    "custom stop",
    "daily loss limit",
)


@dataclass(frozen=True)
class ProtectionLock:
    lock_type: str
    scope: str
    key: str
    reason: str
    trigger: str
    created_at: datetime
    expires_at: datetime
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def id(self) -> str:
        payload = "|".join([
            self.lock_type,
            self.scope,
            self.key,
            self.trigger,
            self.expires_at.isoformat(),
        ])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def is_active(self, now: datetime) -> bool:
        return self.expires_at > _as_utc(now)

    def matches_entry(self, symbol: str, strategy: str) -> bool:
        if self.scope == "global":
            return True
        if self.scope == "symbol":
            return self.key == ac.normalize_symbol(symbol)
        if self.scope == "strategy":
            return self.key == str(strategy or "unknown")
        return False

    def to_dict(self) -> dict:
        payload = {
            "id": self.id,
            "lock_type": self.lock_type,
            "scope": self.scope,
            "key": self.key,
            "reason": self.reason,
            "trigger": self.trigger,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload

    @classmethod
    def from_dict(cls, row: Mapping) -> "ProtectionLock" | None:
        try:
            return cls(
                lock_type=str(row.get("lock_type") or ""),
                scope=str(row.get("scope") or ""),
                key=str(row.get("key") or ""),
                reason=str(row.get("reason") or ""),
                trigger=str(row.get("trigger") or ""),
                created_at=_parse_datetime(row.get("created_at")) or _utc_now(),
                expires_at=_parse_datetime(row.get("expires_at")) or _utc_now(),
                metadata=row.get("metadata") or {},
            )
        except Exception:
            return None


@dataclass(frozen=True)
class ProtectionDecision:
    allowed: bool
    reason: str = "OK"
    lock: ProtectionLock | None = None


@dataclass(frozen=True)
class ProtectionConfig:
    enabled: bool = False
    symbol_cooldown_days: float = 1.0
    symbol_stoploss_cooldown_days: float = 3.0
    symbol_stoploss_loss_pct: float = 0.035
    strategy_stoploss_lookback_days: float = 10.0
    strategy_stoploss_threshold: int = 3
    strategy_stoploss_cooldown_days: float = 3.0
    low_profit_lookback_days: float = 20.0
    low_profit_min_trades: int = 5
    low_profit_threshold_pct: float = 0.0
    low_profit_cooldown_days: float = 5.0
    max_drawdown_lookback_days: float = 20.0
    max_drawdown_pct: float = 0.05
    max_drawdown_cooldown_days: float = 5.0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return _as_utc(value)
    if not value:
        return None
    try:
        return _as_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def _parse_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _cfg_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _cfg_float(value, default: float) -> float:
    parsed = _parse_float(value)
    if parsed is None or parsed < 0:
        return default
    return parsed


def _cfg_signed_float(value, default: float) -> float:
    parsed = _parse_float(value)
    return default if parsed is None else parsed


def _cfg_int(value, default: int) -> int:
    parsed = _parse_float(value)
    if parsed is None:
        return default
    return max(0, int(parsed))


def _closed_sell_rows(rows: Iterable[Mapping]) -> list[Mapping]:
    return [
        row for row in rows
        if str(row.get("side", "")).lower() == "sell"
        and str(row.get("status", "")).lower() == "closed"
    ]


def _row_timestamp(row: Mapping) -> datetime | None:
    return (
        _parse_datetime(row.get("exit_date"))
        or _parse_datetime(row.get("timestamp"))
        or _parse_datetime(row.get("closed_at"))
    )


def _row_pnl_pct(row: Mapping) -> float | None:
    return _parse_float(row.get("pnl_pct"))


def _row_portfolio_pnl_pct(row: Mapping) -> tuple[float, str] | None:
    for key in ("portfolio_pnl_pct", "portfolio_return_pct", "portfolio_impact_pct"):
        parsed = _parse_float(row.get(key))
        if parsed is not None:
            return parsed, key
    return None


def _is_stoploss_row(row: Mapping, cfg: ProtectionConfig) -> bool:
    reason = str(row.get("exit_reason") or row.get("reason") or "").lower()
    if any(term in reason for term in STOPLOSS_REASON_TERMS):
        return True
    pnl_pct = _row_pnl_pct(row)
    return pnl_pct is not None and pnl_pct <= -abs(cfg.symbol_stoploss_loss_pct)


def _lock(
    *,
    lock_type: str,
    scope: str,
    key: str,
    reason: str,
    trigger: str,
    created_at: datetime,
    expires_at: datetime,
    metadata: Mapping[str, object] | None = None,
) -> ProtectionLock | None:
    if expires_at <= created_at:
        return None
    return ProtectionLock(
        lock_type=lock_type,
        scope=scope,
        key=key,
        reason=reason,
        trigger=trigger,
        created_at=created_at,
        expires_at=expires_at,
        metadata=metadata or {},
    )


class ProtectionManager:
    def __init__(self, config: ProtectionConfig | None = None, *, lock_file: Path | None = None):
        self.config = config or ProtectionConfig()
        self.lock_file = Path(lock_file) if lock_file is not None else DEFAULT_LOCK_FILE

    @classmethod
    def from_config(cls, cfg: Mapping | None = None, *, lock_file: Path | None = None) -> "ProtectionManager":
        cfg = cfg or get_config()
        raw = {}
        if isinstance(cfg, Mapping):
            raw = cfg.get("protections", cfg.get("protection_manager", {})) or {}
        if not isinstance(raw, Mapping):
            raw = {}
        config = ProtectionConfig(
            enabled=_cfg_bool(raw.get("enabled"), False),
            symbol_cooldown_days=_cfg_float(raw.get("symbol_cooldown_days"), 1.0),
            symbol_stoploss_cooldown_days=_cfg_float(raw.get("symbol_stoploss_cooldown_days"), 3.0),
            symbol_stoploss_loss_pct=_cfg_float(raw.get("symbol_stoploss_loss_pct"), 0.035),
            strategy_stoploss_lookback_days=_cfg_float(raw.get("strategy_stoploss_lookback_days"), 10.0),
            strategy_stoploss_threshold=_cfg_int(raw.get("strategy_stoploss_threshold"), 3),
            strategy_stoploss_cooldown_days=_cfg_float(raw.get("strategy_stoploss_cooldown_days"), 3.0),
            low_profit_lookback_days=_cfg_float(raw.get("low_profit_lookback_days"), 20.0),
            low_profit_min_trades=_cfg_int(raw.get("low_profit_min_trades"), 5),
            low_profit_threshold_pct=_cfg_signed_float(raw.get("low_profit_threshold_pct"), 0.0),
            low_profit_cooldown_days=_cfg_float(raw.get("low_profit_cooldown_days"), 5.0),
            max_drawdown_lookback_days=_cfg_float(raw.get("max_drawdown_lookback_days"), 20.0),
            max_drawdown_pct=_cfg_float(raw.get("max_drawdown_pct"), 0.05),
            max_drawdown_cooldown_days=_cfg_float(raw.get("max_drawdown_cooldown_days"), 5.0),
        )
        return cls(config, lock_file=lock_file)

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def active_locks(self, *, now: datetime | None = None) -> list[ProtectionLock]:
        now = _as_utc(now or _utc_now())
        if not self.enabled:
            return []
        return [lock for lock in self._read_locks() if lock.is_active(now)]

    def evaluate_entry(self, symbol: str, strategy: str, *, now: datetime | None = None) -> ProtectionDecision:
        if not self.enabled:
            return ProtectionDecision(True)
        for lock in self.active_locks(now=now):
            if lock.matches_entry(symbol, strategy):
                return ProtectionDecision(False, lock.reason, lock)
        return ProtectionDecision(True)

    def refresh_from_trade_log(self, *, now: datetime | None = None) -> list[ProtectionLock]:
        return self.refresh_from_rows(trade_log.read_trade_rows(), now=now)

    def refresh_from_rows(self, rows: Iterable[Mapping], *, now: datetime | None = None) -> list[ProtectionLock]:
        now = _as_utc(now or _utc_now())
        if not self.enabled:
            return []
        existing = [lock for lock in self._read_locks() if lock.is_active(now)]
        generated = self._locks_from_rows(rows, now=now)
        merged: dict[str, ProtectionLock] = {lock.id: lock for lock in existing + generated if lock.is_active(now)}
        locks = sorted(merged.values(), key=lambda lock: (lock.expires_at, lock.scope, lock.key))
        self._write_locks(locks)
        return locks

    def _locks_from_rows(self, rows: Iterable[Mapping], *, now: datetime) -> list[ProtectionLock]:
        rows = _closed_sell_rows(rows)
        locks: list[ProtectionLock] = []
        locks.extend(self._symbol_cooldown_locks(rows, now))
        locks.extend(self._symbol_stoploss_locks(rows, now))
        locks.extend(self._strategy_stoploss_locks(rows, now))
        locks.extend(self._low_profit_strategy_locks(rows, now))
        drawdown_lock = self._rolling_drawdown_lock(rows, now)
        if drawdown_lock is not None:
            locks.append(drawdown_lock)
        return [lock for lock in locks if lock is not None and lock.is_active(now)]

    def _symbol_cooldown_locks(self, rows: list[Mapping], now: datetime) -> list[ProtectionLock]:
        locks = []
        if self.config.symbol_cooldown_days <= 0:
            return locks
        for row in rows:
            ts = _row_timestamp(row)
            symbol = str(row.get("symbol") or "")
            if ts is None or not symbol:
                continue
            expires_at = ts + timedelta(days=self.config.symbol_cooldown_days)
            locks.append(_lock(
                lock_type="symbol_cooldown_after_exit",
                scope="symbol",
                key=ac.normalize_symbol(symbol),
                reason=f"{symbol} is cooling down after a recent exit",
                trigger=str(row.get("exit_reason") or "recent exit"),
                created_at=ts,
                expires_at=expires_at,
                metadata={"symbol": symbol},
            ))
        return locks

    def _symbol_stoploss_locks(self, rows: list[Mapping], now: datetime) -> list[ProtectionLock]:
        locks = []
        if self.config.symbol_stoploss_cooldown_days <= 0:
            return locks
        for row in rows:
            if not _is_stoploss_row(row, self.config):
                continue
            ts = _row_timestamp(row)
            symbol = str(row.get("symbol") or "")
            if ts is None or not symbol:
                continue
            locks.append(_lock(
                lock_type="symbol_stoploss_guard",
                scope="symbol",
                key=ac.normalize_symbol(symbol),
                reason=f"{symbol} locked after a stop-loss style exit",
                trigger=str(row.get("exit_reason") or f"pnl_pct={row.get('pnl_pct')}"),
                created_at=ts,
                expires_at=ts + timedelta(days=self.config.symbol_stoploss_cooldown_days),
                metadata={"symbol": symbol, "pnl_pct": _row_pnl_pct(row)},
            ))
        return locks

    def _strategy_stoploss_locks(self, rows: list[Mapping], now: datetime) -> list[ProtectionLock]:
        locks = []
        if self.config.strategy_stoploss_threshold <= 0 or self.config.strategy_stoploss_cooldown_days <= 0:
            return locks
        cutoff = now - timedelta(days=self.config.strategy_stoploss_lookback_days)
        by_strategy: dict[str, list[Mapping]] = {}
        for row in rows:
            ts = _row_timestamp(row)
            if ts is None or ts < cutoff or not _is_stoploss_row(row, self.config):
                continue
            strategy = str(row.get("strategy") or "unknown")
            by_strategy.setdefault(strategy, []).append(row)
        for strategy, hits in by_strategy.items():
            if len(hits) < self.config.strategy_stoploss_threshold:
                continue
            latest_ts = max(_row_timestamp(row) or now for row in hits)
            locks.append(_lock(
                lock_type="strategy_stoploss_guard",
                scope="strategy",
                key=strategy,
                reason=f"{strategy} locked after {len(hits)} stop-loss style exits",
                trigger=f"{len(hits)} stop-loss exits in {self.config.strategy_stoploss_lookback_days:g}d",
                created_at=latest_ts,
                expires_at=latest_ts + timedelta(days=self.config.strategy_stoploss_cooldown_days),
                metadata={"count": len(hits)},
            ))
        return locks

    def _low_profit_strategy_locks(self, rows: list[Mapping], now: datetime) -> list[ProtectionLock]:
        locks = []
        if self.config.low_profit_min_trades <= 0 or self.config.low_profit_cooldown_days <= 0:
            return locks
        cutoff = now - timedelta(days=self.config.low_profit_lookback_days)
        by_strategy: dict[str, list[tuple[Mapping, float]]] = {}
        for row in rows:
            ts = _row_timestamp(row)
            pnl_pct = _row_pnl_pct(row)
            if ts is None or ts < cutoff or pnl_pct is None:
                continue
            strategy = str(row.get("strategy") or "unknown")
            by_strategy.setdefault(strategy, []).append((row, pnl_pct))
        for strategy, items in by_strategy.items():
            if len(items) < self.config.low_profit_min_trades:
                continue
            avg_pnl = sum(pnl for _row, pnl in items) / len(items)
            if avg_pnl > self.config.low_profit_threshold_pct:
                continue
            latest_ts = max(_row_timestamp(row) or now for row, _pnl in items)
            locks.append(_lock(
                lock_type="low_profit_strategy_lock",
                scope="strategy",
                key=strategy,
                reason=f"{strategy} locked for low recent average profit ({avg_pnl:.2%})",
                trigger=f"{len(items)} trades avg_pnl={avg_pnl:.6f}",
                created_at=latest_ts,
                expires_at=latest_ts + timedelta(days=self.config.low_profit_cooldown_days),
                metadata={"count": len(items), "avg_pnl_pct": avg_pnl},
            ))
        return locks

    def _rolling_drawdown_lock(self, rows: list[Mapping], now: datetime) -> ProtectionLock | None:
        if self.config.max_drawdown_pct <= 0 or self.config.max_drawdown_cooldown_days <= 0:
            return None
        cutoff = now - timedelta(days=self.config.max_drawdown_lookback_days)
        points = []
        for row in rows:
            ts = _row_timestamp(row)
            portfolio_return = _row_portfolio_pnl_pct(row)
            if portfolio_return is not None:
                return_pct, return_source = portfolio_return
            else:
                return_pct = _row_pnl_pct(row)
                return_source = "pnl_pct"
            if ts is None or ts < cutoff or return_pct is None:
                continue
            points.append((ts, return_pct, return_source))
        points.sort(key=lambda item: item[0])
        if not points:
            return None
        equity = 1.0
        peak = 1.0
        max_drawdown = 0.0
        for _ts, return_pct, _source in points:
            equity *= 1.0 + return_pct
            peak = max(peak, equity)
            drawdown = (equity - peak) / peak if peak > 0 else 0.0
            max_drawdown = min(max_drawdown, drawdown)
        if max_drawdown > -abs(self.config.max_drawdown_pct):
            return None
        latest_ts = points[-1][0]
        return _lock(
            lock_type="rolling_max_drawdown_lock",
            scope="global",
            key="*",
            reason=f"Global entries locked after rolling drawdown {max_drawdown:.2%}",
            trigger=f"max_drawdown={max_drawdown:.6f}",
            created_at=latest_ts,
            expires_at=latest_ts + timedelta(days=self.config.max_drawdown_cooldown_days),
            metadata={
                "max_drawdown_pct": max_drawdown,
                "trades": len(points),
                "return_sources": sorted({source for _ts, _return, source in points}),
            },
        )

    def _read_locks(self) -> list[ProtectionLock]:
        with trade_log.locked_trade_log(self.lock_file, exclusive=False) as lock_file:
            if not lock_file.exists():
                return []
            try:
                payload = json.loads(lock_file.read_text(encoding="utf-8"))
            except OSError as exc:
                raise RuntimeError(f"Could not read protection lock file {lock_file}: {exc}") from exc
            except json.JSONDecodeError as exc:
                raise ValueError(f"Malformed protection lock file {lock_file}: {exc}") from exc
        rows = payload.get("locks", []) if isinstance(payload, Mapping) else []
        locks = []
        for row in rows:
            lock = ProtectionLock.from_dict(row)
            if lock is not None:
                locks.append(lock)
        return locks

    def _write_locks(self, locks: list[ProtectionLock]) -> None:
        payload = {"version": 1, "locks": [lock.to_dict() for lock in locks]}
        with trade_log.locked_trade_log(self.lock_file, exclusive=True) as lock_file:
            lock_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = lock_file.with_name(f"{lock_file.name}.{uuid.uuid4().hex}.tmp")
            try:
                tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
                tmp_path.replace(lock_file)
            finally:
                if tmp_path.exists():
                    tmp_path.unlink()


def active_locks_for_reporting(now: datetime | None = None) -> list[dict]:
    manager = ProtectionManager.from_config(get_config())
    return [lock.to_dict() for lock in manager.active_locks(now=now)]
