import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from unittest.mock import patch

from scripts import research_factors as rf


def _bars(periods=230, *, start="2025-01-01", base=100.0, drift=0.2):
    dates = pd.date_range(start=start, periods=periods, freq="B")
    bars = []
    for index, date in enumerate(dates):
        close = base + drift * index
        open_price = close - 0.15
        bars.append(SimpleNamespace(
            timestamp=date,
            open=open_price,
            high=close + 1.0,
            low=open_price - 1.0,
            close=close,
            volume=1_000_000 + index * 1_000,
        ))
    return bars


class FactorResearchTests(unittest.TestCase):
    def test_build_factor_dataset_includes_features_and_forward_returns(self):
        dataset, issues = rf.build_factor_dataset(
            {
                "AAPL": _bars(base=100.0, drift=0.25),
                "MSFT": _bars(base=200.0, drift=0.10),
            },
            horizons=(1, 5, 20),
        )

        self.assertEqual(issues, [])
        self.assertFalse(dataset.empty)
        for column in (
            "return_5d",
            "volume_ratio",
            "rsi_14",
            "bb_pct_b",
            "atr_pct",
            "sma50_distance",
            "sma200_distance",
            "gap_pct",
            "breadth_pct",
            "breadth_regime",
            "sector",
            "forward_return_1d",
            "forward_return_20d",
        ):
            self.assertIn(column, dataset.columns)
        self.assertEqual(sorted(dataset["symbol"].unique().tolist()), ["AAPL", "MSFT"])

    def test_volume_ratio_uses_prior_completed_twenty_bars(self):
        bars = _bars(periods=25)
        for index, bar in enumerate(bars):
            bar.volume = 100 if index < 20 else 1000

        dataset, issues = rf.build_factor_dataset({"AAPL": bars}, horizons=(1,))
        last_ratio = float(dataset[dataset["symbol"] == "AAPL"]["volume_ratio"].iloc[20])

        self.assertEqual(issues, [])
        self.assertAlmostEqual(last_ratio, 10.0)

    def test_forward_return_horizons_must_be_positive(self):
        with self.assertRaisesRegex(ValueError, "positive integers"):
            rf.build_factor_dataset({"AAPL": _bars()}, horizons=(0, 5))

    def test_compute_factor_report_has_metrics_and_splits(self):
        dataset, issues = rf.build_factor_dataset(
            {
                "AAPL": _bars(base=100.0, drift=0.30),
                "MSFT": _bars(base=200.0, drift=-0.05),
            },
            horizons=(1, 5),
        )

        summary = rf.compute_factor_report(
            dataset,
            factors=("return_5d",),
            horizons=(1, 5),
            data_quality_issues=issues,
        )

        self.assertGreater(summary["rows"], 0)
        self.assertEqual(summary["generated_at"], "not_recorded")
        self.assertIn("return_5d", summary["factors"])
        one_day = summary["factors"]["return_5d"]["1"]
        self.assertGreater(one_day["observations"], 0)
        self.assertIn("information_coefficient", one_day)
        self.assertIn("quantile_mean_returns", one_day)
        self.assertEqual(set(one_day["splits"].keys()), {"train", "validation", "test"})

    def test_spearman_corr_returns_none_for_constant_inputs(self):
        corr = rf._spearman_corr(
            pd.Series([1.0, 1.0, 1.0]),
            pd.Series([0.01, 0.02, 0.03]),
        )

        self.assertIsNone(corr)

    def test_missing_bars_are_reported(self):
        dataset, issues = rf.build_factor_dataset(
            {
                "AAPL": [],
                "MSFT": _bars(),
            },
            horizons=(1,),
        )

        self.assertFalse(dataset.empty)
        self.assertIn({"symbol": "AAPL", "issue": "missing_bars"}, issues)

    def test_invalid_ohlcv_rows_are_reported_and_skipped(self):
        bad_bar = SimpleNamespace(
            timestamp=pd.Timestamp("2025-01-01"),
            open=100.0,
            high=95.0,
            low=99.0,
            close=101.0,
            volume=1_000,
        )

        dataset, issues = rf.build_factor_dataset(
            {
                "BAD": [bad_bar],
                "MSFT": _bars(),
            },
            horizons=(1,),
        )

        self.assertNotIn("BAD", set(dataset["symbol"].tolist()))
        self.assertIn({"symbol": "BAD", "issue": "invalid_ohlcv"}, issues)

    def test_non_positive_price_and_volume_rows_are_invalid(self):
        bad_price = SimpleNamespace(
            timestamp=pd.Timestamp("2025-01-01"),
            open=0.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=1_000,
        )
        zero_volume = SimpleNamespace(
            timestamp=pd.Timestamp("2025-01-02"),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=0,
        )

        dataset, issues = rf.build_factor_dataset(
            {
                "BAD": [bad_price, zero_volume],
                "MSFT": _bars(),
            },
            horizons=(1,),
        )

        self.assertNotIn("BAD", set(dataset["symbol"].tolist()))
        self.assertIn({"symbol": "BAD", "issue": "invalid_ohlcv"}, issues)

    def test_symbol_date_gaps_are_reported(self):
        bars = _bars(periods=5)
        bars.pop(2)

        dataset, issues = rf.build_factor_dataset({"AAPL": bars}, horizons=(1,))

        self.assertFalse(dataset.empty)
        self.assertTrue(
            any(
                issue["symbol"] == "AAPL"
                and issue["issue"] == "date_gap"
                and issue["missing_count"] == 1
                for issue in issues
            )
        )

    def test_single_symbol_holidays_do_not_report_date_gaps(self):
        def _bar(date):
            return SimpleNamespace(
                timestamp=pd.Timestamp(date),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                volume=1_000_000,
            )

        _, issues = rf.build_factor_dataset(
            {"AAPL": [_bar("2025-01-17"), _bar("2025-01-21")]},
            horizons=(1,),
        )

        self.assertFalse(
            any(issue["symbol"] == "AAPL" and issue["issue"] == "date_gap" for issue in issues)
        )

    def test_write_research_outputs_writes_csv_json_and_markdown(self):
        dataset, issues = rf.build_factor_dataset({"AAPL": _bars()}, horizons=(1,))
        summary = rf.compute_factor_report(
            dataset,
            factors=("return_5d",),
            horizons=(1,),
            data_quality_issues=issues,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            outputs = rf.write_research_outputs(dataset, summary, Path(tmpdir))

            self.assertTrue(outputs["csv"].exists())
            self.assertTrue(outputs["json"].exists())
            self.assertTrue(outputs["markdown"].exists())
            loaded = json.loads(outputs["json"].read_text(encoding="utf-8"))
            self.assertEqual(loaded["rows"], summary["rows"])
            self.assertIn("No live strategy defaults were changed", outputs["markdown"].read_text(encoding="utf-8"))

    def test_resolve_symbols_prefers_explicit_symbols_and_caps(self):
        cfg = {"stocks": {"scan_universe": ["MSFT", "QQQ"]}}

        symbols = rf.resolve_symbols(
            cfg,
            symbols=["aapl", "AAPL", "tsla"],
            use_screener=False,
            max_symbols=1,
        )

        self.assertEqual(symbols, ["AAPL"])

    def test_run_research_passes_explicit_date_range_to_fetch(self):
        args = type("Args", (), {
            "symbols": ["AAPL"],
            "use_screener": False,
            "max_symbols": None,
            "days": 260,
            "start": "2025-01-01",
            "end": "2025-03-31",
            "horizons": [1],
            "generated_at": "test-run",
            "output_dir": "/tmp/research",
        })()

        with (
            patch.object(rf, "get_config", return_value={"stocks": {"scan_universe": []}}),
            patch.object(rf.ac, "get_stock_bars", return_value={"AAPL": _bars(periods=80)}) as get_bars,
            patch.object(rf, "write_research_outputs", return_value={}),
        ):
            result = rf.run_research(args)

        get_bars.assert_called_once_with(
            ["AAPL"],
            timeframe="1Day",
            limit=260,
            start="2025-01-01",
            end="2025-03-31",
        )
        self.assertEqual(result["summary"]["generated_at"], "test-run")

    def test_run_research_reports_requested_symbol_missing_from_fetch_result(self):
        args = type("Args", (), {
            "symbols": ["AAPL", "MSFT"],
            "use_screener": False,
            "max_symbols": None,
            "days": 260,
            "start": None,
            "end": None,
            "horizons": [1],
            "generated_at": "test-run",
            "output_dir": "/tmp/research",
        })()

        with (
            patch.object(rf, "get_config", return_value={"stocks": {"scan_universe": []}}),
            patch.object(rf.ac, "get_stock_bars", return_value={"AAPL": _bars(periods=80)}),
            patch.object(rf, "write_research_outputs", return_value={}),
        ):
            result = rf.run_research(args)

        self.assertIn(
            {"symbol": "MSFT", "issue": "missing_bars"},
            result["summary"]["data_quality"]["issues"],
        )


if __name__ == "__main__":
    unittest.main()
