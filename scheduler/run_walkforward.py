"""
HawksTrade - Rolling Window Validation
======================================
Runs repeated historical backtest windows to check whether a strategy profile is
stable across different recent periods.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.config_loader import get_config  # noqa: E402
from scheduler.run_backtest import run_backtest  # noqa: E402


@dataclass(frozen=True)
class WalkForwardThresholds:
    min_return_pct: float
    max_drawdown_pct: float
    min_profit_factor: float
    min_trades: int
    min_pass_rate: float


def _parse_end_date(value: str | None) -> datetime:
    if value:
        return datetime.strptime(value, "%m/%d/%Y").replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - timedelta(days=2)


def _format_end_date(value: datetime) -> str:
    return value.strftime("%m/%d/%Y")


def build_rolling_windows(*, end_date: str | None, windows: int, step_days: int) -> list[str]:
    if windows <= 0:
        raise ValueError("windows must be positive")
    if step_days <= 0:
        raise ValueError("step_days must be positive")
    latest_end = _parse_end_date(end_date)
    return [
        _format_end_date(latest_end - timedelta(days=step_days * offset))
        for offset in range(windows - 1, -1, -1)
    ]


def _format_pct(value: float) -> str:
    return f"{value:+.2%}"


def _format_ratio(value: float) -> str:
    return "inf" if math.isinf(value) else f"{value:.2f}"


def _as_strategy_list(value: str | None) -> list[str] | None:
    if not value:
        return None
    strategies = [part.strip() for part in value.split(",") if part.strip()]
    return strategies or None


def _cost_model_from_config(cfg: dict, *, slippage_bps, fee_bps, min_fee_usd) -> dict:
    configured = dict((cfg.get("validation", {}) or {}).get("cost_model", {}) or {})
    return {
        "slippage_bps": configured.get("slippage_bps", 0.0) if slippage_bps is None else slippage_bps,
        "fee_bps": configured.get("fee_bps", 0.0) if fee_bps is None else fee_bps,
        "min_fee_usd": configured.get("min_fee_usd", 0.0) if min_fee_usd is None else min_fee_usd,
    }


def window_failures(stats: dict, thresholds: WalkForwardThresholds) -> list[str]:
    failures: list[str] = []
    if stats["return_pct"] < thresholds.min_return_pct:
        failures.append(
            f"return {_format_pct(stats['return_pct'])} < {_format_pct(thresholds.min_return_pct)}"
        )
    if stats["max_drawdown"] < -thresholds.max_drawdown_pct:
        failures.append(
            f"drawdown {_format_pct(stats['max_drawdown'])} exceeds -{thresholds.max_drawdown_pct:.2%}"
        )
    if stats["profit_factor"] < thresholds.min_profit_factor:
        failures.append(
            f"profit_factor {_format_ratio(stats['profit_factor'])} < {thresholds.min_profit_factor:.2f}"
        )
    if int(stats["trades"]) < thresholds.min_trades:
        failures.append(f"trades {stats['trades']} < {thresholds.min_trades}")
    return failures


def run_walkforward(
    *,
    window_days: int = 180,
    step_days: int = 90,
    windows: int = 4,
    end_date: str | None = None,
    initial_fund: float = 10000.0,
    use_screener: bool | None = None,
    strategies: str | None = None,
    slippage_bps: float | None = None,
    fee_bps: float | None = None,
    min_fee_usd: float | None = None,
    thresholds: WalkForwardThresholds | None = None,
) -> tuple[int, str]:
    if window_days <= 0:
        raise ValueError("window_days must be positive")

    cfg = get_config()
    cost_model = _cost_model_from_config(
        cfg,
        slippage_bps=slippage_bps,
        fee_bps=fee_bps,
        min_fee_usd=min_fee_usd,
    )
    selected_strategies = _as_strategy_list(strategies)
    thresholds = thresholds or WalkForwardThresholds(
        min_return_pct=0.0,
        max_drawdown_pct=0.06,
        min_profit_factor=1.0,
        min_trades=5,
        min_pass_rate=0.75,
    )

    records: list[dict] = []
    for window_end in build_rolling_windows(end_date=end_date, windows=windows, step_days=step_days):
        result = run_backtest(
            days=window_days,
            initial_fund=initial_fund,
            end_date=window_end,
            use_screener=use_screener,
            enabled_strategies=selected_strategies,
            cost_model=cost_model,
            return_result=True,
            write_quarterly_csv=False,
        )
        stats = result["stats"]
        failures = window_failures(stats, thresholds)
        records.append({
            "end_date": window_end,
            "passed": not failures,
            "failures": failures,
            "stats": stats,
        })

    passed = sum(1 for record in records if record["passed"])
    pass_rate = passed / len(records) if records else 0.0

    lines = [
        "### HawksTrade Rolling Window Validation",
        f"Window: {window_days} days | Step: {step_days} days | Windows: {len(records)}",
        (
            "Cost model: "
            f"slippage={float(cost_model.get('slippage_bps', 0.0)):.2f} bps, "
            f"fee={float(cost_model.get('fee_bps', 0.0)):.2f} bps, "
            f"min_fee=${float(cost_model.get('min_fee_usd', 0.0)):.2f}"
        ),
        "",
        "| Status | End Date | Return | Max DD | Trades | Win | PF | Sharpe | Notes |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for record in records:
        stats = record["stats"]
        status = "PASS" if record["passed"] else "FAIL"
        notes = "; ".join(record["failures"])
        lines.append(
            f"| {status} | {record['end_date']} | {_format_pct(stats['return_pct'])} | "
            f"{_format_pct(stats['max_drawdown'])} | {stats['trades']} | "
            f"{stats['win_rate']:.1%} | {_format_ratio(stats['profit_factor'])} | "
            f"{stats['daily_sharpe']:.2f} | {notes} |"
        )

    if pass_rate >= thresholds.min_pass_rate:
        lines.append(f"\nRESULT: PASS ({passed}/{len(records)} windows passed, pass_rate={pass_rate:.1%})")
        return 0, "\n".join(lines)
    lines.append(
        f"\nRESULT: FAIL ({passed}/{len(records)} windows passed, "
        f"pass_rate={pass_rate:.1%} < required {thresholds.min_pass_rate:.1%})"
    )
    return 1, "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--window-days", type=int, default=180)
    parser.add_argument("--step-days", type=int, default=90)
    parser.add_argument("--windows", type=int, default=4)
    parser.add_argument("--end-date", type=str, help="Latest window end date (MM/DD/YYYY)")
    parser.add_argument("--fund", type=float, default=10000.0)
    parser.add_argument("--screener", dest="use_screener", action="store_true")
    parser.add_argument("--no-screener", dest="use_screener", action="store_false")
    parser.set_defaults(use_screener=None)
    parser.add_argument("--strategies", type=str, help="Comma-separated strategy names")
    parser.add_argument("--slippage-bps", type=float)
    parser.add_argument("--fee-bps", type=float)
    parser.add_argument("--min-fee-usd", type=float)
    parser.add_argument("--min-return-pct", type=float, default=0.0)
    parser.add_argument("--max-drawdown-pct", type=float, default=0.06)
    parser.add_argument("--min-profit-factor", type=float, default=1.0)
    parser.add_argument("--min-trades", type=int, default=5)
    parser.add_argument("--min-pass-rate", type=float, default=0.75)
    args = parser.parse_args()

    exit_code, output = run_walkforward(
        window_days=args.window_days,
        step_days=args.step_days,
        windows=args.windows,
        end_date=args.end_date,
        initial_fund=args.fund,
        use_screener=args.use_screener,
        strategies=args.strategies,
        slippage_bps=args.slippage_bps,
        fee_bps=args.fee_bps,
        min_fee_usd=args.min_fee_usd,
        thresholds=WalkForwardThresholds(
            min_return_pct=args.min_return_pct,
            max_drawdown_pct=args.max_drawdown_pct,
            min_profit_factor=args.min_profit_factor,
            min_trades=args.min_trades,
            min_pass_rate=args.min_pass_rate,
        ),
    )
    print(output)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
