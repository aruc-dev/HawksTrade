import unittest
from unittest.mock import patch

from scheduler.run_walkforward import (
    WalkForwardThresholds,
    build_rolling_windows,
    run_walkforward,
    window_failures,
)


def _stats(return_pct=0.04, max_drawdown=-0.02, trades=20, profit_factor=2.0):
    return {
        "return_pct": return_pct,
        "max_drawdown": max_drawdown,
        "trades": trades,
        "win_rate": 0.55,
        "profit_factor": profit_factor,
        "daily_sharpe": 1.2,
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


if __name__ == "__main__":
    unittest.main()
