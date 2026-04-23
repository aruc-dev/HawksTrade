import logging
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scheduler import reconcile_trade_log


class ReconcileTradeLogTests(unittest.TestCase):
    def test_run_uses_supplied_positions_without_fetching_from_broker(self):
        positions = [SimpleNamespace(symbol="AAPL")]
        summary = {
            "positions": 1,
            "updated_open_rows": 1,
            "reopened_rows": 0,
            "created_rows": 0,
            "marked_unfilled_sells": 0,
            "closed_filled_sells": 0,
            "closed_stale_rows": 0,
        }

        with (
            patch.object(reconcile_trade_log.ac, "get_all_positions") as get_all_positions,
            patch.object(reconcile_trade_log.ac, "get_open_orders") as get_open_orders,
            patch.object(reconcile_trade_log.ac, "get_closed_orders") as get_closed_orders,
            patch.object(
                reconcile_trade_log,
                "reconcile_open_trades_with_positions",
                return_value=summary,
            ) as reconcile,
            patch.object(
                reconcile_trade_log,
                "reconcile_order_intents",
                return_value={"updated_rows": 0, "open_orders": 0, "closed_orders": 0},
            ) as reconcile_intents,
        ):
            result = reconcile_trade_log.run(positions=positions)

        get_all_positions.assert_not_called()
        get_open_orders.assert_not_called()
        get_closed_orders.assert_not_called()
        reconcile.assert_called_once_with(positions, closed_orders=[])
        reconcile_intents.assert_called_once_with(open_orders=[], closed_orders=[])
        self.assertEqual(result, {**summary, "updated_order_intents": 0})

    def test_run_reconciles_order_intents_when_fetching_broker_state(self):
        positions = [SimpleNamespace(symbol="AMD")]
        open_orders = [SimpleNamespace(id="open-1")]
        closed_orders = [SimpleNamespace(id="closed-1")]
        summary = {
            "positions": 1,
            "updated_open_rows": 1,
            "reopened_rows": 0,
            "created_rows": 0,
            "marked_unfilled_sells": 0,
            "closed_filled_sells": 0,
            "closed_stale_rows": 0,
        }

        with (
            patch.object(reconcile_trade_log.ac, "get_all_positions", return_value=positions),
            patch.object(reconcile_trade_log.ac, "get_open_orders", return_value=open_orders),
            patch.object(reconcile_trade_log.ac, "get_closed_orders", return_value=closed_orders),
            patch.object(
                reconcile_trade_log,
                "reconcile_open_trades_with_positions",
                return_value=summary,
            ),
            patch.object(
                reconcile_trade_log,
                "reconcile_order_intents",
                return_value={"updated_rows": 2, "open_orders": 1, "closed_orders": 1},
            ) as reconcile_intents,
        ):
            result = reconcile_trade_log.run()

        reconcile_intents.assert_called_once_with(open_orders=open_orders, closed_orders=closed_orders)
        self.assertEqual(result["updated_order_intents"], 2)

    def test_run_continues_when_closed_order_fetch_fails(self):
        positions = [SimpleNamespace(symbol="AMD")]
        open_orders = [SimpleNamespace(id="open-1")]
        summary = {
            "positions": 1,
            "updated_open_rows": 1,
            "reopened_rows": 0,
            "created_rows": 0,
            "marked_unfilled_sells": 0,
            "closed_filled_sells": 0,
            "closed_stale_rows": 0,
        }

        with (
            patch.object(reconcile_trade_log.ac, "get_all_positions", return_value=positions),
            patch.object(reconcile_trade_log.ac, "get_open_orders", return_value=open_orders),
            patch.object(reconcile_trade_log.ac, "get_closed_orders", side_effect=RuntimeError("timeout")),
            patch.object(
                reconcile_trade_log,
                "reconcile_open_trades_with_positions",
                return_value=summary,
            ) as reconcile,
            patch.object(
                reconcile_trade_log,
                "reconcile_order_intents",
                return_value={"updated_rows": 1, "open_orders": 1, "closed_orders": 0},
            ) as reconcile_intents,
            self.assertLogs("reconcile_trade_log", level="WARNING") as logs,
        ):
            result = reconcile_trade_log.run()

        reconcile.assert_called_once_with(positions, closed_orders=[])
        reconcile_intents.assert_called_once_with(open_orders=open_orders, closed_orders=[])
        self.assertEqual(result["updated_order_intents"], 1)
        self.assertTrue(any("continuing reconciliation without them" in message for message in logs.output))

    def test_safe_reconcile_logs_and_continues_on_failure(self):
        logger = logging.getLogger("tests.reconcile_trade_log")

        with (
            patch.object(reconcile_trade_log, "run", side_effect=RuntimeError("broker down")),
            self.assertLogs(logger, level="WARNING") as captured,
        ):
            result = reconcile_trade_log.safe_reconcile(
                context="test.context",
                logger=logger,
            )

        self.assertIsNone(result)
        self.assertIn("Trade-log reconciliation failed context=test.context", captured.output[0])


if __name__ == "__main__":
    unittest.main()
