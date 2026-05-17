import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from analysis.spa_test import returns_matrix_from_csv, spa_test, strategy_search_space_catalog
from scheduler.run_spa_analysis import render_catalog_report, run_spa_report


class SPATests(unittest.TestCase):
    def test_spa_recovers_constructed_better_strategy(self):
        rng = np.random.default_rng(7)
        benchmark = rng.normal(0.0, 0.01, size=160)
        strategies = pd.DataFrame({
            "noise_1": benchmark + rng.normal(0.0, 0.01, size=160),
            "edge": benchmark + 0.006 + rng.normal(0.0, 0.004, size=160),
            "noise_2": benchmark + rng.normal(0.0, 0.01, size=160),
        })

        result = spa_test(strategies, benchmark, n_boot=500, block_size=5, seed=3)

        self.assertEqual(result.best_strategy, "edge")
        self.assertLess(result.p_value, 0.05)

    def test_spa_random_walks_fail_to_reject(self):
        rng = np.random.default_rng(11)
        benchmark = rng.normal(0.0, 0.01, size=180)
        strategies = pd.DataFrame({
            f"variant_{idx}": benchmark + rng.normal(0.0, 0.01, size=180)
            for idx in range(8)
        })

        result = spa_test(strategies, benchmark, n_boot=500, block_size=5, seed=5)

        self.assertGreater(result.p_value, 0.05)

    def test_p_value_increases_with_more_null_candidates(self):
        rng = np.random.default_rng(13)
        benchmark = rng.normal(0.0, 0.01, size=160)
        edge = benchmark + 0.002 + rng.normal(0.0, 0.01, size=160)
        small = pd.DataFrame({"edge": edge})
        large = small.copy()
        for idx in range(20):
            large[f"noise_{idx}"] = benchmark + rng.normal(0.0, 0.01, size=160)

        small_result = spa_test(small, benchmark, n_boot=600, block_size=5, seed=17)
        large_result = spa_test(large, benchmark, n_boot=600, block_size=5, seed=17)

        self.assertGreaterEqual(large_result.p_value, small_result.p_value)

    def test_catalog_has_expected_strategy_variants(self):
        catalog = strategy_search_space_catalog()

        self.assertIn("momentum", catalog)
        self.assertGreaterEqual(len(catalog["momentum"]), 20)

    def test_returns_matrix_from_csv_and_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "returns.csv"
            pd.DataFrame({
                "date": pd.date_range("2025-01-01", periods=40),
                "benchmark": [0.0] * 40,
                "variant": [0.002] * 40,
            }).to_csv(path, index=False)

            strategies, benchmark = returns_matrix_from_csv(str(path))
            report = run_spa_report(returns_csv=path, n_boot=100, block_size=3, seed=1)

        self.assertEqual(list(strategies.columns), ["variant"])
        self.assertEqual(len(benchmark), 40)
        self.assertIn("SPA p-value", report)

    def test_catalog_report_lists_enabled_strategies(self):
        cfg = {"strategies": {"momentum": {"enabled": True}, "gap_up": {"enabled": False}}}

        with patch("scheduler.run_spa_analysis.get_config", return_value=cfg):
            report = render_catalog_report()

        self.assertIn("momentum", report)
        self.assertIn("Variants", report)


if __name__ == "__main__":
    unittest.main()
