import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scheduler import run_risk_check


class RunRiskCheckTests(unittest.TestCase):
    def test_run_skips_when_daily_loss_check_fails(self):
        with (
            patch.object(run_risk_check.rm, "daily_loss_exceeded", side_effect=RuntimeError("unauthorized")),
            patch.object(run_risk_check.oe, "exit_position") as exit_position,
        ):
            run_risk_check.run(dry_run=True)

        exit_position.assert_not_called()

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


if __name__ == "__main__":
    unittest.main()
