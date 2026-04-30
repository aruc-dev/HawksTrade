"""
HawksTrade - Production Validation Gate
======================================
Runs cost-aware backtest windows and paper-trade criteria before live scaling or
before enabling disabled alpha sleeves.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.config_loader import get_config  # noqa: E402
from scheduler.run_backtest import run_backtest  # noqa: E402


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _format_pct(value: float) -> str:
    return f"{value:+.2%}"


def _format_ratio(value: float) -> str:
    if math.isinf(value):
        return "inf"
    return f"{value:.2f}"


def threshold_failures(stats: dict, gate: dict) -> list[str]:
    """Return human-readable threshold failures for a backtest gate."""
    failures: list[str] = []

    min_return = gate.get("min_return_pct")
    if min_return is not None and stats["return_pct"] < float(min_return):
        failures.append(
            f"return {_format_pct(stats['return_pct'])} < required {_format_pct(float(min_return))}"
        )

    max_drawdown = gate.get("max_drawdown_pct")
    if max_drawdown is not None and stats["max_drawdown"] < -float(max_drawdown):
        failures.append(
            f"drawdown {_format_pct(stats['max_drawdown'])} exceeds -{float(max_drawdown):.2%}"
        )

    min_profit_factor = gate.get("min_profit_factor")
    if min_profit_factor is not None and stats["profit_factor"] < float(min_profit_factor):
        failures.append(
            f"profit_factor {_format_ratio(stats['profit_factor'])} < {float(min_profit_factor):.2f}"
        )

    min_sharpe = gate.get("min_daily_sharpe")
    if min_sharpe is not None and stats["daily_sharpe"] < float(min_sharpe):
        failures.append(
            f"daily_sharpe {stats['daily_sharpe']:.2f} < {float(min_sharpe):.2f}"
        )

    min_trades = gate.get("min_trades")
    if min_trades is not None and int(stats["trades"]) < int(min_trades):
        failures.append(f"trades {stats['trades']} < {int(min_trades)}")

    min_win_rate = gate.get("min_win_rate")
    if min_win_rate is not None and stats["win_rate"] < float(min_win_rate):
        failures.append(
            f"win_rate {stats['win_rate']:.1%} < {float(min_win_rate):.1%}"
        )

    return failures


def evaluate_backtest_gate(gate: dict, cost_model: dict, initial_fund: float) -> dict:
    """Run one configured backtest gate and return a pass/fail record."""
    result = run_backtest(
        days=int(gate["days"]),
        initial_fund=initial_fund,
        end_date=gate.get("end_date"),
        use_screener=gate.get("screener"),
        enabled_strategies=_as_list(gate.get("strategies")) or None,
        cost_model=cost_model,
        return_result=True,
    )
    stats = result["stats"]
    failures = threshold_failures(stats, gate)
    required = bool(gate.get("required", True))
    return {
        "name": gate["name"],
        "required": required,
        "passed": not failures,
        "failures": failures,
        "stats": stats,
    }


def _load_trade_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _float_value(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _profit_factor_from_returns(values: Iterable[float]) -> float:
    gross_profit = sum(v for v in values if v > 0)
    gross_loss = abs(sum(v for v in values if v < 0))
    if gross_loss == 0:
        return math.inf if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def _max_drawdown_from_returns(values: Iterable[float]) -> float:
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for ret in values:
        equity *= 1 + ret
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, (equity / peak) - 1)
    return max_drawdown


def evaluate_rsi_forward_gate(rows: list[dict], criteria: dict) -> dict:
    """Evaluate RSI Reversion paper-trade history against enablement criteria."""
    rsi_rows = [
        row for row in rows
        if (
            row.get("strategy") == "rsi_reversion"
            and row.get("status") == "closed"
            and str(row.get("side", "")).strip().lower() == "sell"
        )
    ]
    returns = [_float_value(row.get("pnl_pct")) for row in rsi_rows]
    timestamps = [
        parsed for parsed in (_parse_timestamp(row.get("timestamp", "")) for row in rsi_rows)
        if parsed is not None
    ]
    paper_days = 0
    if timestamps:
        paper_days = max((max(timestamps).date() - min(timestamps).date()).days + 1, 1)

    wins = sum(1 for ret in returns if ret > 0)
    closed_trades = len(returns)
    stats = {
        "paper_days": paper_days,
        "closed_trades": closed_trades,
        "win_rate": wins / closed_trades if closed_trades else 0.0,
        "profit_factor": _profit_factor_from_returns(returns),
        "total_return_pct": sum(returns),
        "max_drawdown": _max_drawdown_from_returns(returns),
    }

    failures: list[str] = []
    if paper_days < int(criteria.get("required_paper_days", 0)):
        failures.append(
            f"paper_days {paper_days} < {int(criteria.get('required_paper_days', 0))}"
        )
    if closed_trades < int(criteria.get("min_closed_trades", 0)):
        failures.append(
            f"closed_trades {closed_trades} < {int(criteria.get('min_closed_trades', 0))}"
        )
    if stats["win_rate"] < float(criteria.get("min_win_rate", 0.0)):
        failures.append(
            f"win_rate {stats['win_rate']:.1%} < {float(criteria.get('min_win_rate', 0.0)):.1%}"
        )
    if stats["profit_factor"] < float(criteria.get("min_profit_factor", 0.0)):
        failures.append(
            f"profit_factor {_format_ratio(stats['profit_factor'])} < {float(criteria.get('min_profit_factor', 0.0)):.2f}"
        )
    if stats["total_return_pct"] < float(criteria.get("min_total_return_pct", 0.0)):
        failures.append(
            f"total_return {_format_pct(stats['total_return_pct'])} < {_format_pct(float(criteria.get('min_total_return_pct', 0.0)))}"
        )
    max_drawdown = criteria.get("max_drawdown_pct")
    if max_drawdown is not None and stats["max_drawdown"] < -float(max_drawdown):
        failures.append(
            f"drawdown {_format_pct(stats['max_drawdown'])} exceeds -{float(max_drawdown):.2%}"
        )

    return {
        "name": "rsi_reversion_forward_paper",
        "required": True,
        "passed": not failures,
        "failures": failures,
        "stats": stats,
    }


def _render_backtest_record(record: dict) -> str:
    stats = record["stats"]
    status = "PASS" if record["passed"] else ("FAIL" if record["required"] else "WARN")
    line = (
        f"{status:4} {record['name']}: return={_format_pct(stats['return_pct'])}, "
        f"drawdown={_format_pct(stats['max_drawdown'])}, trades={stats['trades']}, "
        f"win={stats['win_rate']:.1%}, pf={_format_ratio(stats['profit_factor'])}, "
        f"sharpe={stats['daily_sharpe']:.2f}"
    )
    if record["failures"]:
        line += " | " + "; ".join(record["failures"])
    return line


def _render_rsi_forward_record(record: dict) -> str:
    stats = record["stats"]
    status = "PASS" if record["passed"] else "FAIL"
    line = (
        f"{status:4} {record['name']}: paper_days={stats['paper_days']}, "
        f"closed_trades={stats['closed_trades']}, win={stats['win_rate']:.1%}, "
        f"pf={_format_ratio(stats['profit_factor'])}, "
        f"total_return={_format_pct(stats['total_return_pct'])}, "
        f"drawdown={_format_pct(stats['max_drawdown'])}"
    )
    if record["failures"]:
        line += " | " + "; ".join(record["failures"])
    return line


def run_validation_gate(profile: str = "production", initial_fund: float = 10000.0) -> tuple[int, str]:
    cfg = get_config()
    validation_cfg = cfg.get("validation", {})
    cost_model = validation_cfg.get("cost_model", {})

    records: list[dict] = []
    lines = [
        "### HawksTrade Validation Gate",
        (
            "Cost model: "
            f"slippage={float(cost_model.get('slippage_bps', 0.0)):.2f} bps, "
            f"fee={float(cost_model.get('fee_bps', 0.0)):.2f} bps, "
            f"min_fee=${float(cost_model.get('min_fee_usd', 0.0)):.2f}"
        ),
        "",
    ]

    if profile in {"production", "all"}:
        lines.append("Production gates:")
        for gate in validation_cfg.get("production_gate", {}).get("windows", []):
            record = evaluate_backtest_gate(gate, cost_model, initial_fund)
            records.append(record)
            lines.append(_render_backtest_record(record))
        lines.append("")

    if profile in {"rsi", "all"}:
        rsi_cfg = validation_cfg.get("rsi_reversion_enablement", {})
        lines.append("RSI Reversion enablement gates:")
        for gate in rsi_cfg.get("backtest_windows", []):
            record = evaluate_backtest_gate(gate, cost_model, initial_fund)
            records.append(record)
            lines.append(_render_backtest_record(record))
        trade_log = BASE_DIR / cfg["reporting"]["trade_log_file"]
        forward_record = evaluate_rsi_forward_gate(_load_trade_rows(trade_log), rsi_cfg)
        records.append(forward_record)
        lines.append(_render_rsi_forward_record(forward_record))
        lines.append("")

    if profile in {"range", "all"}:
        range_cfg = validation_cfg.get("range_breakout_enablement", {})
        lines.append("Range Breakout enablement gates:")
        for gate in range_cfg.get("backtest_windows", []):
            record = evaluate_backtest_gate(gate, cost_model, initial_fund)
            records.append(record)
            lines.append(_render_backtest_record(record))
        lines.append("")

    required_failures = [r for r in records if r["required"] and not r["passed"]]
    warnings = [r for r in records if not r["required"] and not r["passed"]]
    if required_failures:
        lines.append(f"RESULT: FAIL ({len(required_failures)} required gate(s) failed)")
        exit_code = 1
    else:
        warn_text = f", {len(warnings)} watch warning(s)" if warnings else ""
        lines.append(f"RESULT: PASS{warn_text}")
        exit_code = 0

    return exit_code, "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile",
        choices=["production", "rsi", "range", "all"],
        default="production",
        help="Gate profile to run",
    )
    parser.add_argument("--fund", type=float, default=10000.0)
    args = parser.parse_args()
    exit_code, output = run_validation_gate(profile=args.profile, initial_fund=args.fund)
    print(output)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
