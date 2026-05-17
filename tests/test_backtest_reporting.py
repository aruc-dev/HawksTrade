import math
import unittest

import pandas as pd

from scheduler.run_backtest import _compute_strategy_attribution


class BacktestReportingTests(unittest.TestCase):
    def test_strategy_attribution_returns_numeric_per_strategy_stats(self):
        trades = pd.DataFrame([
            {"strategy": "momentum", "pnl": 10.0, "pnl_pct": 0.02},
            {"strategy": "momentum", "pnl": -5.0, "pnl_pct": -0.01},
            {"strategy": "gap_up", "pnl": 7.0, "pnl_pct": 0.03},
        ])

        attribution = _compute_strategy_attribution(trades)

        self.assertEqual(attribution["momentum"]["trades"], 2)
        self.assertEqual(attribution["momentum"]["win_rate"], 0.5)
        self.assertEqual(attribution["momentum"]["total_pnl"], 5.0)
        self.assertEqual(attribution["momentum"]["profit_factor"], 2.0)
        self.assertTrue(math.isinf(attribution["gap_up"]["profit_factor"]))


if __name__ == "__main__":
    unittest.main()
