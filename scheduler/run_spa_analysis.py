#!/usr/bin/env python3
"""Run Hansen SPA-style multiple-testing correction from a returns matrix."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from analysis.spa_test import returns_matrix_from_csv, spa_test, strategy_search_space_catalog  # noqa: E402
from core.config_loader import get_config  # noqa: E402


def _decision(p_value: float) -> str:
    if p_value < 0.05:
        return "strong evidence; eligible for full sample-size-allowed risk"
    if p_value < 0.20:
        return "weak evidence; keep capped and continue collecting OOS evidence"
    return "not statistically distinguishable after multiple-testing correction"


def render_catalog_report() -> str:
    cfg = get_config()
    enabled = [
        name for name, raw in cfg.get("strategies", {}).items()
        if raw.get("enabled", False)
    ]
    catalog = strategy_search_space_catalog()
    lines = [
        "# Multiple-Testing Correction Catalog",
        "",
        "The Phase 1 SPA workflow uses `scheduler/run_backtest.py --grid <strategy>` ",
        "to emit daily return matrices, then `scheduler/run_spa_analysis.py` to compute p-values.",
        "",
        "| Strategy | Enabled | Variants |",
        "|---|---:|---:|",
    ]
    for strategy, variants in sorted(catalog.items()):
        lines.append(f"| `{strategy}` | {strategy in enabled} | {len(variants)} |")
    return "\n".join(lines) + "\n"


def run_spa_report(
    *,
    returns_csv: str | Path,
    benchmark_column: str = "benchmark",
    n_boot: int = 5000,
    block_size: int = 5,
    seed: int = 42,
) -> str:
    strategies, benchmark = returns_matrix_from_csv(str(returns_csv), benchmark_column=benchmark_column)
    result = spa_test(strategies, benchmark, n_boot=n_boot, block_size=block_size, seed=seed)
    lines = [
        "# Multiple-Testing Correction Report",
        "",
        f"- Returns matrix: `{returns_csv}`",
        f"- Benchmark column: `{benchmark_column}`",
        f"- Method: {result.method}",
        f"- Variants tested: {result.variants}",
        f"- Observations: {result.observations}",
        f"- Best variant: `{result.best_strategy}`",
        f"- SPA statistic: {result.statistic:.4f}",
        f"- SPA p-value: {result.p_value:.4f}",
        f"- Decision: {_decision(result.p_value)}",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--returns-csv", help="CSV with date, benchmark, and strategy return columns")
    parser.add_argument("--benchmark-column", default="benchmark")
    parser.add_argument("--output", type=str)
    parser.add_argument("--n-boot", type=int, default=5000)
    parser.add_argument("--block-size", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--catalog", action="store_true", help="Render the configured search-space catalog")
    args = parser.parse_args(argv)

    if args.catalog:
        report = render_catalog_report()
    else:
        if not args.returns_csv:
            parser.error("--returns-csv is required unless --catalog is used")
        report = run_spa_report(
            returns_csv=args.returns_csv,
            benchmark_column=args.benchmark_column,
            n_boot=args.n_boot,
            block_size=args.block_size,
            seed=args.seed,
        )
    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = BASE_DIR / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
