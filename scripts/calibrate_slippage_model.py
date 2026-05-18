#!/usr/bin/env python3
"""Propose slippage-model k updates from logged fills.

The script is intentionally read-only: it prints a YAML snippet with proposed
`slippage_model` values instead of editing config/config.yaml.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config_loader import get_config

BASE_DIR = Path(__file__).resolve().parent.parent


def _float(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _parse_timestamp(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_rows(path: Path, since: datetime | None) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    if since is None:
        return rows
    return [row for row in rows if (ts := _parse_timestamp(row.get("timestamp"))) is not None and ts >= since]


def _asset_class(row: dict) -> str:
    raw = str(row.get("asset_class", "")).lower()
    return "crypto" if "crypto" in raw or "/" in str(row.get("symbol", "")) else "stock"


def _fit_scale(rows: list[dict]) -> float | None:
    xs = []
    ys = []
    for row in rows:
        expected = _float(row.get("expected_slippage_bps"))
        realised = _float(row.get("realised_slippage_bps"))
        if expected is None or realised is None or expected <= 0 or realised < 0:
            continue
        xs.append(expected)
        ys.append(realised)
    if not xs:
        return None
    denom = sum(x * x for x in xs)
    if denom <= 0:
        return None
    return sum(x * y for x, y in zip(xs, ys)) / denom


def main() -> int:
    parser = argparse.ArgumentParser(description="Propose slippage model calibration from trade-log fills.")
    parser.add_argument("--since", help="ISO timestamp/date lower bound, e.g. 2026-01-01")
    parser.add_argument("--trade-log", default=None, help="Override trade log path")
    parser.add_argument("--min-fills", type=int, default=50, help="Minimum fills per asset class before proposing k")
    args = parser.parse_args()

    cfg = get_config()
    trade_log = Path(args.trade_log) if args.trade_log else BASE_DIR / cfg["reporting"]["trade_log_file"]
    since = _parse_timestamp(args.since) if args.since else None
    rows = _load_rows(trade_log, since)
    by_asset = {
        "stock": [row for row in rows if _asset_class(row) == "stock"],
        "crypto": [row for row in rows if _asset_class(row) == "crypto"],
    }

    current = cfg.get("slippage_model", {}) or {}
    proposal = {"slippage_model": dict(current)}
    notes = []
    for asset, asset_rows in by_asset.items():
        scale = _fit_scale(asset_rows)
        key = "k_crypto" if asset == "crypto" else "k_stock"
        current_k = _float(current.get(key)) or 1.0
        usable = sum(
            1
            for row in asset_rows
            if _float(row.get("expected_slippage_bps")) is not None
            and _float(row.get("realised_slippage_bps")) is not None
        )
        if scale is None or usable < args.min_fills:
            notes.append(f"{asset}: insufficient fills ({usable}/{args.min_fills}); keeping {key}={current_k}")
            proposal["slippage_model"][key] = current_k
            continue
        proposal["slippage_model"][key] = round(max(0.1, min(100.0, current_k * scale)), 4)
        notes.append(f"{asset}: n={usable}, scale={scale:.3f}, {key} {current_k:.4f} -> {proposal['slippage_model'][key]:.4f}")

    print("# Proposed config/config.yaml snippet (review before applying)")
    print(yaml.safe_dump(proposal, sort_keys=False).rstrip())
    print("\n# Calibration notes")
    for note in notes:
        print(f"# - {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
