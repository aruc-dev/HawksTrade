import csv
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tracking import trade_log


class TradeLogTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.original_trade_log = trade_log.TRADE_LOG
        trade_log.TRADE_LOG = Path(self.tmpdir.name) / "trades.csv"
        self.addCleanup(setattr, trade_log, "TRADE_LOG", self.original_trade_log)

    def _read_rows(self):
        with open(trade_log.TRADE_LOG, "r") as f:
            return list(csv.DictReader(f))

    def test_mark_trade_closed_updates_most_recent_open_buy(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        older = (now - timedelta(days=2)).isoformat()
        newer = (now - timedelta(days=1)).isoformat()

        trade_log.log_trade({
            "timestamp": older,
            "mode": "paper",
            "symbol": "AAPL",
            "strategy": "momentum",
            "asset_class": "stock",
            "side": "buy",
            "qty": 1,
            "entry_price": 100,
            "order_id": "old",
            "status": "open",
        })
        trade_log.log_trade({
            "timestamp": newer,
            "mode": "paper",
            "symbol": "AAPL",
            "strategy": "momentum",
            "asset_class": "stock",
            "side": "buy",
            "qty": 1,
            "entry_price": 110,
            "order_id": "new",
            "status": "open",
        })

        trade_log.mark_trade_closed("AAPL", exit_price=121, pnl_pct=0.1, reason="test exit")

        rows = self._read_rows()
        self.assertEqual(rows[0]["status"], "open")
        self.assertEqual(rows[1]["status"], "closed")
        self.assertEqual(rows[1]["exit_price"], "121")
        self.assertEqual(rows[1]["pnl_pct"], "0.1")
        self.assertEqual(rows[1]["exit_reason"], "test exit")


if __name__ == "__main__":
    unittest.main()
