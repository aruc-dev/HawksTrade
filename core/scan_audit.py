"""Structured scan audit logging for scheduler runs."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AUDIT_SCHEMA_VERSION = 1
BLOCK_STATUSES = {
    "entry_blocked",
    "entry_failed",
    "order_governor_blocked",
    "strategy_readiness_blocked",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    current = value or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat()


def audit_log_path(output_dir: Path, current: datetime | None = None) -> Path:
    current = current or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return Path(output_dir) / f"scan_audit_{current.astimezone(timezone.utc).strftime('%Y%m%d')}.jsonl"


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return _iso(value)
    return str(value)


def _symbols(values: Iterable[Any] | None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values or []:
        symbol = str(value or "").strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        result.append(symbol)
    return result


def _signal_summary(signal: Mapping[str, Any], *, strategy: str, asset_class: str) -> dict[str, Any]:
    fields = {
        "symbol",
        "action",
        "confidence",
        "reason",
        "atr_risk_qty",
        "atr_stop_price",
        "source_system",
        "source_signal_id",
        "source_created_at",
        "source_scores",
    }
    summary = {
        "strategy": strategy,
        "asset_class": asset_class,
    }
    for field in fields:
        if field in signal and signal.get(field) not in (None, ""):
            summary[field] = _json_safe(signal.get(field))
    summary.setdefault("symbol", str(signal.get("symbol") or ""))
    summary.setdefault("action", str(signal.get("action") or ""))
    return summary


def _result_summary(result: Mapping[str, Any] | None, *, symbol: str, strategy: str, asset_class: str) -> dict[str, Any]:
    if result is None:
        return {
            "symbol": symbol,
            "strategy": strategy,
            "asset_class": asset_class,
            "status": "no_result",
            "error": "order executor returned no result",
        }
    fields = {
        "symbol",
        "strategy",
        "asset_class",
        "status",
        "side",
        "qty",
        "entry_price",
        "order_id",
        "order_type",
        "governor_code",
        "readiness_code",
        "block_code",
        "error_type",
        "error",
        "limit_price",
    }
    summary = {
        "symbol": symbol,
        "strategy": strategy,
        "asset_class": asset_class,
    }
    for field in fields:
        if field in result and result.get(field) not in (None, ""):
            summary[field] = _json_safe(result.get(field))
    summary.setdefault("status", "")
    return summary


class ScanAuditRecorder:
    """Collect and append one structured JSON object per scan run."""

    def __init__(
        self,
        *,
        output_dir: Path,
        run_id: str,
        mode: str,
        dry_run: bool,
        run_stocks: bool,
        run_crypto: bool,
        strategy_filter: Iterable[str] | None = None,
        started_at: datetime | None = None,
        enabled: bool = True,
    ):
        self.enabled = bool(enabled)
        self.output_dir = Path(output_dir)
        self.started_at = started_at or utc_now()
        self.output_path = audit_log_path(self.output_dir, self.started_at)
        self.record: dict[str, Any] = {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "run_id": str(run_id or ""),
            "started_at": _iso(self.started_at),
            "mode": str(mode or "").lower(),
            "dry_run": bool(dry_run),
            "run_stocks": bool(run_stocks),
            "run_crypto": bool(run_crypto),
            "strategy_filter": sorted(str(item) for item in (strategy_filter or []) if str(item).strip()),
            "market_open": None,
            "open_symbols": [],
            "pending_entry_symbols": {},
            "universes": {},
            "strategies": [],
            "signals": [],
            "blocks": [],
            "entry_results": [],
            "errors": [],
            "summary": {},
        }

    def record_market_context(self, *, market_open: bool, open_symbols: Iterable[Any]) -> None:
        self.record["market_open"] = bool(market_open)
        self.record["open_symbols"] = _symbols(open_symbols)

    def record_pending_entries(self, pending_entry_symbols: Mapping[str, str] | None) -> None:
        self.record["pending_entry_symbols"] = {
            str(symbol): str(asset_class)
            for symbol, asset_class in (pending_entry_symbols or {}).items()
        }

    def record_universe(
        self,
        asset_class: str,
        symbols: Iterable[Any] | None,
        *,
        source: str,
        static_symbols: Iterable[Any] | None = None,
        dynamic_symbols: Iterable[Any] | None = None,
        skipped_reason: str = "",
    ) -> None:
        evaluated = _symbols(symbols)
        self.record["universes"][asset_class] = {
            "source": source,
            "evaluated_count": len(evaluated),
            "evaluated_symbols": evaluated,
            "static_symbols": _symbols(static_symbols),
            "dynamic_symbols": _symbols(dynamic_symbols),
            "skipped_reason": skipped_reason,
        }

    def record_strategy_scan(
        self,
        *,
        strategy: str,
        asset_class: str,
        universe: Iterable[Any],
        signals: Iterable[Mapping[str, Any]],
        status: str = "completed",
    ) -> None:
        evaluated = _symbols(universe)
        signal_rows = [_signal_summary(signal, strategy=strategy, asset_class=asset_class) for signal in signals]
        buy_signal_symbols = {
            str(signal.get("symbol") or "").strip()
            for signal in signal_rows
            if str(signal.get("action") or "").lower() == "buy"
        }
        rejections = [
            {
                "symbol": symbol,
                "stage": "strategy_scan",
                "code": "no_signal",
                "reason": "strategy emitted no buy signal for evaluated symbol",
            }
            for symbol in evaluated
            if symbol not in buy_signal_symbols
        ]
        self.record["strategies"].append(
            {
                "strategy": strategy,
                "asset_class": asset_class,
                "status": status,
                "evaluated_count": len(evaluated),
                "evaluated_universe": evaluated,
                "signal_count": len(signal_rows),
                "signals": signal_rows,
                "rejections": rejections,
            }
        )
        self.record["signals"].extend(signal_rows)

    def record_strategy_error(
        self,
        *,
        strategy: str,
        asset_class: str,
        universe: Iterable[Any],
        error: Exception,
    ) -> None:
        evaluated = _symbols(universe)
        error_row = {
            "strategy": strategy,
            "asset_class": asset_class,
            "stage": "strategy_scan",
            "error_type": type(error).__name__,
            "error": str(error),
        }
        self.record["strategies"].append(
            {
                "strategy": strategy,
                "asset_class": asset_class,
                "status": "error",
                "evaluated_count": len(evaluated),
                "evaluated_universe": evaluated,
                "signal_count": 0,
                "signals": [],
                "rejections": [],
                "error": error_row,
            }
        )
        self.record["errors"].append(error_row)
        self.record_block(
            stage="strategy_scan",
            code="strategy_error",
            reason=str(error),
            strategy=strategy,
            asset_class=asset_class,
            context={"error_type": type(error).__name__},
        )

    def record_block(
        self,
        *,
        stage: str,
        code: str,
        reason: str,
        symbol: str = "",
        strategy: str = "",
        asset_class: str = "",
        context: Mapping[str, Any] | None = None,
    ) -> None:
        self.record["blocks"].append(
            {
                "timestamp": _iso(),
                "stage": stage,
                "code": code,
                "reason": reason,
                "symbol": symbol,
                "strategy": strategy,
                "asset_class": asset_class,
                "context": _json_safe(dict(context or {})),
            }
        )

    def record_entry_result(
        self,
        result: Mapping[str, Any] | None,
        *,
        symbol: str,
        strategy: str,
        asset_class: str,
    ) -> None:
        summary = _result_summary(result, symbol=symbol, strategy=strategy, asset_class=asset_class)
        self.record["entry_results"].append(summary)
        status = str(summary.get("status") or "")
        if result is None:
            self.record_block(
                stage="execution",
                code="no_result",
                reason="order executor returned no result",
                symbol=symbol,
                strategy=strategy,
                asset_class=asset_class,
            )
            return
        if status in BLOCK_STATUSES:
            self.record_block(
                stage="execution",
                code=str(summary.get("block_code") or summary.get("governor_code") or summary.get("readiness_code") or status),
                reason=str(summary.get("error") or status),
                symbol=str(summary.get("symbol") or symbol),
                strategy=str(summary.get("strategy") or strategy),
                asset_class=str(summary.get("asset_class") or asset_class),
                context={key: summary[key] for key in ("error_type", "order_type") if key in summary},
            )

    def finish(self, *, status: str, outcome: str) -> Path | None:
        self.record["completed_at"] = _iso()
        self.record["status"] = str(status or "")
        self.record["outcome"] = str(outcome or "")
        self.record["summary"] = {
            "signals": len(self.record["signals"]),
            "blocks": len(self.record["blocks"]),
            "entry_results": len(self.record["entry_results"]),
            "strategy_runs": len(self.record["strategies"]),
            "no_signal_rejections": sum(
                len(strategy.get("rejections", []))
                for strategy in self.record["strategies"]
            ),
        }
        if not self.enabled:
            return None
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.output_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(_json_safe(self.record), sort_keys=True) + "\n")
        return self.output_path


def load_audit_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def latest_audit_records(log_dir: Path, *, limit: int = 1) -> list[dict[str, Any]]:
    paths = sorted(Path(log_dir).glob("scan_audit_*.jsonl"), reverse=True)
    records: list[dict[str, Any]] = []
    for path in paths:
        for record in reversed(load_audit_records(path)):
            records.append(record)
            if len(records) >= limit:
                return list(reversed(records))
    return list(reversed(records))
