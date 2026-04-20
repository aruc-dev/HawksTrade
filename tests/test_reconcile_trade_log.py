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
            "closed_stale_rows": 0,
        }

        with (
            patch.object(reconcile_trade_log.ac, "get_all_positions") as get_all_positions,
            patch.object(
                reconcile_trade_log,
                "reconcile_open_trades_with_positions",
                return_value=summary,
            ) as reconcile,
        ):
            result = reconcile_trade_log.run(positions=positions)

        get_all_positions.assert_not_called()
        reconcile.assert_called_once_with(positions)
        self.assertEqual(result, summary)

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
