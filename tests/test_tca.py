import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tracking import tca


class TcaTests(unittest.TestCase):
    def test_compute_implementation_shortfall_buy_uses_adverse_bps(self):
        result = tca.compute_implementation_shortfall({
            "timestamp": "2026-05-18T15:00:00+00:00",
            "symbol": "AAPL",
            "strategy": "momentum",
            "asset_class": "stock",
            "side": "buy",
            "qty": "10",
            "entry_price": "102",
            "decision_price": "100",
            "arrival_price": "101",
            "expected_slippage_bps": "150",
        })

        self.assertTrue(result["eligible"])
        self.assertAlmostEqual(result["implementation_shortfall_bps"], 200.0)
        self.assertAlmostEqual(result["timing_bps"], 100.0)
        self.assertAlmostEqual(result["slippage_bps"], 99.0099, places=4)
        self.assertAlmostEqual(result["residual_bps"], 50.0)

    def test_compute_implementation_shortfall_sell_negates_direction(self):
        result = tca.compute_implementation_shortfall({
            "timestamp": "2026-05-18T15:00:00+00:00",
            "symbol": "AAPL",
            "strategy": "momentum",
            "asset_class": "stock",
            "side": "sell",
            "qty": "10",
            "exit_price": "98",
            "decision_price": "100",
            "arrival_price": "99",
            "expected_slippage_bps": "150",
        })

        self.assertTrue(result["eligible"])
        self.assertAlmostEqual(result["implementation_shortfall_bps"], 200.0)
        self.assertAlmostEqual(result["timing_bps"], 100.0)
        self.assertAlmostEqual(result["slippage_bps"], 101.0101, places=4)
        self.assertAlmostEqual(result["residual_bps"], 50.0)

    def test_missing_decision_price_is_not_eligible(self):
        result = tca.compute_implementation_shortfall({
            "side": "buy",
            "entry_price": "100",
        })

        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "missing_decision_price")

    def test_aggregate_by_strategy(self):
        rows = [
            {
                "timestamp": "2026-05-18T15:00:00+00:00",
                "symbol": "AAPL",
                "strategy": "momentum",
                "asset_class": "stock",
                "side": "buy",
                "qty": "1",
                "entry_price": "101",
                "decision_price": "100",
                "expected_slippage_bps": "50",
            },
            {
                "timestamp": "2026-05-18T16:00:00+00:00",
                "symbol": "MSFT",
                "strategy": "momentum",
                "asset_class": "stock",
                "side": "buy",
                "qty": "1",
                "entry_price": "102",
                "decision_price": "100",
                "expected_slippage_bps": "100",
            },
        ]

        frame = tca.prepare_tca_frame(rows)
        summary = tca.aggregate_by(frame, ["strategy"])

        self.assertEqual(summary.loc[0, "strategy"], "momentum")
        self.assertEqual(summary.loc[0, "fills"], 2)
        self.assertAlmostEqual(summary.loc[0, "median_is_bps"], 150.0)

    def test_detect_slippage_anomaly_uses_trailing_sigma(self):
        start = datetime(2026, 5, 1, tzinfo=timezone.utc)
        residuals = [9, 10, 10, 10, 11, 12.5]
        rows = []
        for idx, residual in enumerate(residuals):
            expected = 20.0
            fill = 100.0 * (1.0 + (expected + residual) / 10000.0)
            rows.append({
                "timestamp": (start + timedelta(days=idx)).isoformat(),
                "symbol": "AAPL",
                "strategy": "momentum",
                "asset_class": "stock",
                "side": "buy",
                "qty": "1",
                "entry_price": str(fill),
                "decision_price": "100",
                "expected_slippage_bps": str(expected),
            })

        anomalies = tca.detect_slippage_anomalies(rows, sigma=3.0, min_history=5)
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["symbol"], "AAPL")

        no_anomalies = tca.detect_slippage_anomalies(rows[:-1], sigma=3.0, min_history=5)
        self.assertEqual(no_anomalies, [])

    def test_weekly_report_renders_latency_section(self):
        report = tca.render_weekly_tca_report(
            [
                {
                    "timestamp": "2026-05-18T15:00:00+00:00",
                    "symbol": "AAPL",
                    "strategy": "momentum",
                    "asset_class": "stock",
                    "side": "buy",
                    "qty": "1",
                    "entry_price": "101",
                    "decision_price": "100",
                    "expected_slippage_bps": "50",
                    "latency_ms": '{"total": 120, "ack": 20}',
                }
            ],
            generated_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        )

        self.assertIn("Weekly TCA Report", report)
        self.assertIn("By Strategy", report)
        self.assertIn("Latency", report)
        self.assertIn("total", report)

    def test_write_weekly_tca_report_uses_date_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = tca.write_weekly_tca_report(
                Path(tmp),
                [],
                generated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
            )

            self.assertEqual(path.name, "tca_weekly_2026-05-18.md")
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
