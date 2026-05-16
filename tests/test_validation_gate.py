import math
import unittest
from unittest.mock import patch

from scheduler.run_validation_gate import (
    evaluate_slippage_sensitivity_gate,
    evaluate_rsi_forward_gate,
    reliability_warnings,
    run_validation_gate,
    threshold_failures,
)


class ValidationGateTests(unittest.TestCase):
    def test_threshold_failures_detects_return_and_drawdown(self):
        stats = {
            "return_pct": -0.01,
            "max_drawdown": -0.06,
            "profit_factor": 0.8,
            "daily_sharpe": 0.1,
            "trades": 4,
            "win_rate": 0.25,
        }
        gate = {
            "min_return_pct": 0.0,
            "max_drawdown_pct": 0.04,
            "min_profit_factor": 1.0,
            "min_daily_sharpe": 0.5,
            "min_trades": 5,
            "min_win_rate": 0.4,
        }

        failures = threshold_failures(stats, gate)

        self.assertEqual(len(failures), 6)
        self.assertTrue(any("return" in failure for failure in failures))
        self.assertTrue(any("drawdown" in failure for failure in failures))

    def test_threshold_failures_accepts_infinite_profit_factor(self):
        stats = {
            "return_pct": 0.05,
            "max_drawdown": -0.01,
            "profit_factor": math.inf,
            "daily_sharpe": 1.0,
            "trades": 10,
            "win_rate": 0.7,
        }

        self.assertEqual(threshold_failures(stats, {"min_profit_factor": 2.0}), [])

    def test_reliability_warnings_flag_low_trade_count_without_failure(self):
        warnings = reliability_warnings({"trades": 12}, min_reliable_trades=30)

        self.assertEqual(len(warnings), 1)
        self.assertIn("trades 12 < reliability floor 30", warnings[0])
        self.assertEqual(reliability_warnings({"trades": 30}, min_reliable_trades=30), [])

    def test_slippage_sensitivity_gate_uses_stressed_slippage_and_soft_return_floor(self):
        gate = {"name": "default_12m_costed", "days": 365}
        cost_model = {
            "slippage_bps": 10.0,
            "fee_bps": 5.0,
            "sensitivity_soft_min_return_pct": 0.08,
        }
        result_payload = {
            "stats": {
                "return_pct": 0.05,
                "max_drawdown": -0.01,
                "profit_factor": 1.5,
                "daily_sharpe": 1.0,
                "trades": 20,
                "win_rate": 0.6,
            },
        }

        with patch("scheduler.run_validation_gate.run_backtest", return_value=result_payload) as run_backtest:
            record = evaluate_slippage_sensitivity_gate(gate, cost_model, 10000, 30.0)

        self.assertFalse(record["required"])
        self.assertFalse(record["passed"])
        self.assertIn("sensitivity floor", record["failures"][0])
        self.assertEqual(run_backtest.call_args.kwargs["cost_model"]["slippage_bps"], 30.0)

    def test_slippage_sensitivity_gate_adds_reliability_warning(self):
        gate = {"name": "default_12m_costed", "days": 365}
        result_payload = {
            "stats": {
                "return_pct": 0.10,
                "max_drawdown": -0.01,
                "profit_factor": 1.5,
                "daily_sharpe": 1.0,
                "trades": 4,
                "win_rate": 0.6,
            },
        }

        with patch("scheduler.run_validation_gate.run_backtest", return_value=result_payload):
            record = evaluate_slippage_sensitivity_gate(
                gate,
                {},
                10000,
                30.0,
                min_reliable_trades=30,
            )

        self.assertTrue(record["passed"])
        self.assertIn("reliability floor 30", record["warnings"][0])

    def test_rsi_forward_gate_requires_paper_history(self):
        criteria = {
            "required_paper_days": 60,
            "min_closed_trades": 20,
            "min_win_rate": 0.48,
            "min_profit_factor": 1.15,
            "min_total_return_pct": 0.02,
            "max_drawdown_pct": 0.04,
        }

        result = evaluate_rsi_forward_gate([], criteria)

        self.assertFalse(result["passed"])
        self.assertIn("paper_days 0 < 60", result["failures"])
        self.assertIn("closed_trades 0 < 20", result["failures"])

    def test_rsi_forward_gate_passes_valid_history(self):
        rows = []
        for day in range(1, 61):
            rows.append({
                "timestamp": f"2026-01-{((day - 1) % 28) + 1:02d}T00:00:00+00:00",
                "strategy": "rsi_reversion",
                "status": "closed",
                "side": "sell",
                "pnl_pct": "0.01" if day % 3 else "-0.002",
            })
        rows[-1]["timestamp"] = "2026-03-01T00:00:00+00:00"
        criteria = {
            "required_paper_days": 60,
            "min_closed_trades": 20,
            "min_win_rate": 0.48,
            "min_profit_factor": 1.15,
            "min_total_return_pct": 0.02,
            "max_drawdown_pct": 0.04,
        }

        result = evaluate_rsi_forward_gate(rows, criteria)

        self.assertTrue(result["passed"])
        self.assertEqual(result["stats"]["closed_trades"], 60)

    def test_rsi_forward_gate_counts_only_closed_sell_rows(self):
        rows = [
            {
                "timestamp": "2026-01-01T00:00:00+00:00",
                "strategy": "rsi_reversion",
                "status": "closed",
                "side": "buy",
                "pnl_pct": "0.10",
            },
            {
                "timestamp": "2026-01-02T00:00:00+00:00",
                "strategy": "rsi_reversion",
                "status": "closed",
                "side": "sell",
                "pnl_pct": "-0.02",
            },
        ]
        criteria = {
            "required_paper_days": 0,
            "min_closed_trades": 0,
            "min_win_rate": 0.0,
            "min_profit_factor": 0.0,
            "min_total_return_pct": -1.0,
            "max_drawdown_pct": 1.0,
        }

        result = evaluate_rsi_forward_gate(rows, criteria)

        self.assertEqual(result["stats"]["closed_trades"], 1)
        self.assertEqual(result["stats"]["total_return_pct"], -0.02)

    def test_range_profile_runs_configured_backtest_gates(self):
        cfg = {
            "validation": {
                "cost_model": {},
                "range_breakout_enablement": {
                    "backtest_windows": [
                        {
                            "name": "range_breakout_12m_costed",
                            "days": 365,
                            "strategies": ["range_breakout"],
                            "required": True,
                        }
                    ]
                },
            },
        }
        record = {
            "name": "range_breakout_12m_costed",
            "required": True,
            "passed": True,
            "failures": [],
            "stats": {
                "return_pct": 0.05,
                "max_drawdown": -0.01,
                "trades": 12,
                "win_rate": 0.55,
                "profit_factor": 2.0,
                "daily_sharpe": 1.2,
            },
        }

        with (
            patch("scheduler.run_validation_gate.get_config", return_value=cfg),
            patch("scheduler.run_validation_gate.evaluate_backtest_gate", return_value=record) as gate,
        ):
            exit_code, output = run_validation_gate(profile="range")

        self.assertEqual(exit_code, 0)
        self.assertIn("Range Breakout enablement gates:", output)
        gate.assert_called_once()

    def test_production_profile_adds_slippage_sensitivity_only_when_requested(self):
        cfg = {
            "validation": {
                "cost_model": {
                    "sensitivity_levels_bps": [10, 30],
                    "sensitivity_soft_min_return_pct": 0.08,
                },
                "production_gate": {
                    "windows": [
                        {
                            "name": "default_12m_costed",
                            "days": 365,
                            "required": True,
                        }
                    ]
                },
            },
        }
        record = {
            "name": "default_12m_costed",
            "required": True,
            "passed": True,
            "failures": [],
            "stats": {
                "return_pct": 0.10,
                "max_drawdown": -0.01,
                "trades": 60,
                "win_rate": 0.55,
                "profit_factor": 2.0,
                "daily_sharpe": 1.2,
            },
        }
        sensitivity_record = {
            "name": "default_12m_costed_slippage_30bps",
            "required": False,
            "passed": True,
            "failures": [],
            "stats": record["stats"],
        }

        with (
            patch("scheduler.run_validation_gate.get_config", return_value=cfg),
            patch("scheduler.run_validation_gate.evaluate_backtest_gate", return_value=record) as gate,
            patch(
                "scheduler.run_validation_gate.evaluate_slippage_sensitivity_gate",
                return_value=sensitivity_record,
            ) as sensitivity,
        ):
            exit_code, output = run_validation_gate(
                profile="production",
                slippage_sensitivity=True,
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("Production slippage sensitivity", output)
        gate.assert_called_once()
        self.assertEqual(sensitivity.call_count, 2)

    def test_production_profile_reports_low_trade_reliability_warning(self):
        cfg = {
            "validation": {
                "min_reliable_trades": 30,
                "cost_model": {},
                "production_gate": {
                    "windows": [
                        {
                            "name": "thin_required_gate",
                            "days": 30,
                            "required": True,
                        }
                    ]
                },
            },
        }
        record = {
            "name": "thin_required_gate",
            "required": True,
            "passed": True,
            "failures": [],
            "warnings": ["trades 4 < reliability floor 30; treat performance metrics as directional"],
            "stats": {
                "return_pct": 0.02,
                "max_drawdown": -0.01,
                "trades": 4,
                "win_rate": 0.75,
                "profit_factor": 2.0,
                "daily_sharpe": 1.2,
            },
        }

        with (
            patch("scheduler.run_validation_gate.get_config", return_value=cfg),
            patch("scheduler.run_validation_gate.evaluate_backtest_gate", return_value=record) as gate,
        ):
            exit_code, output = run_validation_gate(profile="production")

        self.assertEqual(exit_code, 0)
        self.assertIn("watch: trades 4 < reliability floor 30", output)
        self.assertIn("RESULT: PASS, 1 watch warning(s)", output)
        self.assertEqual(gate.call_args.kwargs["min_reliable_trades"], 30)

    def test_gap_profile_runs_configured_backtest_gates(self):
        cfg = {
            "validation": {
                "cost_model": {},
                "gap_up_enablement": {
                    "backtest_windows": [
                        {
                            "name": "gap_up_12m_costed",
                            "days": 365,
                            "strategies": ["gap_up"],
                            "required": True,
                        }
                    ]
                },
            },
        }
        record = {
            "name": "gap_up_12m_costed",
            "required": True,
            "passed": True,
            "failures": [],
            "stats": {
                "return_pct": 0.03,
                "max_drawdown": -0.01,
                "trades": 15,
                "win_rate": 0.73,
                "profit_factor": 3.0,
                "daily_sharpe": 1.2,
            },
        }

        with (
            patch("scheduler.run_validation_gate.get_config", return_value=cfg),
            patch("scheduler.run_validation_gate.evaluate_backtest_gate", return_value=record) as gate,
        ):
            exit_code, output = run_validation_gate(profile="gap")

        self.assertEqual(exit_code, 0)
        self.assertIn("Gap-Up enablement gates:", output)
        gate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
