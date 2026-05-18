import csv
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core import order_executor
from tracking import order_intents, trade_log


class OrderExecutorTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.original_trade_log = trade_log.TRADE_LOG
        trade_log.TRADE_LOG = Path(self.tmpdir.name) / "trades.csv"
        self.addCleanup(setattr, trade_log, "TRADE_LOG", self.original_trade_log)
        self.original_order_intents = order_intents.ORDER_INTENTS
        order_intents.ORDER_INTENTS = Path(self.tmpdir.name) / "order_intents.csv"
        self.addCleanup(setattr, order_intents, "ORDER_INTENTS", self.original_order_intents)
        account_patch = patch.object(
            order_executor.ac,
            "get_account",
            return_value=SimpleNamespace(portfolio_value="10000", cash="10000", buying_power="10000"),
        )
        account_patch.start()
        self.addCleanup(account_patch.stop)
        open_orders_patch = patch.object(order_executor.ac, "get_open_orders", return_value=[])
        open_orders_patch.start()
        self.addCleanup(open_orders_patch.stop)

        trade_log.log_trade({
            "timestamp": "2026-04-10T12:00:00",
            "mode": "paper",
            "symbol": "AAPL",
            "strategy": "momentum",
            "asset_class": "stock",
            "side": "buy",
            "qty": 2,
            "entry_price": 100,
            "risk_tier": "exploration:0",
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
        self.assertEqual(result["risk_tier"], "exploration:0")

        with open(trade_log.TRADE_LOG, "r") as f:
            rows = list(csv.DictReader(f))

        self.assertEqual(rows[0]["side"], "buy")
        self.assertEqual(rows[0]["status"], "closed")
        self.assertEqual(float(rows[0]["exit_price"]), 110.0)
        self.assertEqual(rows[1]["side"], "sell")
        self.assertEqual(rows[1]["status"], "closed")
        self.assertEqual(rows[1]["risk_tier"], "exploration:0")
        self.assertEqual(trade_log.get_closed_trades("momentum")[0]["risk_tier"], "exploration:0")

    def test_exit_position_keeps_trade_open_when_exit_order_not_filled(self):
        position = SimpleNamespace(qty="2", avg_entry_price="100")
        order = SimpleNamespace(id="exit-1", status="pending_new", filled_qty="0")

        with (
            patch.object(order_executor.ac, "get_position", return_value=position),
            patch.object(order_executor.ac, "get_stock_latest_price", return_value=110),
            patch.object(order_executor.ac, "get_open_orders", return_value=[]),
            patch.object(order_executor.ac, "place_limit_order", return_value=order),
            self.assertLogs("core.order_executor", level="INFO") as logs,
        ):
            result = order_executor.exit_position("AAPL", "take profit", dry_run=False)

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "submitted")
        self.assertTrue(any("Exit order submitted for AAPL" in message for message in logs.output))
        self.assertFalse(any("WARNING:core.order_executor:Exit order submitted for AAPL" in message for message in logs.output))

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

    def test_exit_position_blocks_when_pending_exit_lookup_fails(self):
        position = SimpleNamespace(qty="2", avg_entry_price="100")

        with (
            patch.object(order_executor.ac, "get_position", return_value=position),
            patch.object(order_executor.ac, "get_stock_latest_price", return_value=110),
            patch.object(order_executor.ac, "get_open_orders", side_effect=RuntimeError("timeout")),
            patch.object(order_executor.ac, "place_limit_order") as place_limit_order,
        ):
            result = order_executor.exit_position("AAPL", "take profit", dry_run=False)

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "pending_exit_check_failed")
        place_limit_order.assert_not_called()

        with open(trade_log.TRADE_LOG, "r") as f:
            rows = list(csv.DictReader(f))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "open")

    def test_exit_position_rejects_short_position(self):
        position = SimpleNamespace(qty="-2", avg_entry_price="100")

        with (
            patch.object(order_executor.ac, "get_position", return_value=position),
            patch.object(order_executor.ac, "get_stock_latest_price") as latest_price,
            patch.object(order_executor.ac, "place_limit_order") as place_limit_order,
            self.assertLogs("core.order_executor", level="ERROR") as logs,
        ):
            result = order_executor.exit_position("AAPL", "take profit", dry_run=False)

        self.assertIsNone(result)
        latest_price.assert_not_called()
        place_limit_order.assert_not_called()
        self.assertTrue(any("non-long position" in message for message in logs.output))

    def test_exit_position_rejects_non_positive_latest_price_before_submit(self):
        position = SimpleNamespace(qty="2", avg_entry_price="100")

        with (
            patch.object(order_executor.ac, "get_position", return_value=position),
            patch.object(order_executor.ac, "get_stock_latest_price", return_value=0),
            patch.object(order_executor.ac, "place_limit_order") as place_limit_order,
        ):
            result = order_executor.exit_position("AAPL", "take profit", dry_run=False)

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "invalid_exit_price")
        place_limit_order.assert_not_called()

    def test_exit_position_force_market_bypasses_limit_order_type(self):
        position = SimpleNamespace(qty="2", avg_entry_price="100")
        order = SimpleNamespace(id="exit-market", status="filled", filled_qty="2")

        with (
            patch.object(order_executor.ac, "get_position", return_value=position),
            patch.object(order_executor.ac, "get_stock_latest_price", return_value=110),
            patch.object(order_executor.ac, "get_open_orders", return_value=[]),
            patch.object(order_executor.ac, "place_market_order", return_value=order) as place_market_order,
            patch.object(order_executor.ac, "place_limit_order") as place_limit_order,
        ):
            result = order_executor.exit_position("AAPL", "emergency", dry_run=False, force_market=True)

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "closed")
        place_market_order.assert_called_once()
        place_limit_order.assert_not_called()

    def test_exit_position_uses_requested_limit_price_and_filled_avg_for_pnl(self):
        position = SimpleNamespace(qty="2", avg_entry_price="100")
        order = SimpleNamespace(id="exit-limit", status="filled", filled_qty="2", filled_avg_price="108.5")

        with (
            patch.object(order_executor.ac, "get_position", return_value=position),
            patch.object(order_executor.ac, "get_stock_latest_price", return_value=109),
            patch.object(order_executor.ac, "get_open_orders", return_value=[]),
            patch.object(order_executor.ac, "place_limit_order", return_value=order) as place_limit_order,
            patch.object(order_executor.ac, "place_market_order") as place_market_order,
        ):
            result = order_executor.exit_position("AAPL", "manual limit", dry_run=False, limit_price=108.25)

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "closed")
        self.assertEqual(result["order_type"], "limit")
        self.assertEqual(result["limit_price"], 108.25)
        self.assertEqual(float(result["exit_price"]), 108.5)
        self.assertEqual(float(result["pnl_pct"]), 0.085)
        place_limit_order.assert_called_once()
        self.assertEqual(place_limit_order.call_args.args[3], 108.25)
        place_market_order.assert_not_called()

        rows = [row for row in trade_log.read_trade_rows() if row["symbol"] == "AAPL"]
        self.assertEqual(rows[0]["status"], "closed")
        self.assertEqual(float(rows[0]["exit_price"]), 108.5)
        self.assertEqual(rows[1]["side"], "sell")
        self.assertEqual(float(rows[1]["exit_price"]), 108.5)

    def test_exit_position_rejects_invalid_custom_limit_price(self):
        position = SimpleNamespace(qty="2", avg_entry_price="100")

        with (
            patch.object(order_executor.ac, "get_position", return_value=position),
            patch.object(order_executor.ac, "get_stock_latest_price", return_value=110),
            patch.object(order_executor.ac, "place_limit_order") as place_limit_order,
            patch.object(order_executor.ac, "place_market_order") as place_market_order,
        ):
            result = order_executor.exit_position("AAPL", "manual limit", dry_run=False, limit_price=0)

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "invalid_limit_price")
        place_limit_order.assert_not_called()
        place_market_order.assert_not_called()

    def test_enter_position_logs_submitted_buy_without_open_exposure(self):
        order = SimpleNamespace(id="entry-submitted", status="pending_new", filled_qty="0")

        with (
            patch.object(order_executor.ac, "get_stock_latest_price", return_value=100),
            patch.object(order_executor.rm, "pre_trade_check", return_value={"approved": True, "qty": 4}),
            patch.object(order_executor.rm, "cap_position_qty", return_value=4),
            patch.object(order_executor.ac, "place_limit_order", return_value=order),
            self.assertLogs("core.order_executor", level="INFO") as logs,
        ):
            result = order_executor.enter_position("MSFT", "gap_up", dry_run=False)

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["qty"], 1.0)
        self.assertEqual(result["risk_tier"], "exploration:0")
        self.assertTrue(any("Entry order submitted for MSFT" in message for message in logs.output))
        self.assertFalse(any("WARNING:core.order_executor:Entry order submitted for MSFT" in message for message in logs.output))

        rows = [row for row in trade_log.read_trade_rows() if row["symbol"] == "MSFT"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "submitted")
        self.assertEqual(rows[0]["qty"], "1.0")
        self.assertEqual(rows[0]["risk_tier"], "exploration:0")
        self.assertFalse(any(row["symbol"] == "MSFT" for row in trade_log.get_open_trades()))

    def test_enter_position_blocks_when_scaled_notional_below_minimum(self):
        with (
            patch.object(order_executor.ac, "get_stock_latest_price", return_value=100),
            patch.object(order_executor.rm, "pre_trade_check", return_value={"approved": True, "qty": 2}),
            patch.object(order_executor.rm, "cap_position_qty", return_value=2),
            patch.object(order_executor.ac, "place_limit_order") as place_limit_order,
            self.assertLogs("core.order_executor", level="INFO") as logs,
        ):
            result = order_executor.enter_position("MSFT", "gap_up", dry_run=False)

        self.assertIsNone(result)
        place_limit_order.assert_not_called()
        self.assertTrue(any("scaled notional $50.00 is below min trade value $100.00" in message for message in logs.output))
        self.assertFalse(any(row["symbol"] == "MSFT" for row in trade_log.read_trade_rows()))

    def test_enter_position_logs_base_cap_and_sample_size_scaling_separately(self):
        order = SimpleNamespace(id="entry-scaled", status="filled", filled_qty="1.0")

        with (
            patch.object(order_executor.ac, "get_stock_latest_price", return_value=100),
            patch.object(order_executor.rm, "pre_trade_check", return_value={"approved": True, "qty": 4}),
            patch.object(order_executor.rm, "cap_position_qty", return_value=4),
            patch.object(order_executor.ac, "place_limit_order", return_value=order),
            self.assertLogs("core.order_executor", level="INFO") as logs,
        ):
            result = order_executor.enter_position(
                "MSFT",
                "gap_up",
                dry_run=False,
                suggested_qty=10,
                closed_trades_count=0,
            )

        self.assertIsNotNone(result)
        self.assertEqual(result["qty"], 1.0)
        self.assertTrue(
            any(
                "Entry max-position cap for MSFT: requested=10.0 base_capped=4" in message
                for message in logs.output
            )
        )
        self.assertTrue(
            any(
                "Sample-size risk tier for gap_up/MSFT" in message
                and "base_qty=4" in message
                and "scaled_qty=1.0" in message
                for message in logs.output
            )
        )

    def test_closed_trades_for_strategy_supports_legacy_getter_without_strategy_kwarg(self):
        def legacy_getter(*args, **kwargs):
            if kwargs:
                raise TypeError("get_closed_trades() got an unexpected keyword argument 'strategy'")
            return [{"strategy": "gap_up"}, {"strategy": "momentum"}, {"strategy": "gap_up"}]

        with patch.object(order_executor, "get_closed_trades", side_effect=legacy_getter):
            rows = order_executor._closed_trades_for_strategy("gap_up")

        self.assertEqual(rows, [{"strategy": "gap_up"}, {"strategy": "gap_up"}])

    def test_closed_trades_for_strategy_reraises_unrelated_type_error(self):
        with patch.object(order_executor, "get_closed_trades", side_effect=TypeError("bad trade row")):
            with self.assertRaisesRegex(TypeError, "bad trade row"):
                order_executor._closed_trades_for_strategy("gap_up")

    def test_enter_position_uses_precomputed_closed_trade_count(self):
        with (
            patch.object(order_executor.ac, "get_stock_latest_price", return_value=100),
            patch.object(order_executor.rm, "pre_trade_check", return_value={"approved": True, "qty": 2}),
            patch.object(order_executor.rm, "cap_position_qty", return_value=2),
            patch.object(order_executor, "get_closed_trades", side_effect=AssertionError("should not read CSV")),
            patch.object(order_executor, "effective_risk_for", wraps=order_executor.effective_risk_for) as effective_risk,
        ):
            result = order_executor.enter_position(
                "MSFT",
                "gap_up",
                dry_run=True,
                closed_trades_count=42,
            )

        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(effective_risk.call_args.kwargs["closed_trades"], 42)

    def test_enter_position_blocks_live_strategy_when_readiness_gate_fails_before_price_fetch(self):
        with (
            patch.object(order_executor, "MODE", "live"),
            patch.dict(
                order_executor.CFG,
                {
                    "strategies": {
                        "range_breakout": {
                            "live_readiness": {
                                "enabled": True,
                                "min_closed_paper_trades": 1,
                                "min_paper_days": 0,
                            },
                        },
                    },
                },
                clear=False,
            ),
            patch.object(
                order_executor.ac,
                "get_crypto_latest_price",
                side_effect=AssertionError("price lookup should not run"),
            ),
            patch.object(order_executor.ac, "place_limit_order") as place_limit_order,
        ):
            result = order_executor.enter_position("BTC/USD", "range_breakout", asset_class="crypto")

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "strategy_readiness_blocked")
        self.assertEqual(result["readiness_code"], "strategy_live_readiness_blocked")
        place_limit_order.assert_not_called()

    def test_enter_position_blocks_duplicate_pending_entry_before_submit(self):
        pending_order = SimpleNamespace(id="pending-buy", symbol="MSFT", side="buy", status="new")

        with (
            patch.object(order_executor.ac, "get_stock_latest_price", return_value=100),
            patch.object(order_executor.rm, "pre_trade_check", return_value={"approved": True, "qty": 4}),
            patch.object(order_executor.rm, "cap_position_qty", return_value=4),
            patch.object(order_executor.ac, "get_open_orders", return_value=[pending_order]),
            patch.object(order_executor.ac, "place_limit_order") as place_limit_order,
            self.assertLogs("core.order_executor", level="WARNING") as logs,
        ):
            result = order_executor.enter_position("MSFT", "gap_up", dry_run=False)

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "order_governor_blocked")
        self.assertEqual(result["governor_code"], "duplicate_pending_entry")
        place_limit_order.assert_not_called()
        self.assertTrue(any("Order governor blocked buy MSFT" in message for message in logs.output))

    def test_enter_position_blocks_when_governor_account_lookup_fails(self):
        with (
            patch.object(order_executor.ac, "get_stock_latest_price", return_value=100),
            patch.object(order_executor.rm, "pre_trade_check", return_value={"approved": True, "qty": 4}),
            patch.object(order_executor.rm, "cap_position_qty", return_value=4),
            patch.object(order_executor.ac, "get_account", side_effect=RuntimeError("account timeout")),
            patch.object(order_executor.ac, "place_limit_order") as place_limit_order,
        ):
            result = order_executor.enter_position("MSFT", "gap_up", dry_run=False)

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "order_governor_blocked")
        self.assertEqual(result["governor_code"], "account_lookup_failed")
        place_limit_order.assert_not_called()

    def test_enter_position_skips_governor_broker_lookups_when_disabled(self):
        order = SimpleNamespace(id="entry-filled", status="filled", filled_qty="2", filled_avg_price="100")

        with (
            patch.dict(order_executor.CFG, {"order_governor": {"enabled": False}}),
            patch.object(order_executor.ac, "get_stock_latest_price", return_value=100),
            patch.object(order_executor.rm, "pre_trade_check", return_value={"approved": True, "qty": 8}),
            patch.object(order_executor.rm, "cap_position_qty", return_value=8),
            patch.object(order_executor.ac, "get_account", side_effect=AssertionError("account lookup should not run")),
            patch.object(order_executor.ac, "get_open_orders", side_effect=AssertionError("order lookup should not run")),
            patch.object(order_executor.ac, "place_limit_order", return_value=order) as place_limit_order,
        ):
            result = order_executor.enter_position("MSFT", "gap_up", dry_run=False)

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "open")
        place_limit_order.assert_called_once()

    def test_exit_position_reports_account_lookup_failure_as_exit_check_failure(self):
        position = SimpleNamespace(qty="2", avg_entry_price="100")

        with (
            patch.object(order_executor.ac, "get_position", return_value=position),
            patch.object(order_executor.ac, "get_stock_latest_price", return_value=110),
            patch.object(order_executor.ac, "get_account", side_effect=RuntimeError("account timeout")),
            patch.object(order_executor.ac, "place_limit_order") as place_limit_order,
        ):
            result = order_executor.exit_position("AAPL", "risk exit", dry_run=False)

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "pending_exit_check_failed")
        self.assertEqual(result["governor_code"], "account_lookup_failed")
        place_limit_order.assert_not_called()

    def test_exit_position_skips_governor_broker_lookups_when_disabled(self):
        position = SimpleNamespace(qty="2", avg_entry_price="100")
        order = SimpleNamespace(id="exit-1", status="filled", filled_qty="2")

        with (
            patch.dict(order_executor.CFG, {"order_governor": {"enabled": False}}),
            patch.object(order_executor.ac, "get_position", return_value=position),
            patch.object(order_executor.ac, "get_stock_latest_price", return_value=110),
            patch.object(order_executor.ac, "get_account", side_effect=AssertionError("account lookup should not run")),
            patch.object(order_executor.ac, "get_open_orders", side_effect=AssertionError("order lookup should not run")),
            patch.object(order_executor.ac, "place_limit_order", return_value=order) as place_limit_order,
        ):
            result = order_executor.exit_position("AAPL", "risk exit", dry_run=False)

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "closed")
        place_limit_order.assert_called_once()

    def test_enter_position_logs_filled_buy_as_open_with_fill_details(self):
        order = SimpleNamespace(
            id="entry-filled",
            status="filled",
            filled_qty="1.5",
            filled_avg_price="101.25",
        )

        with (
            patch.object(order_executor.ac, "get_stock_latest_price", return_value=100),
            patch.object(order_executor.rm, "pre_trade_check", return_value={"approved": True, "qty": 8}),
            patch.object(order_executor.rm, "cap_position_qty", return_value=8),
            patch.object(order_executor.ac, "place_limit_order", return_value=order),
        ):
            result = order_executor.enter_position("MSFT", "gap_up", dry_run=False)

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "open")
        self.assertEqual(result["qty"], 1.5)
        self.assertEqual(result["entry_price"], 101.25)

        rows = [row for row in trade_log.get_open_trades() if row["symbol"] == "MSFT"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "open")
        self.assertEqual(rows[0]["qty"], "1.5")
        self.assertEqual(rows[0]["entry_price"], "101.25")

    def test_enter_position_logs_wider_stock_atr_stop(self):
        order = SimpleNamespace(
            id="entry-filled",
            status="filled",
            filled_qty="1",
            filled_avg_price="100",
        )

        with (
            patch.object(order_executor.ac, "get_stock_latest_price", return_value=100),
            patch.object(order_executor.rm, "pre_trade_check", return_value={"approved": True, "qty": 4}),
            patch.object(order_executor.rm, "cap_position_qty", return_value=4),
            patch.object(order_executor.ac, "place_limit_order", return_value=order),
        ):
            result = order_executor.enter_position(
                "MSFT",
                "gap_up",
                dry_run=False,
                atr_stop_price=90.0,
            )

        self.assertEqual(result["stop_loss"], 90.0)

    def test_enter_position_logs_wider_crypto_custom_stop(self):
        order = SimpleNamespace(
            id="entry-filled",
            status="filled",
            filled_qty="1",
            filled_avg_price="100",
        )

        with (
            patch.object(order_executor.ac, "get_crypto_latest_price", return_value=100),
            patch.object(order_executor.rm, "pre_trade_check", return_value={"approved": True, "qty": 4}),
            patch.object(order_executor.rm, "cap_position_qty", return_value=4),
            patch.object(order_executor.ac, "place_limit_order", return_value=order),
        ):
            result = order_executor.enter_position(
                "BTC/USD",
                "range_breakout",
                asset_class="crypto",
                dry_run=False,
                atr_stop_price=90.0,
            )

        self.assertEqual(result["stop_loss"], 90.0)

    def test_enter_position_logs_partial_buy_with_filled_quantity_only(self):
        order = SimpleNamespace(
            id="entry-partial",
            status="partially_filled",
            filled_qty="0.25",
            filled_avg_price="100.5",
        )

        with (
            patch.object(order_executor.ac, "get_stock_latest_price", return_value=100),
            patch.object(order_executor.rm, "pre_trade_check", return_value={"approved": True, "qty": 4}),
            patch.object(order_executor.rm, "cap_position_qty", return_value=4),
            patch.object(order_executor.ac, "place_limit_order", return_value=order),
        ):
            result = order_executor.enter_position("MSFT", "gap_up", dry_run=False)

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "partially_filled")
        self.assertEqual(result["qty"], 0.25)
        self.assertEqual(result["risk_tier"], "exploration:0")

        rows = [row for row in trade_log.get_open_trades() if row["symbol"] == "MSFT"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "partially_filled")
        self.assertEqual(rows[0]["qty"], "0.25")
        self.assertEqual(rows[0]["risk_tier"], "exploration:0")

    def test_enter_position_reuses_client_order_id_after_submit_failure(self):
        seen_client_ids = []
        order = SimpleNamespace(id="entry-retry", status="filled", filled_qty="2")

        def _place_limit_order(*args, **kwargs):
            seen_client_ids.append(kwargs["client_order_id"])
            if len(seen_client_ids) == 1:
                raise RuntimeError("lost response")
            return order

        with (
            patch.dict(os.environ, {"HAWKSTRADE_RUN_ID": "run-retry"}),
            patch.object(order_executor.ac, "get_stock_latest_price", return_value=100),
            patch.object(order_executor.rm, "pre_trade_check", return_value={"approved": True, "qty": 8}),
            patch.object(order_executor.rm, "cap_position_qty", return_value=8),
            patch.object(order_executor.ac, "place_limit_order", side_effect=_place_limit_order),
        ):
            first = order_executor.enter_position("MSFT", "gap_up", dry_run=False)
            second = order_executor.enter_position("MSFT", "gap_up", dry_run=False)

        rows = order_intents.read_order_intents()

        self.assertIsNotNone(first)
        self.assertEqual(first["status"], "entry_failed")
        self.assertIsNotNone(second)
        self.assertEqual(len(seen_client_ids), 2)
        self.assertEqual(seen_client_ids[0], seen_client_ids[1])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["client_order_id"], seen_client_ids[0])
        self.assertEqual(rows[0]["status"], "filled")


if __name__ == "__main__":
    unittest.main()
