import json
import tempfile
import unittest
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from scheduler.run_backtest import (
    BacktestSimulator,
    _backtest_execution_notes,
    _format_execution_notes,
    _make_bar_fetcher,
    _momentum_backtest_scan_time,
)
from scripts import validate_backtest_realism as realism
from strategies.momentum import _regular_session_progress


def _frame(periods=6):
    index = pd.date_range("2026-01-02", periods=periods, freq="B", tz=timezone.utc)
    return pd.DataFrame(
        {
            "open": [100 + i for i in range(periods)],
            "high": [101 + i for i in range(periods)],
            "low": [99 + i for i in range(periods)],
            "close": [100.5 + i for i in range(periods)],
            "volume": [1000 + i for i in range(periods)],
        },
        index=index,
    )


class DataQualityTests(unittest.TestCase):
    def test_valid_ohlcv_frame_has_no_findings(self):
        frame = _frame()

        findings = realism.validate_ohlcv_frame("AAPL", frame, expected_index=frame.index)

        self.assertEqual(findings, [])

    def test_data_quality_detects_missing_and_invalid_bars(self):
        expected = _frame()
        bad = expected.drop(expected.index[2]).copy()
        bad.loc[bad.index[0], "high"] = bad.loc[bad.index[0], "low"] - 1
        bad.loc[bad.index[1], "close"] = bad.loc[bad.index[1], "high"] + 1
        bad.loc[bad.index[3], "volume"] = 0

        findings = realism.validate_ohlcv_frame("AAPL", bad, expected_index=expected.index)
        messages = {finding.message for finding in findings}

        self.assertIn("OHLCV frame is missing expected bars", messages)
        self.assertIn("OHLCV frame has high below low", messages)
        self.assertIn("OHLCV frame has close outside high/low range", messages)
        self.assertIn("OHLCV frame has zero or negative volume", messages)

    def test_dataset_quality_reports_expected_symbol_missing_entirely(self):
        expected = _frame()

        findings = realism.validate_ohlcv_dataset(
            {"AAPL": expected},
            expected_indexes={"AAPL": expected.index, "MSFT": expected.index},
        )

        self.assertTrue(
            any(
                finding.symbol == "MSFT"
                and finding.message == "OHLCV frame is empty or missing"
                for finding in findings
            )
        )

    def test_data_quality_warns_on_stale_close_streak(self):
        frame = _frame()
        frame["close"] = 100.0
        frame["high"] = 101.0
        frame["low"] = 99.0

        findings = realism.validate_ohlcv_frame("AAPL", frame, stale_close_bars=5)

        self.assertTrue(any(finding.message == "OHLCV frame has a stale close-price streak" for finding in findings))


class LookaheadParityTests(unittest.TestCase):
    def test_identical_signal_sets_have_no_drift(self):
        baseline = [
            {
                "date": "2026-01-05",
                "strategy": "momentum",
                "symbol": "AAPL",
                "entry_price": 100.0,
                "atr_stop_price": 95.0,
            }
        ]

        findings = realism.compare_signal_sets(baseline, [dict(baseline[0])])

        self.assertEqual(findings, [])

    def test_signal_value_change_is_reported_as_lookahead_drift(self):
        baseline = [
            {
                "date": "2026-01-05",
                "strategy": "momentum",
                "symbol": "AAPL",
                "entry_price": 100.0,
            }
        ]
        replay = [dict(baseline[0], entry_price=101.0)]

        findings = realism.compare_signal_sets(baseline, replay)

        self.assertTrue(any(finding.check == "lookahead_parity" for finding in findings))


class WarmupStabilityTests(unittest.TestCase):
    def test_stable_warmup_values_pass(self):
        findings = realism.check_warmup_stability({"ema_20": {60: 100.0, 120: 100.00001, 240: 100.0}})

        self.assertEqual(findings, [])

    def test_material_warmup_variance_fails(self):
        findings = realism.check_warmup_stability({"ema_20": {60: 98.0, 120: 100.0, 240: 101.0}})

        self.assertTrue(any(finding.message == "Indicator value changed materially across warmup lengths" for finding in findings))


class GapUpIntradayTests(unittest.TestCase):
    def test_gap_up_synthetic_proxy_is_warning_by_default(self):
        status = realism.evaluate_gap_up_intraday_status(
            gap_up_enabled=True,
            synthetic_minute_proxy_used=True,
            real_minute_replay_available=False,
        )

        self.assertEqual(status.severity, realism.WARNING)
        self.assertFalse(status.validated)
        self.assertEqual(status.mode, "daily_open_proxy")

    def test_gap_up_synthetic_proxy_fails_when_real_intraday_required(self):
        status = realism.evaluate_gap_up_intraday_status(
            gap_up_enabled=True,
            synthetic_minute_proxy_used=True,
            real_minute_replay_available=False,
            require_real_intraday=True,
        )

        self.assertEqual(status.severity, realism.ERROR)
        self.assertFalse(status.validated)

    def test_backtest_minute_fetch_marks_synthetic_proxy_usage(self):
        sim = BacktestSimulator(initial_fund=10000.0)
        sim.current_date = _frame().index[-1]
        sim.historical_data = {"AAPL": _frame()}

        fetcher = _make_bar_fetcher(sim)
        bars = fetcher(["AAPL"], timeframe="1Min", limit=10)

        self.assertTrue(sim.synthetic_minute_proxy_used)
        self.assertIn("AAPL", sim.synthetic_minute_proxy_symbols)
        self.assertEqual(len(bars["AAPL"]), 1)

    def test_backtest_minute_fetch_uses_near_close_volume_proxy_for_momentum(self):
        sim = BacktestSimulator(initial_fund=10000.0)
        sim.current_date = _frame().index[-1]
        sim.historical_data = {"AAPL": _frame()}

        fetcher = _make_bar_fetcher(sim)
        bars = fetcher(["AAPL"], timeframe="1Min", limit=390)
        proxy_bar = bars["AAPL"][0]
        proxy_time_et = proxy_bar.timestamp.astimezone(ZoneInfo("America/New_York"))

        self.assertTrue(sim.synthetic_session_volume_proxy_used)
        self.assertIn("AAPL", sim.synthetic_session_volume_proxy_symbols)
        self.assertEqual((proxy_time_et.hour, proxy_time_et.minute), (15, 55))
        self.assertAlmostEqual(proxy_bar.volume, float(_frame()["volume"].iloc[-1]) * 385 / 390)

    def test_backtest_report_notes_call_out_gap_up_daily_proxy(self):
        sim = BacktestSimulator(initial_fund=10000.0)
        sim.synthetic_minute_proxy_used = True
        sim.synthetic_minute_proxy_symbols.add("AAPL")
        cfg = {"strategies": {"gap_up": {"enabled": True}}}

        notes = _backtest_execution_notes(cfg, sim)
        rendered = _format_execution_notes(notes)

        self.assertIn("Gap-Up opening-window backtest uses a synthetic 9:35 ET daily-open proxy", rendered)
        self.assertIn("not intraday-validated", rendered)

    def test_backtest_report_notes_call_out_momentum_volume_proxy(self):
        sim = BacktestSimulator(initial_fund=10000.0)
        sim.synthetic_session_volume_proxy_used = True
        sim.synthetic_session_volume_proxy_symbols.add("AAPL")
        cfg = {"strategies": {"momentum": {"enabled": True}, "gap_up": {"enabled": False}}}

        notes = _backtest_execution_notes(cfg, sim)
        rendered = _format_execution_notes(notes)

        self.assertIn("Momentum volume-pace backtest uses a synthetic elapsed-session volume proxy", rendered)
        self.assertIn("not real minute bars", rendered)

    def test_momentum_backtest_scan_time_is_regular_session_near_close(self):
        scan_time = _momentum_backtest_scan_time(pd.Timestamp("2026-01-05T00:00:00Z"))

        elapsed, in_session = _regular_session_progress(scan_time, 390)

        self.assertTrue(in_session)
        self.assertGreaterEqual(elapsed, 390 * 0.95)


class TradeReplayTests(unittest.TestCase):
    def test_matching_trade_replay_passes(self):
        rows = [{"symbol": "AAPL", "exit_date": "2026-01-05", "exit_price": 101.0, "pnl_pct": 0.01}]

        findings = realism.compare_trade_replay(rows, [dict(rows[0])])

        self.assertEqual(findings, [])

    def test_trade_replay_tolerance_mismatch_fails(self):
        expected = [{"symbol": "AAPL", "exit_date": "2026-01-05", "exit_price": 101.0, "pnl_pct": 0.01}]
        simulated = [dict(expected[0], exit_price=103.0)]

        findings = realism.compare_trade_replay(expected, simulated, price_tolerance_pct=0.001)

        self.assertTrue(any(finding.message == "Replay value exceeded tolerance" for finding in findings))

    def test_trade_replay_detects_quantity_and_dollar_pnl_drift(self):
        expected = [
            {
                "symbol": "AAPL",
                "exit_date": "2026-01-05",
                "exit_price": 101.0,
                "pnl_pct": 0.01,
                "qty": 10.0,
                "pnl": 10.0,
            }
        ]
        simulated = [dict(expected[0], qty=9.0, pnl=9.0)]

        findings = realism.compare_trade_replay(expected, simulated)
        fields = {finding.context["field"] for finding in findings if finding.context}

        self.assertIn("qty", fields)
        self.assertIn("pnl_dollars", fields)


class AcceptanceScriptTests(unittest.TestCase):
    def test_default_acceptance_passes_with_gap_warning(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = realism.run_acceptance(days=30, baseline_file=Path(tmpdir) / "missing.json")

        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["errors"], 0)
        self.assertGreaterEqual(result["warnings"], 1)
        self.assertTrue(
            any(finding["check"] == "backtest_window_replay" for finding in result["findings"])
        )
        self.assertTrue(
            any(finding["check"] == "strategy_lookahead_smoke" for finding in result["findings"])
        )

    def test_acceptance_fails_when_real_gap_up_intraday_is_required(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = realism.run_acceptance(
                days=30,
                baseline_file=Path(tmpdir) / "missing.json",
                require_real_gap_up_intraday=True,
            )

        self.assertEqual(result["status"], "fail")
        self.assertGreater(result["errors"], 0)

    def test_cli_main_returns_success_for_default_gate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            code = realism.main(["--days", "30", "--baseline-file", str(Path(tmpdir) / "missing.json")])

        self.assertEqual(code, 0)

    def test_acceptance_baseline_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            baseline = Path(tmpdir) / "baseline.json"
            baseline.write_text(
                '{"version": 1, "status": "fail", "expected_checks": ["missing_check"]}',
                encoding="utf-8",
            )

            result = realism.run_acceptance(days=30, baseline_file=baseline)

        self.assertEqual(result["status"], "fail")
        self.assertTrue(
            any(finding["check"] == "acceptance_baseline" for finding in result["findings"])
        )

    def test_write_baseline_skips_existing_baseline_comparison(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            baseline = Path(tmpdir) / "baseline.json"
            baseline.write_text(
                '{"version": 1, "status": "fail", "expected_checks": ["missing_check"]}',
                encoding="utf-8",
            )

            code = realism.main(["--days", "30", "--baseline-file", str(baseline), "--write-baseline"])
            written = json.loads(baseline.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(written["status"], "pass")
        self.assertEqual(written["errors"], 0)
        self.assertGreaterEqual(written["warnings"], 1)
        self.assertIn("gap_up_intraday", written)


if __name__ == "__main__":
    unittest.main()
