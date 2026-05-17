import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from scheduler.run_walkforward import (
    WalkForwardThresholds,
    _maybe_file_regression_issue,
    _configured_profile_windows,
    build_rolling_windows,
    get_walkforward_profile,
    profile_window_failures,
    run_walkforward,
    window_failures,
)


def _stats(return_pct=0.04, max_drawdown=-0.02, trades=20, profit_factor=2.0, daily_sharpe=1.2):
    return {
        "return_pct": return_pct,
        "max_drawdown": max_drawdown,
        "trades": trades,
        "win_rate": 0.55,
        "profit_factor": profit_factor,
        "daily_sharpe": daily_sharpe,
    }


def _profile_cfg():
    return {
        "window_days": 180,
        "screener": True,
        "strategies": ["momentum", "gap_up"],
        "windows": [
            {"label": "covid_crash_2020", "end_date": "09/30/2020", "regime": "Crash"},
            {
                "label": "current_regime_auto",
                "end_date": "auto",
                "auto_offset_days": 60,
                "regime": "Current",
            },
        ],
        "cost_levels": [
            {"name": "baseline", "slippage_bps": 7.5, "fee_bps": 5.0},
            {"name": "stressed", "slippage_bps": 15.0, "fee_bps": 5.0},
        ],
        "thresholds": {
            "baseline": {
                "min_return_pct": 0.0,
                "max_drawdown_pct": 0.08,
                "min_profit_factor": 1.0,
                "min_trades": 5,
            },
            "stressed": {
                "min_return_pct": 0.0,
                "max_drawdown_pct": 0.10,
                "min_profit_factor": 1.0,
                "min_trades": 5,
                "min_daily_sharpe": 0.2,
            },
        },
        "pass_rate": {"baseline": 1.0, "stressed": 1.0},
        "blocking_levels": ["stressed"],
        "oos_lock": {
            "label": "locked_oos_recent_60d",
            "end_date": "auto",
            "auto_offset_days": 2,
            "window_days": 60,
            "must_pass_at": "stressed",
            "scale_min_trades_to_window": True,
        },
        "regression_issue": {"auto_file": False},
    }


class WalkForwardTests(unittest.TestCase):
    def test_build_rolling_windows_returns_oldest_to_newest(self):
        windows = build_rolling_windows(end_date="04/30/2026", windows=3, step_days=30)

        self.assertEqual(windows, ["03/01/2026", "03/31/2026", "04/30/2026"])

    def test_window_failures_checks_configured_thresholds(self):
        thresholds = WalkForwardThresholds(
            min_return_pct=0.01,
            max_drawdown_pct=0.05,
            min_profit_factor=1.2,
            min_trades=10,
            min_pass_rate=0.75,
        )

        failures = window_failures(
            _stats(return_pct=-0.02, max_drawdown=-0.07, trades=4, profit_factor=0.9),
            thresholds,
        )

        self.assertEqual(len(failures), 4)

    def test_run_walkforward_passes_and_suppresses_quarterly_csv_output(self):
        cfg = {"validation": {"cost_model": {"slippage_bps": 10, "fee_bps": 5, "min_fee_usd": 0}}}

        with (
            patch("scheduler.run_walkforward.get_config", return_value=cfg),
            patch("scheduler.run_walkforward.run_backtest", return_value={"stats": _stats()}) as run_backtest,
        ):
            exit_code, output = run_walkforward(
                window_days=90,
                step_days=30,
                windows=2,
                end_date="04/30/2026",
                strategies="momentum,ma_crossover",
                thresholds=WalkForwardThresholds(
                    min_return_pct=0.0,
                    max_drawdown_pct=0.06,
                    min_profit_factor=1.0,
                    min_trades=5,
                    min_pass_rate=1.0,
                ),
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("RESULT: PASS", output)
        self.assertEqual(run_backtest.call_count, 2)
        first_call = run_backtest.call_args_list[0].kwargs
        self.assertFalse(first_call["write_quarterly_csv"])
        self.assertEqual(first_call["enabled_strategies"], ["momentum", "ma_crossover"])

    def test_run_walkforward_fails_when_pass_rate_is_low(self):
        cfg = {"validation": {"cost_model": {}}}
        results = [
            {"stats": _stats(return_pct=-0.02, trades=20)},
            {"stats": _stats(return_pct=0.03, trades=20)},
        ]

        with (
            patch("scheduler.run_walkforward.get_config", return_value=cfg),
            patch("scheduler.run_walkforward.run_backtest", side_effect=results),
        ):
            exit_code, output = run_walkforward(
                window_days=90,
                step_days=30,
                windows=2,
                end_date="04/30/2026",
                thresholds=WalkForwardThresholds(
                    min_return_pct=0.0,
                    max_drawdown_pct=0.06,
                    min_profit_factor=1.0,
                    min_trades=5,
                    min_pass_rate=0.75,
                ),
            )

        self.assertEqual(exit_code, 1)
        self.assertIn("RESULT: FAIL", output)
        self.assertIn("return -2.00% < +0.00%", output)

    def test_get_walkforward_profile_rejects_unknown_profile(self):
        cfg = {"validation": {"walkforward": {"profiles": {}}}}

        with self.assertRaisesRegex(ValueError, "Unknown walk-forward profile"):
            get_walkforward_profile(cfg, "missing")

    def test_configured_profile_windows_resolves_auto_and_oos_dates(self):
        today = datetime(2026, 5, 16, tzinfo=timezone.utc)
        windows = _configured_profile_windows(_profile_cfg(), today=today)
        oos = _configured_profile_windows(_profile_cfg(), today=today, oos_only=True)

        self.assertEqual(windows[-1]["end_date"], "03/17/2026")
        self.assertEqual(windows[-1]["window_days"], 180)
        self.assertEqual(oos, [{
            "label": "locked_oos_recent_60d",
            "regime": "Locked out-of-sample",
            "end_date": "05/14/2026",
            "window_days": 60,
            "oos": True,
        }])

    def test_profile_window_failures_uses_annualized_return_and_daily_sharpe(self):
        thresholds = WalkForwardThresholds(
            min_return_pct=0.08,
            max_drawdown_pct=0.10,
            min_profit_factor=1.0,
            min_trades=5,
            min_pass_rate=1.0,
            min_daily_sharpe=0.5,
        )

        stats = _stats(return_pct=0.01, daily_sharpe=0.1)
        failures = profile_window_failures(stats, thresholds, window_days=180)

        self.assertIn("annualized_return", failures[0])
        self.assertIn("daily_sharpe", failures[-1])
        self.assertIn("annualized_return_pct", stats)

    def test_run_profile_iterates_cost_levels_writes_report_and_artifacts(self):
        cfg = {
            "validation": {
                "cost_model": {"slippage_bps": 10, "fee_bps": 5, "min_fee_usd": 0},
                "walkforward": {"enabled": True, "profiles": {"unit": _profile_cfg()}},
            }
        }
        backtest_result = {
            "stats": _stats(return_pct=0.04, trades=12),
            "per_strategy": {
                "momentum": {
                    "trades": 12,
                    "win_rate": 0.5,
                    "avg_pnl_pct": 0.01,
                    "total_pnl": 100.0,
                    "best_pnl_pct": 0.04,
                    "worst_pnl_pct": -0.02,
                    "profit_factor": 2.0,
                }
            },
            "data_coverage": {"missing_history_symbols": ["OLD"]},
        }

        with TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "walkforward.md"
            artifacts_dir = Path(tmpdir) / "artifacts"
            with (
                patch("scheduler.run_walkforward.get_config", return_value=cfg),
                patch("scheduler.run_walkforward.run_backtest", return_value=backtest_result) as run_backtest,
            ):
                exit_code, output = run_walkforward(
                    profile="unit",
                    initial_fund=10000,
                    today=datetime(2026, 5, 16, tzinfo=timezone.utc),
                    write_report=True,
                    report_path=report_path,
                    write_artifacts=True,
                    artifacts_dir=artifacts_dir,
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("Per-Strategy Attribution", output)
            self.assertTrue(report_path.exists())
            self.assertEqual(len(list(artifacts_dir.glob("*.json"))), 4)
            self.assertEqual(run_backtest.call_count, 4)
            first_call = run_backtest.call_args_list[0].kwargs
            self.assertEqual(first_call["enabled_strategies"], ["momentum", "gap_up"])
            self.assertEqual(first_call["cost_model"]["slippage_bps"], 7.5)
            self.assertFalse(first_call["write_quarterly_csv"])

    def test_oos_only_runs_only_binding_cost_level(self):
        cfg = {
            "validation": {
                "cost_model": {"slippage_bps": 10, "fee_bps": 5, "min_fee_usd": 0},
                "walkforward": {"enabled": True, "profiles": {"unit": _profile_cfg()}},
            }
        }

        with (
            patch("scheduler.run_walkforward.get_config", return_value=cfg),
            patch("scheduler.run_walkforward.run_backtest", return_value={"stats": _stats(trades=8)}) as run_backtest,
        ):
            exit_code, output = run_walkforward(
                profile="unit",
                oos_only=True,
                today=datetime(2026, 5, 16, tzinfo=timezone.utc),
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("OOS Walk-Forward Report", output)
        self.assertEqual(run_backtest.call_count, 1)
        self.assertEqual(run_backtest.call_args.kwargs["cost_model"]["slippage_bps"], 15.0)

    def test_oos_scales_min_trades_to_shorter_window(self):
        profile = _profile_cfg()
        profile["thresholds"]["stressed"]["min_trades"] = 25
        cfg = {
            "validation": {
                "cost_model": {"slippage_bps": 10, "fee_bps": 5, "min_fee_usd": 0},
                "walkforward": {"enabled": True, "profiles": {"unit": profile}},
            }
        }

        with (
            patch("scheduler.run_walkforward.get_config", return_value=cfg),
            patch("scheduler.run_walkforward.run_backtest", return_value={"stats": _stats(trades=8)}),
        ):
            exit_code, output = run_walkforward(
                profile="unit",
                oos_only=True,
                today=datetime(2026, 5, 16, tzinfo=timezone.utc),
            )

        self.assertEqual(exit_code, 0)
        self.assertNotIn("trades 8 < 25", output)

    def test_profile_exit_code_uses_blocking_levels_only(self):
        profile = _profile_cfg()
        profile["thresholds"]["baseline"]["min_trades"] = 5
        profile["thresholds"]["stressed"]["min_trades"] = 5
        cfg = {
            "validation": {
                "cost_model": {"slippage_bps": 10, "fee_bps": 5, "min_fee_usd": 0},
                "walkforward": {"enabled": True, "profiles": {"unit": profile}},
            }
        }
        results = [
            {"stats": _stats(trades=1)},
            {"stats": _stats(trades=1)},
            {"stats": _stats(trades=8)},
            {"stats": _stats(trades=8)},
        ]

        with (
            patch("scheduler.run_walkforward.get_config", return_value=cfg),
            patch("scheduler.run_walkforward.run_backtest", side_effect=results),
        ):
            exit_code, output = run_walkforward(
                profile="unit",
                today=datetime(2026, 5, 16, tzinfo=timezone.utc),
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("| baseline | advisory | 0/2 | 0.0% | 100.0% | FAIL |", output)
        self.assertIn("| stressed | blocking | 2/2 | 100.0% | 100.0% | PASS |", output)

    def test_regression_issue_uses_absolute_report_path_outside_repo(self):
        profile = _profile_cfg()
        profile["regression_issue"]["auto_file"] = True
        outside_report = Path("/tmp/hawkstrade-outside-walkforward.md")
        summary = {
            "stressed": {
                "result": False,
                "passed": 0,
                "total": 2,
                "pass_rate": 0.0,
                "required": 1.0,
            }
        }
        completed = Mock(returncode=0, stdout="created issue", stderr="")

        with (
            patch("scheduler.run_walkforward.shutil.which", return_value="/usr/bin/bd"),
            patch("scheduler.run_walkforward.subprocess.run", return_value=completed) as run_bd,
        ):
            output = _maybe_file_regression_issue(
                profile_name="unit",
                summary=summary,
                profile_cfg=profile,
                report_path=outside_report,
                force=None,
            )

        self.assertEqual(output, "created issue")
        body = run_bd.call_args.args[0][4]
        self.assertIn(f"Report: {outside_report}", body)


if __name__ == "__main__":
    unittest.main()
