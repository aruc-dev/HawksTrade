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
        order = SimpleNamespace(id="exit-1", status="filled", filled_qty="2")

        with (
            patch.object(order_executor.ac, "get_position", return_value=position),
            patch.object(order_executor.ac, "get_stock_latest_price", return_value=110),
            patch.object(order_executor.ac, "get_open_orders", return_value=[]),
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

    def test_exit_position_keeps_trade_open_when_exit_order_not_filled(self):
        position = SimpleNamespace(qty="2", avg_entry_price="100")
        order = SimpleNamespace(id="exit-1", status="new", filled_qty="0")

        with (
            patch.object(order_executor.ac, "get_position", return_value=position),
            patch.object(order_executor.ac, "get_stock_latest_price", return_value=110),
            patch.object(order_executor.ac, "get_open_orders", return_value=[]),
            patch.object(order_executor.ac, "place_limit_order", return_value=order),
        ):
            result = order_executor.exit_position("AAPL", "take profit", dry_run=False)

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "submitted")

        with open(trade_log.TRADE_LOG, "r") as f:
            rows = list(csv.DictReader(f))

        self.assertEqual(rows[0]["side"], "buy")
        self.assertEqual(rows[0]["status"], "open")
        self.assertEqual(rows[1]["side"], "sell")
        self.assertEqual(rows[1]["status"], "submitted")

    def test_exit_position_matches_crypto_broker_symbol_to_trade_log_symbol(self):
        trade_log.log_trade({
            "timestamp": "2026-04-10T12:00:00",
            "mode": "paper",
            "symbol": "DOGE/USD",
            "strategy": "ma_crossover",
            "asset_class": "crypto",
            "side": "buy",
            "qty": 100,
            "entry_price": 0.09,
            "order_id": "entry-2",
            "status": "open",
        })
        position = SimpleNamespace(qty="100", avg_entry_price="0.09")
        order = SimpleNamespace(id="exit-2", status="filled", filled_qty="100")

        with (
            patch.object(order_executor.ac, "get_position", return_value=position),
            patch.object(order_executor.ac, "get_crypto_latest_price", return_value=0.1),
            patch.object(order_executor.ac, "get_open_orders", return_value=[]),
            patch.object(order_executor.ac, "place_limit_order", return_value=order) as place_limit_order,
        ):
            result = order_executor.exit_position("DOGEUSD", "take profit", asset_class="crypto")

        self.assertIsNotNone(result)
        self.assertEqual(result["symbol"], "DOGE/USD")
        place_limit_order.assert_called_once()
        self.assertEqual(place_limit_order.call_args.args[0], "DOGE/USD")

        with open(trade_log.TRADE_LOG, "r") as f:
            rows = list(csv.DictReader(f))

        doge_rows = [row for row in rows if row["symbol"] == "DOGE/USD"]
        self.assertEqual(doge_rows[0]["status"], "closed")
        self.assertEqual(doge_rows[1]["side"], "sell")

    def test_exit_position_skips_duplicate_sell_when_exit_order_pending(self):
        position = SimpleNamespace(qty="2", avg_entry_price="100")
        pending_order = SimpleNamespace(id="pending-1", symbol="AAPL", side="sell")

        with (
            patch.object(order_executor.ac, "get_position", return_value=position),
            patch.object(order_executor.ac, "get_stock_latest_price", return_value=110),
            patch.object(order_executor.ac, "get_open_orders", return_value=[pending_order]),
            patch.object(order_executor.ac, "place_limit_order") as place_limit_order,
        ):
            result = order_executor.exit_position("AAPL", "take profit", dry_run=False)

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "pending_exit")
        place_limit_order.assert_not_called()

        with open(trade_log.TRADE_LOG, "r") as f:
            rows = list(csv.DictReader(f))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "open")


if __name__ == "__main__":
    unittest.main()
