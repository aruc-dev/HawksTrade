"""
HawksTrade - HawksCapitol Copy Strategy
=======================================

Adapter strategy that consumes scored HawksCapitol copy-buy signals from a
persisted JSON file and emits normal HawksTrade buy signals. HawksCapitol owns
disclosure ingestion, point-in-time scoring, and filing-lag filtering; HawksTrade
owns portfolio risk, execution, trade logging, and exits.
"""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from core.config_loader import get_config
from strategies.base_strategy import BaseStrategy


BASE_DIR = Path(__file__).resolve().parent.parent
CFG = get_config()
log = logging.getLogger("strategy.capitol_copy")


def _as_utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _parse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper().replace(" ", "")


def _truthy_text(value: Any) -> bool:
    return bool(str(value or "").strip())


def _resolve_signal_path(raw_path: str | None, base_dir: Path) -> Path:
    env_path = os.getenv("HAWKSTRADE_CAPITOL_SIGNAL_PATH")
    configured = env_path or raw_path or "integrations/HawksCapitol/data/signals/latest.json"
    path = Path(configured).expanduser()
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _load_signal_rows(path: Path) -> list[dict]:
    if not path.exists():
        log.info("[CapitolCopy] Signal file does not exist: %s", path)
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("[CapitolCopy] Could not load signal file %s: %s", path, exc)
        return []

    rows = payload.get("signals", []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        log.warning("[CapitolCopy] Signal file %s did not contain a signal list.", path)
        return []
    return [row for row in rows if isinstance(row, dict)]


class CapitolCopyStrategy(BaseStrategy):
    name = "capitol_copy"
    asset_class = "stocks"

    def __init__(self, cfg: dict | None = None, base_dir: Path | None = None):
        self.cfg = cfg if cfg is not None else CFG
        self.base_dir = Path(base_dir) if base_dir is not None else BASE_DIR

    @property
    def strategy_cfg(self) -> dict:
        strategies = self.cfg.get("strategies", {}) or {}
        value = strategies.get(self.name, {}) or {}
        return value if isinstance(value, dict) else {}

    def _signal_path(self) -> Path:
        return _resolve_signal_path(self.strategy_cfg.get("signal_path"), self.base_dir)

    def scan(self, universe: List[str], **kwargs) -> List[Dict]:
        cfg = self.strategy_cfg
        if not bool(cfg.get("enabled", False)):
            return []

        rows = _load_signal_rows(self._signal_path())
        if not rows:
            return []

        now = _as_utc(kwargs.get("current_time"))
        max_age_hours = _finite_float(cfg.get("max_signal_age_hours", 72), 72.0)
        min_conviction = _finite_float(cfg.get("min_conviction_score", 0.65), 0.65)
        min_freshness = _finite_float(cfg.get("min_freshness_score", 0.35), 0.35)
        min_entry_quality = _finite_float(cfg.get("min_entry_quality_score", 0.55), 0.55)
        max_signals = max(0, int(_finite_float(cfg.get("max_signals", 1), 1.0)))
        if max_signals == 0:
            return []
        require_created_at = bool(cfg.get("require_created_at", True))
        respect_scan_universe = bool(cfg.get("respect_scan_universe", False))
        allowed_asset_types = {
            str(item).strip().lower()
            for item in cfg.get("allowed_asset_types", ["stock"])
            if str(item).strip()
        }
        allowed_symbols = {
            _normalize_symbol(item)
            for item in cfg.get("allowed_symbols", [])
            if _normalize_symbol(item)
        }
        universe_symbols = {_normalize_symbol(symbol) for symbol in universe if _normalize_symbol(symbol)}
        if respect_scan_universe and not universe_symbols:
            log.warning("[CapitolCopy] Scan universe is empty while respect_scan_universe=true; blocking all signals.")
            return []
        existing_symbols = {
            _normalize_symbol(symbol)
            for symbol in kwargs.get("existing_symbols", [])
            if _normalize_symbol(symbol)
        }

        candidates = []
        for row in rows:
            symbol = _normalize_symbol(row.get("ticker") or row.get("symbol"))
            if not symbol:
                continue
            if symbol in existing_symbols:
                continue
            if allowed_symbols and symbol not in allowed_symbols:
                continue
            if respect_scan_universe and universe_symbols and symbol not in universe_symbols:
                continue

            side = str(row.get("side") or row.get("action") or "").strip().lower()
            if side not in {"buy", "copy_buy"}:
                continue
            asset_type = str(row.get("asset_type") or "stock").strip().lower()
            if allowed_asset_types and asset_type not in allowed_asset_types:
                continue
            if _truthy_text(row.get("blocked_reason")):
                continue

            created_at = _parse_timestamp(row.get("created_at"))
            if created_at is None:
                if require_created_at:
                    continue
            else:
                age_hours = (now - created_at).total_seconds() / 3600.0
                if age_hours < -0.1:
                    continue
                if max_age_hours > 0 and age_hours > max_age_hours:
                    continue

            conviction = _finite_float(row.get("conviction_score"))
            freshness = _finite_float(row.get("freshness_score"))
            entry_quality = _finite_float(row.get("entry_quality_score"))
            if conviction < min_conviction:
                continue
            if freshness < min_freshness:
                continue
            if entry_quality < min_entry_quality:
                continue

            target_weight = _finite_float(row.get("target_weight_pct"))
            composite = (0.45 * conviction) + (0.30 * entry_quality) + (0.20 * freshness) + (0.05 * target_weight)
            candidates.append((composite, target_weight, conviction, entry_quality, freshness, row, symbol, created_at))

        candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3], item[4]), reverse=True)
        signals = []
        for composite, target_weight, conviction, entry_quality, freshness, row, symbol, created_at in candidates[:max_signals]:
            signal_id = str(row.get("signal_id") or "").strip()
            rationale = str(row.get("rationale") or "HawksCapitol copy-buy signal").strip()
            signals.append({
                "symbol": symbol,
                "action": "buy",
                "strategy": self.name,
                "confidence": round(max(0.0, min(1.0, composite)), 4),
                "reason": (
                    f"HawksCapitol signal {signal_id or symbol}: "
                    f"conviction={conviction:.2f} freshness={freshness:.2f} "
                    f"entry_quality={entry_quality:.2f}"
                ),
                "source_system": "HawksCapitol",
                "source_signal_id": signal_id,
                "source_tx_ids": row.get("source_tx_ids") or [],
                "source_created_at": created_at.isoformat() if created_at else "",
                "source_rationale": rationale,
                "source_scores": {
                    "conviction_score": round(conviction, 4),
                    "freshness_score": round(freshness, 4),
                    "entry_quality_score": round(entry_quality, 4),
                    "target_weight_pct": round(target_weight, 4),
                    "composite_score": round(composite, 4),
                },
            })
        log.info("[CapitolCopy] Accepted %s/%s HawksCapitol signals.", len(signals), len(rows))
        return signals

    def should_exit(self, symbol: str, entry_price: float) -> tuple:
        return False, ""
