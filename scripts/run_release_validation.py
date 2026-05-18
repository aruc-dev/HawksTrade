#!/usr/bin/env python3
"""
Run the first-release validation gate bundle.

This is intentionally a thin command runner. It keeps release validation
repeatable for local/EC2 runs and for the manual GitHub Actions workflow.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.data_lockup import current_lockup  # noqa: E402


@dataclass(frozen=True)
class ValidationGate:
    name: str
    command: tuple[str, ...]


def _python() -> str:
    return sys.executable or "python3"


def _auto_backtest_end_date() -> str | None:
    lockup = current_lockup()
    if lockup is None:
        return None
    return (lockup.start_date - timedelta(days=1)).strftime("%m/%d/%Y")


def _resolved_backtest_end_date(value: str | None) -> str | None:
    if value is None or value == "" or value.lower() == "none":
        return None
    if value.lower() == "auto":
        return _auto_backtest_end_date()
    return value


def build_release_validation_plan(args: argparse.Namespace) -> list[ValidationGate]:
    py = _python()
    gates = [
        ValidationGate("unit tests", (py, "-m", "unittest", "discover", "-v")),
        ValidationGate(
            "deprecation strict unit tests",
            (py, "-W", "error::DeprecationWarning", "-m", "unittest", "discover"),
        ),
        ValidationGate(
            "compileall",
            (
                py,
                "-m",
                "compileall",
                "analysis",
                "core",
                "strategies",
                "scheduler",
                "tracking",
                "tests",
                "scripts",
                "screener",
            ),
        ),
        ValidationGate("OOS lockup leakage", (py, "scripts/check_oos_lockup_leakage.py")),
        ValidationGate(
            "production validation gate",
            (py, "scheduler/run_validation_gate.py", "--profile", "production"),
        ),
    ]

    backtest_cmd = [
        py,
        "scheduler/run_backtest.py",
        "--days",
        str(args.backtest_days),
        "--fund",
        str(args.fund),
        "--no-quarterly-output",
    ]
    backtest_end_date = _resolved_backtest_end_date(args.backtest_end_date)
    if backtest_end_date:
        backtest_cmd.extend(["--end-date", backtest_end_date])
    gates.append(ValidationGate("30-day backtest", tuple(backtest_cmd)))

    if not args.skip_operational:
        gates.extend(
            [
                ValidationGate("scan dry-run", (py, "scheduler/run_scan.py", "--dry-run")),
                ValidationGate("risk-check dry-run", (py, "scheduler/run_risk_check.py", "--dry-run")),
                ValidationGate("daily report", (py, "scheduler/run_report.py")),
            ]
        )

    if args.include_ec2_health:
        gates.extend(
            [
                ValidationGate("systemd deployment check", ("bash", "scripts/check_systemd.sh")),
                ValidationGate("linux health check", (py, "scripts/check_health_linux.py", "--hours", "8")),
            ]
        )

    return gates


def run_validation_plan(
    gates: Sequence[ValidationGate],
    *,
    cwd: Path = ROOT,
    dry_run: bool = False,
) -> int:
    for index, gate in enumerate(gates, start=1):
        command_text = " ".join(gate.command)
        print(f"\n[{index}/{len(gates)}] {gate.name}: {command_text}", flush=True)
        if dry_run:
            continue
        completed = subprocess.run(gate.command, cwd=cwd)
        if completed.returncode != 0:
            print(f"\nFAILED: {gate.name} exited {completed.returncode}", file=sys.stderr, flush=True)
            return completed.returncode
    print("\nRelease validation passed.", flush=True)
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run HawksTrade release validation gates.")
    parser.add_argument("--fund", type=float, default=10000.0)
    parser.add_argument("--backtest-days", type=int, default=30)
    parser.add_argument(
        "--backtest-end-date",
        default="auto",
        help="Backtest end date in MM/DD/YYYY, 'auto' for last pre-lockup date, or 'none'.",
    )
    parser.add_argument(
        "--skip-operational",
        action="store_true",
        help="Skip Alpaca-dependent dry-run scan, risk check, and report gates.",
    )
    parser.add_argument(
        "--include-ec2-health",
        action="store_true",
        help="Also run systemd and Linux health checks on an EC2 deployment host.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print planned gates without executing them.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    return run_validation_plan(build_release_validation_plan(args), dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
