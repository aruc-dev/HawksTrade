import csv
import multiprocessing
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

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

    def test_windows_locking_uses_stable_region_and_requested_mode(self):
        class FakeMsvcrt:
            LK_LOCK = 1
            LK_RLCK = 2
            LK_UNLCK = 3

            def __init__(self):
                self.calls = []

            def locking(self, fd, mode, nbytes):
                self.calls.append({
                    "mode": mode,
                    "nbytes": nbytes,
                    "offset": os.lseek(fd, 0, os.SEEK_CUR),
                    "size": os.fstat(fd).st_size,
                })

        fake_msvcrt = FakeMsvcrt()
        original_fcntl = trade_log.fcntl
        had_msvcrt = hasattr(trade_log, "msvcrt")
        original_msvcrt = getattr(trade_log, "msvcrt", None)

        def restore_lock_modules():
            trade_log.fcntl = original_fcntl
            if had_msvcrt:
                trade_log.msvcrt = original_msvcrt
            else:
                delattr(trade_log, "msvcrt")

        trade_log.fcntl = None
        trade_log.msvcrt = fake_msvcrt
        self.addCleanup(restore_lock_modules)

        lock_path = Path(self.tmpdir.name) / "windows.lock"
        with open(lock_path, "a+b") as lock_file:
            trade_log._lock_file(lock_file, exclusive=False)
            trade_log._unlock_file(lock_file)
            trade_log._lock_file(lock_file, exclusive=True)
            trade_log._unlock_file(lock_file)

        self.assertEqual(lock_path.stat().st_size, 1)
        self.assertEqual(
            [call["mode"] for call in fake_msvcrt.calls],
            [
                fake_msvcrt.LK_RLCK,
                fake_msvcrt.LK_UNLCK,
                fake_msvcrt.LK_LOCK,
                fake_msvcrt.LK_UNLCK,
            ],
        )
        self.assertTrue(all(call["offset"] == 0 for call in fake_msvcrt.calls))
        self.assertTrue(all(call["nbytes"] == 1 for call in fake_msvcrt.calls))
        self.assertTrue(all(call["size"] == 1 for call in fake_msvcrt.calls))

    def test_locked_trade_log_docstring_describes_generic_csv_lock(self):
        self.assertIn("CSV file path", trade_log.locked_trade_log.__doc__)

    def test_shared_locked_trade_log_reuses_existing_lock_read_write(self):
        lock_path = trade_log._lock_path(trade_log.TRADE_LOG)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_bytes(b"\0")
        real_open = open
        lock_open_modes = []

        def recording_open(path, mode="r", *args, **kwargs):
            if Path(path) == lock_path:
                lock_open_modes.append(mode)
            return real_open(path, mode, *args, **kwargs)

        with mock.patch("builtins.open", recording_open):
            with trade_log.locked_trade_log(exclusive=False):
                pass

        self.assertEqual(lock_open_modes, ["r+b"])

    def test_read_rows_unlocked_opens_csv_with_newline_empty(self):
        path = Path(self.tmpdir.name) / "rows.csv"
        path.write_text("symbol,status\nAAPL,open\n")

        real_open = open
        open_calls = []

        def recording_open(*args, **kwargs):
            open_calls.append((args, kwargs))
            return real_open(*args, **kwargs)

        with mock.patch("builtins.open", recording_open):
            rows = trade_log._read_rows_unlocked(path)

        self.assertEqual(rows[0]["symbol"], "AAPL")
        self.assertEqual(open_calls[0][1].get("newline"), "")

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

    def test_reconcile_closes_submitted_sell_when_broker_order_fills(self):
        trade_log.log_trade({
            "timestamp": "2026-04-20T13:37:10+00:00",
            "mode": "paper",
            "symbol": "AMZN",
            "strategy": "momentum",
            "asset_class": "stock",
            "side": "buy",
            "qty": "3.139315",
            "entry_price": 250.245319,
            "order_id": "entry",
            "status": "open",
        })
        trade_log.log_trade({
            "timestamp": "2026-04-20T13:37:43+00:00",
            "mode": "paper",
            "symbol": "AMZN",
            "strategy": "momentum",
            "asset_class": "stock",
            "side": "sell",
            "qty": "3.139315",
            "entry_price": 250.245319,
            "exit_price": 248.845,
            "pnl_pct": -0.005596,
            "exit_reason": "Momentum hold expired without profit",
            "order_id": "exit-1",
            "status": "submitted",
        })

        summary = trade_log.reconcile_open_trades_with_positions(
            [],
            closed_orders=[
                SimpleNamespace(
                    id="exit-1",
                    side="sell",
                    status="filled",
                    filled_qty="3.139315",
                    filled_avg_price="248.75",
                    filled_at=datetime(2026, 4, 20, 13, 37, 43, tzinfo=timezone.utc),
                )
            ],
        )

        rows = self._read_rows()
        self.assertEqual(summary["closed_filled_sells"], 1)
        self.assertEqual(rows[0]["status"], "closed")
        self.assertEqual(rows[0]["exit_price"], "248.75")
        self.assertEqual(rows[1]["status"], "closed")
        self.assertEqual(rows[1]["exit_price"], "248.75")
        self.assertEqual(rows[1]["timestamp"], "2026-04-20T13:37:43+00:00")
        self.assertEqual(rows[1]["qty"], "3.139315")
        self.assertEqual(rows[1]["pnl_pct"], "-0.005975")

    def test_reconcile_duplicate_open_rows_preserves_zero_broker_entry(self):
        trade_log.log_trade({
            "timestamp": "2026-04-14T18:04:02+00:00",
            "mode": "paper",
            "symbol": "ZERO",
            "strategy": "test",
            "asset_class": "stock",
            "side": "buy",
            "qty": "1",
            "entry_price": "123",
            "order_id": "old-entry",
            "status": "open",
        })
        trade_log.log_trade({
            "timestamp": "2026-04-14T18:04:10+00:00",
            "mode": "paper",
            "symbol": "ZERO",
            "strategy": "test",
            "asset_class": "stock",
            "side": "buy",
            "qty": "1",
            "entry_price": "456",
            "order_id": "new-entry",
            "status": "open",
        })

        summary = trade_log.reconcile_open_trades_with_positions([
            SimpleNamespace(
                symbol="ZERO",
                qty="1",
                avg_entry_price="0",
                asset_class="AssetClass.US_EQUITY",
            )
        ])

        rows = self._read_rows()
        self.assertEqual(summary["updated_open_rows"], 1)
        self.assertEqual(summary["closed_stale_rows"], 1)
        self.assertEqual(rows[0]["status"], "closed")
        self.assertEqual(rows[0]["exit_price"], "0.0")
        self.assertEqual(rows[1]["status"], "open")
        self.assertEqual(rows[1]["entry_price"], "0")
        self.assertEqual(rows[1]["pnl_pct"], "")

    def test_get_trade_age_days_returns_business_days_for_stocks(self):
        # Entry Monday 2024-01-08, "today" = Wednesday 2024-01-10 → 2 business days
        fixed_now = datetime(2024, 1, 10, 12, 0, 0, tzinfo=timezone.utc)
        entry_ts  = datetime(2024, 1,  8,  9, 0, 0, tzinfo=timezone.utc).isoformat()

        trade_log.log_trade({
            "timestamp": entry_ts,
            "mode": "paper",
            "symbol": "AAPL",
            "strategy": "momentum",
            "asset_class": "stock",
            "side": "buy",
            "qty": 1,
            "entry_price": 100,
            "order_id": "bday-test",
            "status": "open",
        })

        with mock.patch("tracking.trade_log._utc_now", return_value=fixed_now):
            age = trade_log.get_trade_age_days("AAPL")

        self.assertEqual(age, 2.0)

    def test_get_trade_age_days_weekends_not_counted_for_stocks(self):
        # Entry Thursday 2024-01-04, "today" = Monday 2024-01-08 → 2 business days (Thu, Fri)
        fixed_now = datetime(2024, 1,  8, 12, 0, 0, tzinfo=timezone.utc)
        entry_ts  = datetime(2024, 1,  4,  9, 0, 0, tzinfo=timezone.utc).isoformat()

        trade_log.log_trade({
            "timestamp": entry_ts,
            "mode": "paper",
            "symbol": "MSFT",
            "strategy": "momentum",
            "asset_class": "stock",
            "side": "buy",
            "qty": 1,
            "entry_price": 200,
            "order_id": "weekend-test",
            "status": "open",
        })

        with mock.patch("tracking.trade_log._utc_now", return_value=fixed_now):
            age = trade_log.get_trade_age_days("MSFT")

        # 4 calendar days but only 2 business days (Sat+Sun excluded)
        self.assertEqual(age, 2.0)

    def test_get_trade_age_days_matches_crypto_symbol_variants(self):
        timestamp = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()

        trade_log.log_trade({
            "timestamp": timestamp,
            "mode": "paper",
            "symbol": "BTC/USD",
            "strategy": "ma_crossover",
            "asset_class": "crypto",
            "side": "buy",
            "qty": "0.1",
            "entry_price": "75000",
            "order_id": "crypto-aware",
            "status": "open",
        })

        age = trade_log.get_trade_age_days("BTCUSD")

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
