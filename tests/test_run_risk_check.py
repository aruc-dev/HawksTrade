import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from alpaca.common.exceptions import APIError

from scheduler import run_risk_check


class FakeMarker:
    def __init__(self):
        self.status = "ok"
        self.fields = {}

    def mark_error(self, **fields):
        self.status = "error"
        self.fields.update(fields)

    def mark_status(self, status, **fields):
        self.status = status
        self.fields.update(fields)


class RunRiskCheckTests(unittest.TestCase):
    def _api_error(self, status_code, message):
        error = json.dumps({"code": status_code, "message": message})
        http_error = SimpleNamespace(
            response=SimpleNamespace(status_code=status_code),
            request=SimpleNamespace(),
        )
        return APIError(error, http_error)

    def test_run_skips_when_daily_loss_check_fails(self):
        with (
            patch.object(run_risk_check.rm, "daily_loss_exceeded", side_effect=RuntimeError("unauthorized")),
            patch.object(run_risk_check.oe, "exit_position") as exit_position,
        ):
            run_risk_check.run(dry_run=True)

        exit_position.assert_not_called()

    def test_run_marks_retryable_category_when_fetch_positions_fails(self):
        marker = FakeMarker()
        with (
            patch.object(run_risk_check.rm, "daily_loss_exceeded", return_value=False),
            patch.object(run_risk_check, "get_open_trades", return_value=[]),
            patch.object(
                run_risk_check.ac,
                "get_all_positions",
                side_effect=self._api_error(503, "service unavailable"),
            ),
        ):
            run_risk_check.run(dry_run=True, marker=marker)

        self.assertEqual(marker.status, "error")
        self.assertEqual(marker.fields["stage"], "fetch_positions")
        self.assertEqual(marker.fields["error_category"], "server_error")
        self.assertEqual(marker.fields["status_code"], 503)
        self.assertTrue(marker.fields["retryable"])

    def test_stale_open_trade_log_rows_are_skipped_when_no_broker_positions(self):
        with (
            patch.object(run_risk_check.rm, "daily_loss_exceeded", return_value=False),
            patch.object(run_risk_check, "get_open_trades", return_value=[{"symbol": "AAPL", "side": "buy"}]),
            patch.object(run_risk_check.ac, "get_all_positions", return_value=[]),
            patch.object(run_risk_check.ac, "get_stock_latest_price") as latest_price,
            patch.object(run_risk_check.oe, "exit_position") as exit_position,
        ):
            run_risk_check.run(dry_run=True)

        latest_price.assert_not_called()
        exit_position.assert_not_called()

    def test_risk_check_reconciles_trade_log_when_no_positions(self):
        with (
            patch.object(run_risk_check.rm, "daily_loss_exceeded", return_value=False),
            patch.object(run_risk_check, "get_open_trades", return_value=[]),
            patch.object(run_risk_check.ac, "get_all_positions", return_value=[]),
            patch.object(run_risk_check.ac, "get_closed_orders", return_value=[]),
            patch.object(run_risk_check, "safe_reconcile", return_value={"positions": 0}) as safe_reconcile,
        ):
            run_risk_check.run(dry_run=False)

        safe_reconcile.assert_called_once_with(
            positions=[],
            closed_orders=[],
            context="run_risk_check.post_run",
            logger=run_risk_check.log,
        )

    def test_risk_check_skips_trade_log_reconciliation_in_dry_run(self):
        with (
            patch.object(run_risk_check.rm, "daily_loss_exceeded", return_value=False),
            patch.object(run_risk_check, "get_open_trades", return_value=[]),
            patch.object(run_risk_check.ac, "get_all_positions", return_value=[]),
            patch.object(run_risk_check, "safe_reconcile") as safe_reconcile,
        ):
            run_risk_check.run(dry_run=True)

        safe_reconcile.assert_not_called()

    def test_risk_check_uses_broker_positions_as_source_of_truth(self):
        position = SimpleNamespace(symbol="AAPL", avg_entry_price="100", asset_class="us_equity")

        with (
            patch.object(run_risk_check.rm, "daily_loss_exceeded", return_value=False),
            patch.object(run_risk_check, "get_open_trades", return_value=[]),
            patch.object(run_risk_check.ac, "get_all_positions", return_value=[position]),
            patch.object(run_risk_check.ac, "get_stock_latest_price", return_value=80),
            patch.object(run_risk_check.oe, "exit_position") as exit_position,
        ):
            run_risk_check.run(dry_run=True)

        exit_position.assert_called_once()

    def test_risk_check_uses_trade_log_entry_when_broker_entry_is_zero(self):
        position = SimpleNamespace(symbol="AAPL", avg_entry_price="0", asset_class="us_equity")
        open_trade = {
            "symbol": "AAPL",
            "side": "buy",
            "entry_price": "100",
            "asset_class": "stock",
        }

        with (
            patch.object(run_risk_check.rm, "daily_loss_exceeded", return_value=False),
            patch.object(run_risk_check, "get_open_trades", return_value=[open_trade]),
            patch.object(run_risk_check.ac, "get_all_positions", return_value=[position]),
            patch.object(run_risk_check.ac, "get_stock_latest_price", return_value=80),
            patch.object(run_risk_check.rm, "should_exit_position", return_value=(True, "Stop-loss hit")) as should_exit,
            patch.object(run_risk_check.oe, "exit_position") as exit_position,
        ):
            run_risk_check.run(dry_run=True)

        should_exit.assert_called_once_with("AAPL", 100.0, 80)
        exit_position.assert_called_once()

    def test_risk_check_uses_trade_log_entry_when_broker_entry_is_decimal_zero(self):
        position = SimpleNamespace(symbol="AAPL", avg_entry_price="0.0", asset_class="us_equity")
        open_trade = {
            "symbol": "AAPL",
            "side": "buy",
            "entry_price": "100",
            "asset_class": "stock",
        }

        with (
            patch.object(run_risk_check.rm, "daily_loss_exceeded", return_value=False),
            patch.object(run_risk_check, "get_open_trades", return_value=[open_trade]),
            patch.object(run_risk_check.ac, "get_all_positions", return_value=[position]),
            patch.object(run_risk_check.ac, "get_stock_latest_price", return_value=80),
            patch.object(run_risk_check.rm, "should_exit_position", return_value=(True, "Stop-loss hit")) as should_exit,
            patch.object(run_risk_check.oe, "exit_position") as exit_position,
        ):
            run_risk_check.run(dry_run=True)

        should_exit.assert_called_once_with("AAPL", 100.0, 80)
        exit_position.assert_called_once()

    def test_risk_check_skips_non_positive_entry_price(self):
        position = SimpleNamespace(symbol="AAPL", avg_entry_price="0", asset_class="us_equity")

        with (
            patch.object(run_risk_check.rm, "daily_loss_exceeded", return_value=False),
            patch.object(run_risk_check, "get_open_trades", return_value=[]),
            patch.object(run_risk_check.ac, "get_all_positions", return_value=[position]),
            patch.object(run_risk_check.ac, "get_stock_latest_price") as latest_price,
            patch.object(run_risk_check.oe, "exit_position") as exit_position,
            self.assertLogs("run_risk_check", level="WARNING") as logs,
        ):
            run_risk_check.run(dry_run=True)

        latest_price.assert_not_called()
        exit_position.assert_not_called()
        self.assertTrue(any("Invalid entry price for AAPL" in message for message in logs.output))

    def test_risk_check_skips_invalid_entry_price(self):
        position = SimpleNamespace(symbol="AAPL", avg_entry_price="abc", asset_class="us_equity")

        with (
            patch.object(run_risk_check.rm, "daily_loss_exceeded", return_value=False),
            patch.object(run_risk_check, "get_open_trades", return_value=[]),
            patch.object(run_risk_check.ac, "get_all_positions", return_value=[position]),
            patch.object(run_risk_check.ac, "get_stock_latest_price") as latest_price,
            patch.object(run_risk_check.oe, "exit_position") as exit_position,
            self.assertLogs("run_risk_check", level="WARNING") as logs,
        ):
            run_risk_check.run(dry_run=True)

        latest_price.assert_not_called()
        exit_position.assert_not_called()
        self.assertTrue(any("Invalid entry price for AAPL" in message for message in logs.output))

    def test_risk_check_exits_crypto_with_trade_log_symbol(self):
        position = SimpleNamespace(symbol="DOGEUSD", avg_entry_price="0.09", asset_class="crypto")
        open_trade = {
            "symbol": "DOGE/USD",
            "side": "buy",
            "entry_price": "0.09",
            "asset_class": "crypto",
        }

        with (
            patch.object(run_risk_check.rm, "daily_loss_exceeded", return_value=False),
            patch.object(run_risk_check, "get_open_trades", return_value=[open_trade]),
            patch.object(run_risk_check.ac, "get_all_positions", return_value=[position]),
            patch.object(run_risk_check.ac, "get_crypto_latest_price", return_value=0.08),
            patch.object(run_risk_check.rm, "should_exit_position", return_value=(True, "Stop-loss hit")),
            patch.object(run_risk_check.oe, "exit_position") as exit_position,
        ):
            run_risk_check.run(dry_run=True)

        exit_position.assert_called_once_with(
            "DOGE/USD", reason="Stop-loss hit", asset_class="crypto", dry_run=True
        )

    def test_run_marks_error_when_exit_is_blocked_by_pending_order_check_failure(self):
        marker = FakeMarker()
        position = SimpleNamespace(symbol="AAPL", avg_entry_price="100", asset_class="us_equity")

        with (
            patch.object(run_risk_check.rm, "daily_loss_exceeded", return_value=False),
            patch.object(run_risk_check, "get_open_trades", return_value=[]),
            patch.object(run_risk_check.ac, "get_all_positions", return_value=[position]),
            patch.object(run_risk_check.ac, "get_stock_latest_price", return_value=80),
            patch.object(run_risk_check.rm, "should_exit_position", return_value=(True, "Stop-loss hit")),
            patch.object(
                run_risk_check.oe,
                "exit_position",
                return_value={"symbol": "AAPL", "status": "pending_exit_check_failed"},
            ),
        ):
            run_risk_check.run(dry_run=True, marker=marker)

        self.assertEqual(marker.status, "error")
        self.assertEqual(marker.fields["stage"], "risk_exit")
        self.assertEqual(marker.fields["error_type"], "PendingExitOrderCheckFailed")
        self.assertEqual(marker.fields["blocked_exit_symbol"], "AAPL")

    def test_repeated_price_fetch_failures_mark_run_unhealthy(self):
        marker = FakeMarker()
        position = SimpleNamespace(symbol="AAPL", avg_entry_price="100", asset_class="us_equity")

        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "price_failures.json"
            env = {
                **os.environ,
                "HAWKSTRADE_PRICE_FAILURE_STATE_FILE": str(state_file),
                "HAWKSTRADE_PRICE_FAILURE_ALERT_THRESHOLD": "2",
            }
            with (
                patch.dict(os.environ, env, clear=True),
                patch.object(run_risk_check.rm, "daily_loss_exceeded", return_value=False),
                patch.object(run_risk_check, "get_open_trades", return_value=[]),
                patch.object(run_risk_check.ac, "get_all_positions", return_value=[position]),
                patch.object(
                    run_risk_check.ac,
                    "get_stock_latest_price",
                    side_effect=[RuntimeError("quote timeout"), RuntimeError("quote timeout")],
                ),
                patch.object(run_risk_check.oe, "exit_position") as exit_position,
            ):
                run_risk_check.run(dry_run=True, marker=marker)
                self.assertEqual(marker.status, "ok")
                run_risk_check.run(dry_run=True, marker=marker)

            exit_position.assert_not_called()
            state = json.loads(state_file.read_text(encoding="utf-8"))

        self.assertEqual(marker.status, "error")
        self.assertEqual(marker.fields["stage"], "price_fetch")
        self.assertEqual(marker.fields["error_type"], "RepeatedPriceFetchFailure")
        self.assertEqual(marker.fields["price_failure_symbol"], "AAPL")
        self.assertEqual(marker.fields["price_failure_count"], 2)
        self.assertEqual(state["symbols"]["AAPL"]["count"], 2)
        self.assertEqual(state["symbols"]["AAPL"]["status"], "nok")

    def test_successful_price_fetch_clears_previous_failure_count(self):
        position = SimpleNamespace(symbol="AAPL", avg_entry_price="100", asset_class="us_equity")

        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "price_failures.json"
            env = {
                **os.environ,
                "HAWKSTRADE_PRICE_FAILURE_STATE_FILE": str(state_file),
                "HAWKSTRADE_PRICE_FAILURE_ALERT_THRESHOLD": "2",
            }
            with (
                patch.dict(os.environ, env, clear=True),
                patch.object(run_risk_check.rm, "daily_loss_exceeded", return_value=False),
                patch.object(run_risk_check, "get_open_trades", return_value=[]),
                patch.object(run_risk_check.ac, "get_all_positions", return_value=[position]),
                patch.object(
                    run_risk_check.ac,
                    "get_stock_latest_price",
                    side_effect=[RuntimeError("quote timeout"), 101.0],
                ),
                patch.object(run_risk_check.rm, "should_exit_position", return_value=(False, "Hold")),
            ):
                run_risk_check.run(dry_run=True)
                run_risk_check.run(dry_run=True)

            state = json.loads(state_file.read_text(encoding="utf-8"))

        self.assertEqual(state["symbols"], {})


if __name__ == "__main__":
    unittest.main()
