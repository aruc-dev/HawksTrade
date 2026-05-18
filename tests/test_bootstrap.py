import unittest

import numpy as np
import pandas as pd

from analysis.bootstrap import (
    block_bootstrap_returns,
    bootstrap_backtest,
    gate_bounds,
    summarise,
    trade_resample,
)


class BootstrapTests(unittest.TestCase):
    def test_trade_resample_positive_trades_excludes_zero_return(self):
        trades = pd.DataFrame({"pnl": [10.0, 20.0, 15.0, 12.0], "pnl_pct": [0.01, 0.02, 0.015, 0.012]})

        dist = trade_resample(trades, n_iter=200, seed=1, initial_fund=1000)
        summary = summarise(dist)

        self.assertGreater(summary["return_pct"]["p05"], 0)
        self.assertEqual(summary["prob_loss"], 0.0)
        self.assertIn("trade_sharpe", summary)
        self.assertNotIn("daily_sharpe", summary)

    def test_block_bootstrap_seed_is_reproducible(self):
        returns = [0.01, -0.002, 0.003, 0.004, -0.001] * 5

        first = block_bootstrap_returns(returns, block_size=3, n_iter=50, seed=7)
        second = block_bootstrap_returns(returns, block_size=3, n_iter=50, seed=7)

        pd.testing.assert_frame_equal(first, second)

    def test_block_bootstrap_drawdown_probability(self):
        returns = [0.01, -0.03, -0.04, 0.02, -0.01] * 4

        dist = block_bootstrap_returns(returns, block_size=2, n_iter=200, seed=3)
        summary = summarise(dist, drawdown_threshold=0.05)

        self.assertIn("prob_drawdown_gt_threshold", summary)
        self.assertGreaterEqual(summary["prob_drawdown_gt_threshold"], 0)
        self.assertIn("daily_sharpe", summary)
        self.assertNotIn("profit_factor", summary)
        self.assertNotIn("win_rate", summary)

    def test_bootstrap_backtest_returns_trade_and_block_summaries(self):
        trades = pd.DataFrame({"pnl": [10.0, -5.0, 8.0], "pnl_pct": [0.01, -0.005, 0.008]})
        curve = pd.DataFrame({"value": np.array([1000, 1010, 1005, 1013], dtype=float)})

        result = bootstrap_backtest(trades, curve, initial_fund=1000, n_iter=50, seed=9)

        self.assertEqual(result["iterations"], 50)
        self.assertIn("return_pct", result["trade"])
        self.assertIn("return_pct", result["block"])
        self.assertIn("trade_sharpe", result["trade"])
        self.assertIn("daily_sharpe", result["block"])
        self.assertNotIn("daily_sharpe", result["trade"])
        self.assertNotIn("profit_factor", result["block"])

    def test_gate_bounds_uses_bootstrap_lower_bounds_when_present(self):
        stats = {
            "return_pct": 0.10,
            "max_drawdown": -0.02,
            "profit_factor": 2.0,
            "daily_sharpe": 1.2,
            "bootstrap": {
                "block": {
                    "return_pct": {"p05": -0.01},
                    "max_drawdown": {"p05": -0.08},
                    "daily_sharpe": {"p05": 0.2},
                },
                "trade": {"profit_factor": {"p05": 0.9}},
            },
        }

        bounds = gate_bounds(stats)

        self.assertEqual(bounds["return_pct"], -0.01)
        self.assertEqual(bounds["max_drawdown"], -0.08)
        self.assertEqual(bounds["profit_factor"], 0.9)
        self.assertEqual(bounds["daily_sharpe"], 0.2)


if __name__ == "__main__":
    unittest.main()
