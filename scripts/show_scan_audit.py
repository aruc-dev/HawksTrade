#!/usr/bin/env python3
"""Print a concise view of recent scan audit records."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.scan_audit import latest_audit_records, load_audit_records  # noqa: E402


BASE_DIR = Path(__file__).resolve().parent.parent


def _fmt_symbols(symbols, *, limit=12) -> str:
    values = [str(symbol) for symbol in symbols or []]
    if not values:
        return "-"
    shown = ", ".join(values[:limit])
    if len(values) > limit:
        shown += f", ... (+{len(values) - limit})"
    return shown


def format_record(record: dict) -> str:
    lines = [
        (
            f"Run {record.get('run_id', '')} | status={record.get('status', '')} "
            f"outcome={record.get('outcome', '')} dry_run={record.get('dry_run')}"
        ),
        f"Started: {record.get('started_at', '')} | Completed: {record.get('completed_at', '')}",
        f"Market open: {record.get('market_open')} | Open positions: {_fmt_symbols(record.get('open_symbols'))}",
    ]
    universes = record.get("universes", {}) or {}
    for asset_class in ("stock", "crypto"):
        info = universes.get(asset_class)
        if not info:
            continue
        line = (
            f"{asset_class.title()} universe: {info.get('evaluated_count', 0)} evaluated "
            f"| source={info.get('source', '')}"
        )
        if info.get("skipped_reason"):
            line += f" | skipped={info['skipped_reason']}"
        lines.append(line)
        if info.get("dynamic_symbols"):
            lines.append(f"  dynamic: {_fmt_symbols(info.get('dynamic_symbols'))}")
        if info.get("static_symbols"):
            lines.append(f"  static: {_fmt_symbols(info.get('static_symbols'))}")
        lines.append(f"  evaluated: {_fmt_symbols(info.get('evaluated_symbols'))}")

    summary = record.get("summary", {}) or {}
    lines.append(
        "Summary: "
        f"signals={summary.get('signals', 0)} "
        f"entry_results={summary.get('entry_results', 0)} "
        f"blocks={summary.get('blocks', 0)} "
        f"no_signal={summary.get('no_signal_rejections', 0)}"
    )
    for strategy in record.get("strategies", []) or []:
        lines.append(
            f"Strategy {strategy.get('strategy', '')}: "
            f"evaluated={strategy.get('evaluated_count', 0)} "
            f"signals={strategy.get('signal_count', 0)} "
            f"no_signal={len(strategy.get('rejections', []) or [])} "
            f"status={strategy.get('status', '')}"
        )
    blocks = record.get("blocks", []) or []
    if blocks:
        lines.append("Blocks:")
        for block in blocks[:20]:
            lines.append(
                "  "
                f"{block.get('stage', '')}/{block.get('code', '')} "
                f"{block.get('strategy', '')}:{block.get('symbol', '')} "
                f"- {block.get('reason', '')}"
            )
        if len(blocks) > 20:
            lines.append(f"  ... (+{len(blocks) - 20} more)")
    return "\n".join(lines)


def _records_from_args(args) -> list[dict]:
    if args.path:
        records = load_audit_records(Path(args.path))
        return records[-args.limit :]
    return latest_audit_records(Path(args.log_dir), limit=args.limit)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show recent HawksTrade scan audit records")
    parser.add_argument("--log-dir", default=str(BASE_DIR / "logs"), help="Directory containing scan_audit_*.jsonl")
    parser.add_argument("--path", help="Specific scan audit JSONL file to read")
    parser.add_argument("--limit", type=int, default=1, help="Number of recent records to print")
    parser.add_argument("--json", action="store_true", help="Print raw JSON records")
    args = parser.parse_args(argv)

    records = _records_from_args(args)
    if args.json:
        print(json.dumps(records, indent=2, sort_keys=True))
        return 0
    if not records:
        print("No scan audit records found.")
        return 1
    for idx, record in enumerate(records):
        if idx:
            print()
        print(format_record(record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
