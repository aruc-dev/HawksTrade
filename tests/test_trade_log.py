import csv
import multiprocessing
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from tracking import trade_log


def _concurrent_log_worker(args):
    path, worker_id, count = args
    from tracking import trade_log as worker_trade_log

    worker_trade_log.TRADE_LOG = Path(path)
    for idx in range(count):
        worker_trade_log.log_trade({
            "timestamp": f"2026-04-17T12:{worker_id:02d}:{idx:02d}+00:00",
            "mode": "paper",
            "symbol": f"T{worker_id}",
            "strategy": "concurrency_test",
            "asset_class": "stock",
            "side": "buy",
            "qty": 1,
            "entry_price": 100 + idx,
            "order_id": f"{worker_id}-{idx}",
            "status": "open",
        })


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

    def test_mark_trade_closed_reduces_quantity_on_partial_exit(self):
        trade_log.log_trade({
            "timestamp": "2026-04-14T19:05:11+00:00",
            "mode": "paper",
            "symbol": "AMZN",
            "strategy": "momentum",
            "asset_class": "stock",
            "side": "buy",
            "qty": "32.139315",
            "entry_price": 248.53,
            "order_id": "entry",
            "status": "open",
        })

        trade_log.mark_trade_closed(
            "AMZN",
            exit_price=248.565,
            pnl_pct=-0.00002,
            reason="partial exit",
            closed_qty="29.0",
        )

        rows = self._read_rows()
        self.assertEqual(rows[0]["status"], "open")
        self.assertEqual(rows[0]["qty"], "3.139315")
        self.assertEqual(rows[0]["exit_price"], "")
        self.assertEqual(rows[0]["pnl_pct"], "")

    def test_reconcile_reopens_closed_buy_when_broker_has_residual(self):
        trade_log.log_trade({
            "timestamp": "2026-04-14T19:05:11+00:00",
            "mode": "paper",
            "symbol": "AMZN",
            "strategy": "momentum",
            "asset_class": "stock",
            "side": "buy",
            "qty": "32.139315",
            "entry_price": 248.53,
            "exit_price": 248.565,
            "pnl_pct": -0.00002,
            "exit_reason": "exit",
            "order_id": "entry",
            "status": "closed",
        })
        trade_log.log_trade({
            "timestamp": "2026-04-14T19:05:13+00:00",
            "mode": "paper",
            "symbol": "AMZN",
            "strategy": "momentum",
            "asset_class": "stock",
            "side": "sell",
            "qty": "29.0",
            "entry_price": 248.57,
            "exit_price": 248.565,
            "pnl_pct": -0.00002,
            "exit_reason": "exit",
            "order_id": "exit",
            "status": "closed",
        })

        summary = trade_log.reconcile_open_trades_with_positions([
            SimpleNamespace(
                symbol="AMZN",
                qty="3.139315",
                avg_entry_price="248.571954",
                asset_class="AssetClass.US_EQUITY",
            )
        ])

        rows = self._read_rows()
        self.assertEqual(summary["reopened_rows"], 1)
        self.assertEqual(rows[0]["status"], "open")
        self.assertEqual(rows[0]["qty"], "3.139315")
        self.assertEqual(rows[0]["entry_price"], "248.571954")
        self.assertEqual(rows[1]["status"], "closed")

    def test_reconcile_marks_unfilled_sell_submitted_when_position_still_open(self):
        trade_log.log_trade({
            "timestamp": "2026-04-14T18:04:02+00:00",
            "mode": "paper",
            "symbol": "META",
            "strategy": "momentum",
            "asset_class": "stock",
            "side": "buy",
            "qty": "12.030374",
            "entry_price": 664.625,
            "exit_price": 666.035,
            "pnl_pct": 0.001315,
            "exit_reason": "exit",
            "order_id": "entry",
            "status": "closed",
        })
        trade_log.log_trade({
            "timestamp": "2026-04-14T18:04:10+00:00",
            "mode": "paper",
            "symbol": "META",
            "strategy": "momentum",
            "asset_class": "stock",
            "side": "sell",
            "qty": "12.030374",
            "entry_price": 665.16,
            "exit_price": 666.035,
            "pnl_pct": 0.001315,
            "exit_reason": "exit",
            "order_id": "exit",
            "status": "closed",
        })

        summary = trade_log.reconcile_open_trades_with_positions([
            SimpleNamespace(
                symbol="META",
                qty="12.030374",
                avg_entry_price="665.16",
                asset_class="AssetClass.US_EQUITY",
            )
        ])

        rows = self._read_rows()
        self.assertEqual(summary["reopened_rows"], 1)
        self.assertEqual(summary["marked_unfilled_sells"], 1)
        self.assertEqual(rows[0]["status"], "open")
        self.assertEqual(rows[1]["status"], "submitted")
        self.assertEqual(rows[1]["pnl_pct"], "")

    def test_get_trade_age_days_accepts_timezone_aware_timestamp(self):
        timestamp = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()

        trade_log.log_trade({
            "timestamp": timestamp,
            "mode": "paper",
            "symbol": "AAPL",
            "strategy": "momentum",
            "asset_class": "stock",
            "side": "buy",
            "qty": 1,
            "entry_price": 100,
            "order_id": "aware",
            "status": "open",
        })

        age = trade_log.get_trade_age_days("AAPL")

        self.assertGreater(age, 1.9)
        self.assertLess(age, 2.1)

    def test_get_open_trades_includes_only_broker_confirmed_buy_exposure(self):
        for side, status, symbol in (
            ("buy", "submitted", "SUBMITTED"),
            ("buy", "partially_filled", "PARTIAL"),
            ("buy", "open", "OPEN"),
            ("sell", "partially_filled", "PARTIAL_SELL"),
        ):
            trade_log.log_trade({
                "timestamp": "2026-04-17T12:00:00+00:00",
                "mode": "paper",
                "symbol": symbol,
                "strategy": "test",
                "asset_class": "stock",
                "side": side,
                "qty": 1,
                "entry_price": 100,
                "order_id": symbol,
                "status": status,
            })

        symbols = {row["symbol"] for row in trade_log.get_open_trades()}

        self.assertEqual(symbols, {"PARTIAL", "OPEN"})

    def test_log_trade_preserves_rows_with_concurrent_process_writers(self):
        worker_count = 4
        rows_per_worker = 25
        ctx = multiprocessing.get_context("spawn")
        processes = [
            ctx.Process(
                target=_concurrent_log_worker,
                args=((str(trade_log.TRADE_LOG), worker_id, rows_per_worker),),
            )
            for worker_id in range(worker_count)
        ]

        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=15)

        for process in processes:
            self.assertEqual(process.exitcode, 0)

        rows = trade_log.read_trade_rows()
        order_ids = {row["order_id"] for row in rows}

        self.assertEqual(len(rows), worker_count * rows_per_worker)
        self.assertEqual(len(order_ids), worker_count * rows_per_worker)
        self.assertTrue(trade_log.TRADE_LOG.with_name("trades.csv.lock").exists())


if __name__ == "__main__":
    unittest.main()
