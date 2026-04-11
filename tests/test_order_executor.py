import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core import order_executor
from tracking import trade_log


class OrderExecutorTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.original_trade_log = trade_log.TRADE_LOG
        trade_log.TRADE_LOG = Path(self.tmpdir.name) / "trades.csv"
        self.addCleanup(setattr, trade_log, "TRADE_LOG", self.original_trade_log)

        trade_log.log_trade({
            "timestamp": "2026-04-10T12:00:00",
            "mode": "paper",
            "symbol": "AAPL",
            "strategy": "momentum",
            "asset_class": "stock",
            "side": "buy",
            "qty": 2,
            "entry_price": 100,
            "order_id": "entry-1",
            "status": "open",
        })

    def test_exit_position_closes_original_open_trade_after_order(self):
        position = SimpleNamespace(qty="2", avg_entry_price="100")
        order = SimpleNamespace(id="exit-1")

        with (
            patch.object(order_executor.ac, "get_position", return_value=position),
            patch.object(order_executor.ac, "get_stock_latest_price", return_value=110),
            patch.object(order_executor.ac, "place_limit_order", return_value=order),
        ):
            result = order_executor.exit_position("AAPL", "take profit", dry_run=False)

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "closed")

        with open(trade_log.TRADE_LOG, "r") as f:
            rows = list(csv.DictReader(f))

        self.assertEqual(rows[0]["side"], "buy")
        self.assertEqual(rows[0]["status"], "closed")
        self.assertEqual(rows[0]["exit_price"], "110")
        self.assertEqual(rows[1]["side"], "sell")
        self.assertEqual(rows[1]["status"], "closed")


if __name__ == "__main__":
    unittest.main()
