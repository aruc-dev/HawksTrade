#!/usr/bin/env python3
"""
Backtest realism acceptance checks.

This script is intentionally deterministic by default. It validates the
correctness guardrails that future backtest work must preserve: market-data
quality, signal parity between full-history and walk-forward views, indicator
warmup stability, Gap-Up intraday-data visibility, and simple broker replay
tolerance checks.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
DEFAULT_BASELINE_FILE = BASE_DIR / "data" / "backtest_acceptance_baselines.json"

ERROR = "error"
WARNING = "warning"
INFO = "info"
REQUIRED_OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")
ACCEPTANCE_CHECKS = (
    "data_quality_self_test",
    "lookahead_self_test",
    "warmup_self_test",
    "trade_replay_self_test",
    "backtest_window_replay",
    "gap_up_intraday",
)


@dataclass(frozen=True)
class RealismFinding:
    check: str
    severity: str
    message: str
    symbol: str = ""
    context: dict | None = None


@dataclass(frozen=True)
class GapUpIntradayStatus:
    mode: str
    validated: bool
    severity: str
    message: str


def _finding(check: str, severity: str, message: str, symbol: str = "", **context) -> RealismFinding:
    return RealismFinding(
        check=check,
        severity=severity,
        message=message,
        symbol=symbol,
        context=context or None,
    )


def validate_ohlcv_frame(
    symbol: str,
    frame: pd.DataFrame,
    *,
    expected_index: Iterable | None = None,
    stale_close_bars: int = 5,
) -> list[RealismFinding]:
    """Validate that a historical OHLCV frame can support honest backtests."""
    findings: list[RealismFinding] = []
    check = "data_quality"

    if frame is None or frame.empty:
        return [_finding(check, ERROR, "OHLCV frame is empty or missing", symbol)]

    missing_columns = [column for column in REQUIRED_OHLCV_COLUMNS if column not in frame.columns]
    if missing_columns:
        return [
            _finding(
                check,
                ERROR,
                "OHLCV frame is missing required columns",
                symbol,
                missing_columns=missing_columns,
            )
        ]

    if frame.index.has_duplicates:
        findings.append(_finding(check, ERROR, "OHLCV frame has duplicate timestamps", symbol))

    if not frame.index.is_monotonic_increasing:
        findings.append(_finding(check, WARNING, "OHLCV frame timestamps are not sorted", symbol))

    if expected_index is not None:
        expected = pd.Index(expected_index)
        missing_timestamps = expected.difference(frame.index)
        if len(missing_timestamps) > 0:
            findings.append(
                _finding(
                    check,
                    ERROR,
                    "OHLCV frame is missing expected bars",
                    symbol,
                    missing_count=int(len(missing_timestamps)),
                    first_missing=str(missing_timestamps[0]),
                )
            )

    numeric = frame.loc[:, REQUIRED_OHLCV_COLUMNS].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any():
        bad_columns = sorted(column for column in REQUIRED_OHLCV_COLUMNS if numeric[column].isna().any())
        findings.append(
            _finding(check, ERROR, "OHLCV frame has null or non-numeric values", symbol, columns=bad_columns)
        )
        numeric = numeric.dropna()

    if numeric.empty:
        return findings

    price_columns = ["open", "high", "low", "close"]
    non_positive_prices = (numeric[price_columns] <= 0).any(axis=1)
    if bool(non_positive_prices.any()):
        findings.append(
            _finding(
                check,
                ERROR,
                "OHLCV frame has non-positive price values",
                symbol,
                count=int(non_positive_prices.sum()),
            )
        )

    non_positive_volume = numeric["volume"] <= 0
    if bool(non_positive_volume.any()):
        findings.append(
            _finding(
                check,
                ERROR,
                "OHLCV frame has zero or negative volume",
                symbol,
                count=int(non_positive_volume.sum()),
            )
        )

    high_below_low = numeric["high"] < numeric["low"]
    if bool(high_below_low.any()):
        findings.append(
            _finding(check, ERROR, "OHLCV frame has high below low", symbol, count=int(high_below_low.sum()))
        )

    open_outside_range = (numeric["open"] > numeric["high"]) | (numeric["open"] < numeric["low"])
    if bool(open_outside_range.any()):
        findings.append(
            _finding(
                check,
                ERROR,
                "OHLCV frame has open outside high/low range",
                symbol,
                count=int(open_outside_range.sum()),
            )
        )

    close_outside_range = (numeric["close"] > numeric["high"]) | (numeric["close"] < numeric["low"])
    if bool(close_outside_range.any()):
        findings.append(
            _finding(
                check,
                ERROR,
                "OHLCV frame has close outside high/low range",
                symbol,
                count=int(close_outside_range.sum()),
            )
        )

    if stale_close_bars > 1 and len(numeric) >= stale_close_bars:
        close = numeric["close"]
        streak_ids = close.ne(close.shift()).cumsum()
        longest_streak = int(close.groupby(streak_ids).size().max())
        if longest_streak >= stale_close_bars:
            findings.append(
                _finding(
                    check,
                    WARNING,
                    "OHLCV frame has a stale close-price streak",
                    symbol,
                    longest_streak=longest_streak,
                )
            )

    return findings


def validate_ohlcv_dataset(
    frames: dict[str, pd.DataFrame],
    *,
    expected_indexes: dict[str, Iterable] | None = None,
    stale_close_bars: int = 5,
) -> list[RealismFinding]:
    frames = frames or {}
    findings: list[RealismFinding] = []
    expected_symbols = set(expected_indexes or {})
    symbols = sorted(set(frames) | expected_symbols)
    for symbol in symbols:
        frame = frames.get(symbol)
        expected_index = expected_indexes.get(symbol) if expected_indexes else None
        findings.extend(
            validate_ohlcv_frame(
                symbol,
                frame,
                expected_index=expected_index,
                stale_close_bars=stale_close_bars,
            )
        )
    return findings


def _signal_identity(signal: dict) -> tuple:
    return (
        str(signal.get("date", "")),
        str(signal.get("strategy", "")),
        str(signal.get("symbol", "")),
        str(signal.get("side", "buy")),
    )


def _coerce_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def compare_signal_sets(
    baseline_signals: Iterable[dict],
    replay_signals: Iterable[dict],
    *,
    numeric_fields: tuple[str, ...] = ("entry_price", "atr_stop_price", "atr_risk_qty"),
    tolerance: float = 1e-9,
) -> list[RealismFinding]:
    """Compare full-history signals with walk-forward replay signals."""
    check = "lookahead_parity"
    baseline = {_signal_identity(signal): signal for signal in baseline_signals}
    replay = {_signal_identity(signal): signal for signal in replay_signals}
    findings: list[RealismFinding] = []

    for key in sorted(set(baseline) - set(replay)):
        findings.append(_finding(check, ERROR, "Walk-forward replay is missing a baseline signal", context_key=key))

    for key in sorted(set(replay) - set(baseline)):
        findings.append(_finding(check, ERROR, "Walk-forward replay emitted an extra signal", context_key=key))

    for key in sorted(set(baseline) & set(replay)):
        left = baseline[key]
        right = replay[key]
        for field in numeric_fields:
            left_value = _coerce_float(left.get(field))
            right_value = _coerce_float(right.get(field))
            if left_value is None and right_value is None:
                continue
            if left_value is None or right_value is None:
                findings.append(
                    _finding(check, ERROR, "Signal numeric field presence changed", field=field, context_key=key)
                )
                continue
            if abs(left_value - right_value) > tolerance:
                findings.append(
                    _finding(
                        check,
                        ERROR,
                        "Signal numeric field changed between full-history and walk-forward views",
                        field=field,
                        baseline=left_value,
                        replay=right_value,
                        context_key=key,
                    )
                )

    return findings


def check_warmup_stability(
    indicator_values: dict[str, dict[int, float]],
    *,
    tolerance_pct: float = 0.001,
    absolute_tolerance: float = 1e-9,
) -> list[RealismFinding]:
    """Ensure indicator outputs are stable across warmup lengths."""
    check = "warmup_stability"
    findings: list[RealismFinding] = []
    for indicator, values_by_warmup in indicator_values.items():
        if len(values_by_warmup) < 2:
            findings.append(
                _finding(check, WARNING, "Indicator has fewer than two warmup samples", indicator=indicator)
            )
            continue

        sorted_items = sorted(values_by_warmup.items())
        benchmark_warmup, benchmark_value = sorted_items[-1]
        benchmark_value = float(benchmark_value)
        for warmup, value in sorted_items[:-1]:
            value = float(value)
            diff = abs(value - benchmark_value)
            denominator = abs(benchmark_value) if abs(benchmark_value) > absolute_tolerance else 1.0
            pct_diff = diff / denominator
            if diff > absolute_tolerance and pct_diff > tolerance_pct:
                findings.append(
                    _finding(
                        check,
                        ERROR,
                        "Indicator value changed materially across warmup lengths",
                        indicator=indicator,
                        warmup=warmup,
                        benchmark_warmup=benchmark_warmup,
                        value=value,
                        benchmark=benchmark_value,
                        pct_diff=pct_diff,
                    )
                )
    return findings


def evaluate_gap_up_intraday_status(
    *,
    gap_up_enabled: bool,
    synthetic_minute_proxy_used: bool,
    real_minute_replay_available: bool,
    require_real_intraday: bool = False,
) -> GapUpIntradayStatus:
    if not gap_up_enabled:
        return GapUpIntradayStatus(
            mode="disabled",
            validated=True,
            severity=INFO,
            message="Gap-Up is disabled for this acceptance run.",
        )

    if real_minute_replay_available:
        return GapUpIntradayStatus(
            mode="real_minute_replay",
            validated=True,
            severity=INFO,
            message="Gap-Up opening-window logic is backed by real minute bars.",
        )

    if synthetic_minute_proxy_used:
        severity = ERROR if require_real_intraday else WARNING
        return GapUpIntradayStatus(
            mode="daily_open_proxy",
            validated=False,
            severity=severity,
            message=(
                "Gap-Up opening-window logic used a synthetic 9:35 ET daily-open proxy; "
                "Gap-Up fills are not intraday-validated."
            ),
        )

    severity = ERROR if require_real_intraday else WARNING
    return GapUpIntradayStatus(
        mode="no_minute_replay",
        validated=False,
        severity=severity,
        message="Gap-Up is enabled but no real minute replay data was available.",
    )


def compare_trade_replay(
    expected_rows: Iterable[dict],
    simulated_rows: Iterable[dict],
    *,
    price_tolerance_pct: float = 0.001,
    pnl_tolerance_pct: float = 0.001,
) -> list[RealismFinding]:
    """Compare broker/paper rows with simulated rows for replay acceptance."""
    check = "trade_replay"
    expected = {
        (str(row.get("symbol", "")), str(row.get("exit_date", row.get("timestamp", "")))): row
        for row in expected_rows
    }
    simulated = {
        (str(row.get("symbol", "")), str(row.get("exit_date", row.get("timestamp", "")))): row
        for row in simulated_rows
    }
    findings: list[RealismFinding] = []

    for key in sorted(set(expected) - set(simulated)):
        findings.append(_finding(check, ERROR, "Replay is missing expected broker trade", context_key=key))
    for key in sorted(set(simulated) - set(expected)):
        findings.append(_finding(check, ERROR, "Replay emitted unexpected simulated trade", context_key=key))

    for key in sorted(set(expected) & set(simulated)):
        left = expected[key]
        right = simulated[key]
        for field, tolerance_pct in (("exit_price", price_tolerance_pct), ("pnl_pct", pnl_tolerance_pct)):
            left_value = _coerce_float(left.get(field))
            right_value = _coerce_float(right.get(field))
            if left_value is None and right_value is None:
                continue
            if left_value is None or right_value is None:
                findings.append(_finding(check, ERROR, "Replay field presence changed", field=field, context_key=key))
                continue
            denominator = abs(left_value) if abs(left_value) > 1e-12 else 1.0
            if abs(left_value - right_value) / denominator > tolerance_pct:
                findings.append(
                    _finding(
                        check,
                        ERROR,
                        "Replay value exceeded tolerance",
                        field=field,
                        expected=left_value,
                        simulated=right_value,
                        tolerance_pct=tolerance_pct,
                        context_key=key,
                    )
                )

    return findings


def _fixture_ohlcv_frame() -> pd.DataFrame:
    index = pd.date_range("2026-01-02", periods=8, freq="B", tz="UTC")
    return pd.DataFrame(
        {
            "open": [100, 101, 102, 103, 104, 105, 106, 107],
            "high": [101, 102, 103, 104, 105, 106, 107, 108],
            "low": [99, 100, 101, 102, 103, 104, 105, 106],
            "close": [100.5, 101.5, 102.5, 103.5, 104.5, 105.5, 106.5, 107.5],
            "volume": [1000, 1100, 1200, 1300, 1400, 1500, 1600, 1700],
        },
        index=index,
    )


def _self_test_data_quality() -> list[RealismFinding]:
    good = _fixture_ohlcv_frame()
    if validate_ohlcv_frame("GOOD", good, expected_index=good.index):
        return [_finding("data_quality_self_test", ERROR, "Valid fixture unexpectedly failed data quality checks")]

    bad = good.drop(good.index[3]).copy()
    bad.loc[bad.index[0], "high"] = bad.loc[bad.index[0], "low"] - 1
    bad.loc[bad.index[1], "close"] = bad.loc[bad.index[1], "high"] + 1
    bad.loc[bad.index[2], "volume"] = 0
    detected = validate_ohlcv_frame("BAD", bad, expected_index=good.index)
    detected_messages = {finding.message for finding in detected}
    expected_messages = {
        "OHLCV frame is missing expected bars",
        "OHLCV frame has high below low",
        "OHLCV frame has close outside high/low range",
        "OHLCV frame has zero or negative volume",
    }
    missing = sorted(expected_messages - detected_messages)
    if missing:
        return [
            _finding(
                "data_quality_self_test",
                ERROR,
                "Invalid fixture did not trigger all expected data-quality findings",
                missing_messages=missing,
            )
        ]
    return []


def _self_test_lookahead() -> list[RealismFinding]:
    baseline = [
        {
            "date": "2026-01-05",
            "strategy": "momentum",
            "symbol": "AAPL",
            "side": "buy",
            "entry_price": 100.0,
            "atr_stop_price": 95.0,
            "atr_risk_qty": 20.0,
        }
    ]
    replay = [dict(baseline[0])]
    if compare_signal_sets(baseline, replay):
        return [_finding("lookahead_self_test", ERROR, "Identical signal fixtures unexpectedly drifted")]

    biased_replay = [dict(baseline[0], entry_price=101.0)]
    if not compare_signal_sets(baseline, biased_replay):
        return [_finding("lookahead_self_test", ERROR, "Signal drift fixture was not detected")]
    return []


def _self_test_warmup() -> list[RealismFinding]:
    stable = {"ema_20": {60: 101.0, 120: 101.00001, 240: 101.0}}
    if check_warmup_stability(stable, tolerance_pct=0.001):
        return [_finding("warmup_self_test", ERROR, "Stable warmup fixture unexpectedly failed")]

    unstable = {"ema_20": {60: 98.0, 120: 100.0, 240: 101.0}}
    if not check_warmup_stability(unstable, tolerance_pct=0.001):
        return [_finding("warmup_self_test", ERROR, "Unstable warmup fixture was not detected")]
    return []


def _self_test_replay() -> list[RealismFinding]:
    broker = [{"symbol": "AAPL", "exit_date": "2026-01-05", "exit_price": 101.0, "pnl_pct": 0.01}]
    simulated = [dict(broker[0])]
    if compare_trade_replay(broker, simulated):
        return [_finding("trade_replay_self_test", ERROR, "Identical replay fixture unexpectedly failed")]

    mismatched = [dict(broker[0], exit_price=103.0)]
    if not compare_trade_replay(broker, mismatched, price_tolerance_pct=0.001):
        return [_finding("trade_replay_self_test", ERROR, "Replay mismatch fixture was not detected")]
    return []


def _self_test_backtest_window(days: int) -> list[RealismFinding]:
    check = "backtest_window_replay"
    if days <= 0:
        return [_finding(check, ERROR, "Acceptance window must be positive", days=days)]
    try:
        from scheduler.run_backtest import BacktestSimulator
    except Exception as exc:
        return [_finding(check, ERROR, "Could not import backtest simulator", error=str(exc))]

    periods = max(2, min(int(days), 60))
    frame = _fixture_ohlcv_frame()
    if periods > len(frame):
        extension = pd.date_range(
            frame.index[-1] + pd.tseries.offsets.BDay(),
            periods=periods - len(frame),
            freq="B",
            tz="UTC",
        )
        last_close = float(frame["close"].iloc[-1])
        extra = pd.DataFrame(
            {
                "open": [last_close + i for i in range(1, len(extension) + 1)],
                "high": [last_close + i + 1 for i in range(1, len(extension) + 1)],
                "low": [last_close + i - 1 for i in range(1, len(extension) + 1)],
                "close": [last_close + i + 0.5 for i in range(1, len(extension) + 1)],
                "volume": [1800 + i * 100 for i in range(len(extension))],
            },
            index=extension,
        )
        frame = pd.concat([frame, extra])
    frame = frame.tail(periods)

    sim = BacktestSimulator(initial_fund=10000.0)
    sim.historical_data = {"AAPL": frame}
    sim.current_date = frame.index[0]
    sim.submit_order(SimpleNamespace(symbol="AAPL", side=SimpleNamespace(value="buy"), qty=1, strategy="acceptance"))
    if "AAPL" not in sim.positions:
        return [_finding(check, ERROR, "Backtest simulator did not open fixture position")]
    sim.current_date = frame.index[-1]
    sim.submit_order(SimpleNamespace(symbol="AAPL", side=SimpleNamespace(value="sell"), qty=1, strategy="acceptance"))
    if not sim.trades_log:
        return [_finding(check, ERROR, "Backtest simulator did not close fixture position")]
    replay_findings = compare_trade_replay(sim.trades_log, [dict(sim.trades_log[0])])
    if replay_findings:
        return [
            _finding(
                check,
                ERROR,
                "Backtest fixture trade failed replay comparison",
                replay_findings=[asdict(finding) for finding in replay_findings],
            )
        ]
    return [_finding(check, INFO, "Backtest simulator replayed acceptance window", days=days, periods=periods)]


def load_baseline(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def write_baseline(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _acceptance_result(
    *,
    days: int,
    baseline_file: Path,
    baseline_loaded: bool,
    gap_status: GapUpIntradayStatus,
    findings: list[RealismFinding],
) -> dict:
    error_count = sum(1 for finding in findings if finding.severity == ERROR)
    warning_count = sum(1 for finding in findings if finding.severity == WARNING)
    return {
        "status": "fail" if error_count else "pass",
        "days": days,
        "baseline_file": str(baseline_file),
        "baseline_loaded": baseline_loaded,
        "errors": error_count,
        "warnings": warning_count,
        "executed_checks": list(ACCEPTANCE_CHECKS),
        "gap_up_intraday": asdict(gap_status),
        "findings": [asdict(finding) for finding in findings],
    }


def compare_acceptance_baseline(result: dict, baseline: dict) -> list[RealismFinding]:
    if not baseline:
        return []
    findings: list[RealismFinding] = []
    check = "acceptance_baseline"

    expected_status = baseline.get("status")
    if expected_status is not None and result.get("status") != expected_status:
        findings.append(
            _finding(
                check,
                ERROR,
                "Acceptance status differs from baseline",
                expected=expected_status,
                actual=result.get("status"),
            )
        )

    for count_field in ("errors", "warnings"):
        if count_field in baseline and result.get(count_field) != baseline.get(count_field):
            findings.append(
                _finding(
                    check,
                    ERROR,
                    f"Acceptance {count_field} count differs from baseline",
                    expected=baseline.get(count_field),
                    actual=result.get(count_field),
                )
            )

    expected_gap = baseline.get("gap_up_intraday")
    actual_gap = result.get("gap_up_intraday", {})
    if isinstance(expected_gap, dict):
        for field in ("mode", "validated", "severity"):
            if field in expected_gap and actual_gap.get(field) != expected_gap.get(field):
                findings.append(
                    _finding(
                        check,
                        ERROR,
                        "Gap-Up intraday acceptance differs from baseline",
                        field=field,
                        expected=expected_gap.get(field),
                        actual=actual_gap.get(field),
                    )
                )

    expected_checks = set(baseline.get("expected_checks") or [])
    if expected_checks:
        actual_checks = set(result.get("executed_checks") or [])
        actual_checks.update(finding.get("check") for finding in result.get("findings", []))
        missing_checks = sorted(expected_checks - actual_checks)
        if missing_checks:
            findings.append(
                _finding(
                    check,
                    ERROR,
                    "Acceptance checks missing from current run",
                    missing_checks=missing_checks,
                )
            )
    return findings


def run_acceptance(
    *,
    days: int,
    baseline_file: Path = DEFAULT_BASELINE_FILE,
    require_real_gap_up_intraday: bool = False,
) -> dict:
    findings: list[RealismFinding] = []
    findings.extend(_self_test_data_quality())
    findings.extend(_self_test_lookahead())
    findings.extend(_self_test_warmup())
    findings.extend(_self_test_replay())
    findings.extend(_self_test_backtest_window(days))

    gap_status = evaluate_gap_up_intraday_status(
        gap_up_enabled=True,
        synthetic_minute_proxy_used=True,
        real_minute_replay_available=False,
        require_real_intraday=require_real_gap_up_intraday,
    )
    findings.append(
        _finding(
            "gap_up_intraday",
            gap_status.severity,
            gap_status.message,
            mode=gap_status.mode,
            validated=gap_status.validated,
        )
    )

    baseline = load_baseline(baseline_file)
    result = _acceptance_result(
        days=days,
        baseline_file=baseline_file,
        baseline_loaded=bool(baseline),
        gap_status=gap_status,
        findings=findings,
    )
    findings.extend(compare_acceptance_baseline(result, baseline))
    return _acceptance_result(
        days=days,
        baseline_file=baseline_file,
        baseline_loaded=bool(baseline),
        gap_status=gap_status,
        findings=findings,
    )


def _print_text_report(result: dict) -> None:
    print(f"Backtest realism acceptance: {result['status'].upper()} ({result['days']} days)")
    print(f"Baseline file: {result['baseline_file']} ({'loaded' if result['baseline_loaded'] else 'not present'})")
    print(f"Errors: {result['errors']} | Warnings: {result['warnings']}")
    for finding in result["findings"]:
        prefix = finding["severity"].upper()
        context = finding.get("context") or {}
        context_text = f" | context={context}" if context else ""
        print(f"- [{prefix}] {finding['check']}: {finding['message']}{context_text}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic backtest realism acceptance checks.")
    parser.add_argument("--days", type=int, default=30, help="Acceptance window label for reporting.")
    parser.add_argument("--baseline-file", type=Path, default=DEFAULT_BASELINE_FILE)
    parser.add_argument("--write-baseline", action="store_true", help="Write the current acceptance summary baseline.")
    parser.add_argument(
        "--require-real-gap-up-intraday",
        action="store_true",
        help="Fail if Gap-Up acceptance does not use real minute replay data.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    args = parser.parse_args(argv)

    result = run_acceptance(
        days=args.days,
        baseline_file=args.baseline_file,
        require_real_gap_up_intraday=args.require_real_gap_up_intraday,
    )

    if args.write_baseline:
        write_baseline(
            args.baseline_file,
            {
                "version": 1,
                "status": result["status"],
                "gap_up_intraday": result["gap_up_intraday"],
                "expected_checks": [
                    *ACCEPTANCE_CHECKS,
                ],
            },
        )

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        _print_text_report(result)

    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
