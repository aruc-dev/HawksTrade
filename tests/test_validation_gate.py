import math
import unittest

from scheduler.run_validation_gate import (
    evaluate_rsi_forward_gate,
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


if __name__ == "__main__":
    unittest.main()
