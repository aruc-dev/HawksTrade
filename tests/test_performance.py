import csv
import tempfile
import unittest
from pathlib import Path

import pandas as pd

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
                "timestamp": "2026-04-10T11:00:00.000000",
                "mode": "paper",
                "symbol": "AAPL",
                "strategy": "momentum",
                "asset_class": "stock",
                "side": "buy",
                "qty": 1,
                "entry_price": 100,
                "exit_price": 105,
                "pnl_pct": 0.05,
                "status": "closed",
            },
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

    def test_compute_summary_counts_realized_sells_and_open_pnl(self):
        self._write_trades([
            {
                "timestamp": "2026-04-10T12:00:00.000000",
                "mode": "paper",
                "symbol": "AAPL",
                "strategy": "momentum",
                "asset_class": "stock",
                "side": "sell",
                "qty": 2,
                "entry_price": 100,
                "exit_price": 110,
                "pnl_pct": 0.10,
                "status": "closed",
            },
        ])

        df = performance.load_closed_trades()
        open_positions = pd.DataFrame([{"symbol": "MSFT", "unrealized_pnl_dollars": -5}])
        summary = performance.compute_summary(df, open_positions=open_positions)

        self.assertEqual(summary["total_trades"], 1)
        self.assertEqual(summary["realized_pnl_dollars"], 20)
        self.assertEqual(summary["open_positions"], 1)
        self.assertEqual(summary["unrealized_pnl_dollars"], -5)
        self.assertEqual(summary["total_pnl_dollars"], 15)


if __name__ == "__main__":
    unittest.main()
