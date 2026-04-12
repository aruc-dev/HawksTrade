import csv
import tempfile
import unittest
from pathlib import Path

from tracking import performance


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.original_trade_log = performance.TRADE_LOG
        performance.TRADE_LOG = Path(self.tmpdir.name) / "trades.csv"
        self.addCleanup(setattr, performance, "TRADE_LOG", self.original_trade_log)

    def _write_trades(self, rows):
        fieldnames = [
            "timestamp", "mode", "symbol", "strategy", "asset_class",
            "side", "qty", "entry_price", "exit_price", "stop_loss",
            "take_profit", "pnl_pct", "exit_reason", "order_id", "status",
        ]
        with open(performance.TRADE_LOG, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def test_load_closed_trades_accepts_mixed_iso_timestamps(self):
        self._write_trades([
            {
                "timestamp": "2026-04-10T12:00:00.000000",
                "mode": "paper",
                "symbol": "AAPL",
                "strategy": "momentum",
                "asset_class": "stock",
                "side": "sell",
                "qty": 1,
                "entry_price": 100,
                "exit_price": 105,
                "pnl_pct": 0.05,
                "status": "closed",
            },
            {
                "timestamp": "2026-04-11T12:00:00.000000+00:00",
                "mode": "paper",
                "symbol": "MSFT",
                "strategy": "momentum",
                "asset_class": "stock",
                "side": "sell",
                "qty": 1,
                "entry_price": 100,
                "exit_price": 95,
                "pnl_pct": -0.05,
                "status": "closed",
            },
        ])

        df = performance.load_closed_trades()

        self.assertEqual(len(df), 2)
        self.assertEqual(str(df["timestamp"].dt.tz), "None")
        summary = performance.compute_summary(df)
        self.assertEqual(summary["total_trades"], 2)


if __name__ == "__main__":
    unittest.main()
