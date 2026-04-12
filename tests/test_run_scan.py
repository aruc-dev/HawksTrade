import unittest
from unittest.mock import patch

from scheduler import run_scan


class RunScanTests(unittest.TestCase):
    def test_asset_class_matching_normalizes_stock_aliases(self):
        self.assertTrue(run_scan._asset_class_matches("stocks", "stock"))
        self.assertTrue(run_scan._asset_class_matches("stock", "stocks"))
        self.assertTrue(run_scan._asset_class_matches("crypto", "crypto"))
        self.assertFalse(run_scan._asset_class_matches("crypto", "stock"))

    def test_already_holding_normalizes_crypto_symbols(self):
        self.assertTrue(run_scan._already_holding("BTC/USD", ["BTCUSD"]))
        self.assertTrue(run_scan._already_holding("ETHUSD", ["ETH/USD"]))

    def test_strategy_exit_runs_for_stock_strategy_alias(self):
        class StockStrategy:
            asset_class = "stocks"

            def should_exit(self, symbol, entry_price):
                return True, f"exit {symbol} {entry_price}"

        open_trade = {
            "symbol": "AAPL",
            "side": "buy",
            "entry_price": "100",
            "asset_class": "stock",
        }

        with (
            patch.object(run_scan, "get_open_trades", return_value=[open_trade]),
            patch.object(run_scan.oe, "exit_position") as exit_position,
        ):
            run_scan._check_strategy_exits([StockStrategy()], ["AAPL"], dry_run=True)

        exit_position.assert_called_once_with(
            "AAPL", reason="exit AAPL 100.0", asset_class="stock", dry_run=True
        )

    def test_strategy_exit_matches_crypto_symbol_formats(self):
        class CryptoStrategy:
            asset_class = "crypto"

            def should_exit(self, symbol, entry_price):
                return True, f"exit {symbol} {entry_price}"

        open_trade = {
            "symbol": "BTC/USD",
            "side": "buy",
            "entry_price": "100",
            "asset_class": "crypto",
        }

        with (
            patch.object(run_scan, "get_open_trades", return_value=[open_trade]),
            patch.object(run_scan.oe, "exit_position") as exit_position,
        ):
            run_scan._check_strategy_exits([CryptoStrategy()], ["BTCUSD"], dry_run=True)

        exit_position.assert_called_once_with(
            "BTCUSD", reason="exit BTCUSD 100.0", asset_class="crypto", dry_run=True
        )

    def test_run_skips_when_market_connection_fails(self):
        with (
            patch.object(run_scan.ac, "is_market_open", side_effect=RuntimeError("unauthorized")),
            patch.object(run_scan, "get_stock_universe") as get_stock_universe,
            patch.object(run_scan.oe, "enter_position") as enter_position,
            patch.object(run_scan.oe, "exit_position") as exit_position,
        ):
            run_scan.run(dry_run=True)

        get_stock_universe.assert_not_called()
        enter_position.assert_not_called()
        exit_position.assert_not_called()

    def test_crypto_only_does_not_build_stock_universe(self):
        with (
            patch.object(run_scan.ac, "is_market_open", return_value=True),
            patch.object(run_scan, "get_open_symbols", return_value=[]),
            patch.object(run_scan.rm, "daily_loss_exceeded", return_value=False),
            patch.object(run_scan, "get_stock_universe") as get_stock_universe,
            patch.object(run_scan, "get_open_trades", return_value=[]),
            patch.object(run_scan, "print_snapshot"),
        ):
            run_scan.run(run_stocks=False, run_crypto=False, dry_run=True)

        get_stock_universe.assert_not_called()

    def test_momentum_hold_extends_profitable_trade(self):
        open_trade = {
            "symbol": "AAPL",
            "strategy": "momentum",
            "asset_class": "stock",
            "entry_price": "100",
        }

        with (
            patch.object(run_scan, "get_open_trades", return_value=[open_trade]),
            patch.object(run_scan, "get_trade_age_days", return_value=4),
            patch.object(run_scan, "_latest_price_for_trade", return_value=103),
            patch.object(run_scan, "_estimate_peak_price_since_entry", return_value=104),
            patch.object(run_scan.oe, "exit_position") as exit_position,
        ):
            run_scan._check_hold_day_exits([], dry_run=True)

        exit_position.assert_not_called()

    def test_momentum_hold_exits_loser_after_min_hold(self):
        open_trade = {
            "symbol": "AAPL",
            "strategy": "momentum",
            "asset_class": "stock",
            "entry_price": "100",
        }

        with (
            patch.object(run_scan, "get_open_trades", return_value=[open_trade]),
            patch.object(run_scan, "get_trade_age_days", return_value=4),
            patch.object(run_scan, "_latest_price_for_trade", return_value=99),
            patch.object(run_scan, "_estimate_peak_price_since_entry", return_value=104),
            patch.object(run_scan.oe, "exit_position") as exit_position,
        ):
            run_scan._check_hold_day_exits([], dry_run=True)

        exit_position.assert_called_once()


if __name__ == "__main__":
    unittest.main()
