import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from alpaca.common.exceptions import APIError

from scheduler import run_scan


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


class RunScanTests(unittest.TestCase):
    def _api_error(self, status_code, message):
        error = json.dumps({"code": status_code, "message": message})
        http_error = SimpleNamespace(
            response=SimpleNamespace(status_code=status_code),
            request=SimpleNamespace(),
        )
        return APIError(error, http_error)

    def test_asset_class_matching_normalizes_stock_aliases(self):
        self.assertTrue(run_scan._asset_class_matches("stocks", "stock"))
        self.assertTrue(run_scan._asset_class_matches("stock", "stocks"))
        self.assertTrue(run_scan._asset_class_matches("crypto", "crypto"))
        self.assertFalse(run_scan._asset_class_matches("crypto", "stock"))

    def test_already_holding_normalizes_crypto_symbols(self):
        self.assertTrue(run_scan._already_holding("BTC/USD", ["BTCUSD"]))
        self.assertTrue(run_scan._already_holding("ETHUSD", ["ETH/USD"]))

    def test_register_entry_result_keeps_submitted_orders_planned_but_not_open(self):
        open_symbols = []
        planned_symbols = set()
        new_entry_symbols = set()

        run_scan._register_entry_result(
            {"symbol": "AAPL", "status": "submitted"},
            "AAPL",
            open_symbols,
            planned_symbols,
            new_entry_symbols,
        )

        self.assertEqual(planned_symbols, {run_scan.ac.normalize_symbol("AAPL")})
        self.assertEqual(open_symbols, [])
        self.assertEqual(new_entry_symbols, set())

    def test_prefetched_regime_bars_require_all_symbols_and_minimum_history(self):
        self.assertTrue(
            run_scan._prefetched_bars_are_sufficient(
                {"SPY": [object()] * 252, "QQQ": [object()] * 51},
                {"SPY": 252, "QQQ": 51},
            )
        )
        self.assertFalse(
            run_scan._prefetched_bars_are_sufficient(
                {"SPY": [object()] * 251, "QQQ": [object()] * 51},
                {"SPY": 252, "QQQ": 51},
            )
        )
        self.assertFalse(
            run_scan._prefetched_bars_are_sufficient(
                {"SPY": [object()] * 252},
                {"SPY": 252, "QQQ": 51},
            )
        )

    def test_stock_strategy_receives_none_regime_bars_when_prefetch_fails(self):
        seen_regime_bars = []

        class FakeMomentum:
            name = "momentum"
            asset_class = "stocks"

            def scan(self, universe, **kwargs):
                seen_regime_bars.append(kwargs.get("regime_bars"))
                return []

        with (
            patch.object(run_scan.ac, "is_market_open", return_value=True),
            patch.object(run_scan.ac, "get_stock_bars", side_effect=RuntimeError("bars unavailable")),
            patch.object(run_scan, "get_open_symbols", side_effect=[[], []]),
            patch.object(run_scan.rm, "daily_loss_exceeded", return_value=False),
            patch.object(run_scan, "get_stock_universe", return_value=["AAPL"]),
            patch.object(run_scan, "STOCK_STRATEGIES", [FakeMomentum()]),
            patch.object(run_scan, "get_open_trades", return_value=[]),
            patch.object(run_scan, "print_snapshot"),
        ):
            run_scan.run(run_stocks=True, run_crypto=False, dry_run=True)

        self.assertEqual(seen_regime_bars, [None])

    def test_momentum_receives_planned_stock_symbols_for_sector_filter(self):
        seen_existing_symbols = []

        class FakeMomentum:
            name = "momentum"
            asset_class = "stocks"

            def scan(self, universe, **kwargs):
                seen_existing_symbols.append(set(kwargs.get("existing_symbols", [])))
                return []

        with (
            patch.object(run_scan.ac, "is_market_open", return_value=True),
            patch.object(
                run_scan.ac,
                "get_stock_bars",
                return_value={"SPY": [object()] * 252, "QQQ": [object()] * 51},
            ),
            patch.object(run_scan, "get_open_symbols", side_effect=[["AAPL", "BTC/USD"], ["AAPL", "BTC/USD"]]),
            patch.object(run_scan, "_pending_entry_symbols", return_value={"MSFT": "stock", "ETHUSD": "crypto"}),
            patch.object(run_scan.rm, "daily_loss_exceeded", return_value=False),
            patch.object(run_scan, "get_stock_universe", return_value=["NVDA", "JPM"]),
            patch.object(run_scan, "STOCK_STRATEGIES", [FakeMomentum()]),
            patch.object(run_scan, "get_open_trades", return_value=[]),
            patch.object(run_scan, "safe_reconcile", return_value={}),
            patch.object(run_scan, "print_snapshot"),
        ):
            run_scan.run(run_stocks=True, run_crypto=False, dry_run=False)

        self.assertEqual(seen_existing_symbols, [{"AAPL", "MSFT"}])

    def test_strategy_failure_marks_scan_marker_unhealthy(self):
        class BrokenMomentum:
            name = "momentum"
            asset_class = "stocks"

            def scan(self, universe, **kwargs):
                raise RuntimeError("strategy exploded")

        marker = FakeMarker()
        with (
            patch.object(run_scan.ac, "is_market_open", return_value=True),
            patch.object(
                run_scan.ac,
                "get_stock_bars",
                return_value={"SPY": [object()] * 252, "QQQ": [object()] * 51},
            ),
            patch.object(run_scan, "get_open_symbols", side_effect=[[], []]),
            patch.object(run_scan.rm, "daily_loss_exceeded", return_value=False),
            patch.object(run_scan, "get_stock_universe", return_value=["AAPL"]),
            patch.object(run_scan, "STOCK_STRATEGIES", [BrokenMomentum()]),
            patch.object(run_scan, "get_open_trades", return_value=[]),
            patch.object(run_scan, "print_snapshot"),
        ):
            run_scan.run(run_stocks=True, run_crypto=False, dry_run=True, marker=marker)

        self.assertEqual(marker.status, "error")
        self.assertEqual(marker.fields["stage"], "stock_strategy")
        self.assertEqual(marker.fields["strategy"], "momentum")
        self.assertEqual(marker.fields["error_type"], "RuntimeError")

    def test_strategy_exit_runs_for_stock_strategy_alias(self):
        class StockStrategy:
            name = "momentum"
            asset_class = "stocks"

            def should_exit(self, symbol, entry_price):
                return True, f"exit {symbol} {entry_price}"

        open_trade = {
            "symbol": "AAPL",
            "side": "buy",
            "strategy": "momentum",
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
            name = "ma_crossover"
            asset_class = "crypto"

            def should_exit(self, symbol, entry_price):
                return True, f"exit {symbol} {entry_price}"

        open_trade = {
            "symbol": "BTC/USD",
            "side": "buy",
            "strategy": "ma_crossover",
            "entry_price": "100",
            "asset_class": "crypto",
        }

        with (
            patch.object(run_scan, "get_open_trades", return_value=[open_trade]),
            patch.object(run_scan.oe, "exit_position") as exit_position,
        ):
            run_scan._check_strategy_exits([CryptoStrategy()], ["BTCUSD"], dry_run=True)

        exit_position.assert_called_once_with(
            "BTC/USD", reason="exit BTC/USD 100.0", asset_class="crypto", dry_run=True
        )

    def test_strategy_exit_only_uses_strategy_that_opened_trade(self):
        class RSIStrategy:
            name = "rsi_reversion"
            asset_class = "stocks"

            def should_exit(self, symbol, entry_price):
                return True, "rsi exit"

        open_trade = {
            "symbol": "AAPL",
            "side": "buy",
            "strategy": "momentum",
            "entry_price": "100",
            "asset_class": "stock",
        }

        with (
            patch.object(run_scan, "get_open_trades", return_value=[open_trade]),
            patch.object(run_scan.oe, "exit_position") as exit_position,
        ):
            run_scan._check_strategy_exits([RSIStrategy()], ["AAPL"], dry_run=True)

        exit_position.assert_not_called()

    def test_strategy_exit_skips_new_entries_when_intraday_disabled(self):
        class MomentumStrategy:
            name = "momentum"
            asset_class = "stocks"

            def should_exit(self, symbol, entry_price):
                return True, "same scan exit"

        open_trade = {
            "symbol": "AAPL",
            "side": "buy",
            "strategy": "momentum",
            "entry_price": "100",
            "asset_class": "stock",
        }

        with (
            patch.object(run_scan, "get_open_trades", return_value=[open_trade]),
            patch.object(run_scan.oe, "exit_position") as exit_position,
        ):
            run_scan._check_strategy_exits(
                [MomentumStrategy()],
                ["AAPL"],
                dry_run=True,
                skip_symbols={run_scan.ac.normalize_symbol("AAPL")},
            )

        exit_position.assert_not_called()

    def test_strategy_exit_marks_error_when_exit_is_blocked_by_pending_order_check_failure(self):
        class MomentumStrategy:
            name = "momentum"
            asset_class = "stocks"

            def should_exit(self, symbol, entry_price):
                return True, "exit"

        marker = FakeMarker()
        open_trade = {
            "symbol": "AAPL",
            "side": "buy",
            "strategy": "momentum",
            "entry_price": "100",
            "asset_class": "stock",
        }

        with (
            patch.object(run_scan, "get_open_trades", return_value=[open_trade]),
            patch.object(
                run_scan.oe,
                "exit_position",
                return_value={"symbol": "AAPL", "status": "pending_exit_check_failed"},
            ),
        ):
            run_scan._check_strategy_exits([MomentumStrategy()], ["AAPL"], dry_run=True, marker=marker)

        self.assertEqual(marker.status, "error")
        self.assertEqual(marker.fields["stage"], "strategy_exit")
        self.assertEqual(marker.fields["error_type"], "PendingExitOrderCheckFailed")
        self.assertEqual(marker.fields["blocked_exit_symbol"], "AAPL")

    def test_strategy_exit_invalid_entry_price_marks_error_and_continues(self):
        class MomentumStrategy:
            name = "momentum"
            asset_class = "stocks"

            def should_exit(self, symbol, entry_price):
                return True, f"exit {symbol}"

        marker = FakeMarker()
        open_trades = [
            {
                "symbol": "AAPL",
                "side": "buy",
                "strategy": "momentum",
                "entry_price": "bad",
                "asset_class": "stock",
            },
            {
                "symbol": "MSFT",
                "side": "buy",
                "strategy": "momentum",
                "entry_price": "100",
                "asset_class": "stock",
            },
        ]

        with (
            patch.object(run_scan, "get_open_trades", return_value=open_trades),
            patch.object(run_scan.oe, "exit_position", return_value={"symbol": "MSFT", "status": "dry_run"}) as exit_position,
        ):
            run_scan._check_strategy_exits(
                [MomentumStrategy()],
                ["AAPL", "MSFT"],
                dry_run=True,
                marker=marker,
            )

        self.assertEqual(marker.status, "error")
        self.assertEqual(marker.fields["stage"], "strategy_exit")
        self.assertEqual(marker.fields["failed_exit_symbol"], "AAPL")
        exit_position.assert_called_once_with(
            "MSFT", reason="exit MSFT", asset_class="stock", dry_run=True
        )

    def test_strategy_exit_exception_marks_error_and_continues(self):
        class BrokenMomentum:
            name = "momentum"
            asset_class = "stocks"

            def should_exit(self, symbol, entry_price):
                if symbol == "AAPL":
                    raise RuntimeError("indicator failed")
                return True, f"exit {symbol}"

        marker = FakeMarker()
        open_trades = [
            {
                "symbol": "AAPL",
                "side": "buy",
                "strategy": "momentum",
                "entry_price": "100",
                "asset_class": "stock",
            },
            {
                "symbol": "MSFT",
                "side": "buy",
                "strategy": "momentum",
                "entry_price": "100",
                "asset_class": "stock",
            },
        ]

        with (
            patch.object(run_scan, "get_open_trades", return_value=open_trades),
            patch.object(run_scan.oe, "exit_position", return_value={"symbol": "MSFT", "status": "dry_run"}) as exit_position,
        ):
            run_scan._check_strategy_exits(
                [BrokenMomentum()],
                ["AAPL", "MSFT"],
                dry_run=True,
                marker=marker,
            )

        self.assertEqual(marker.status, "error")
        self.assertEqual(marker.fields["stage"], "strategy_exit")
        self.assertEqual(marker.fields["failed_exit_symbol"], "AAPL")
        self.assertEqual(marker.fields["error_type"], "RuntimeError")
        exit_position.assert_called_once_with(
            "MSFT", reason="exit MSFT", asset_class="stock", dry_run=True
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

    def test_run_marks_alpaca_error_category_on_connection_failure(self):
        marker = FakeMarker()
        with (
            patch.object(run_scan.ac, "is_market_open", side_effect=self._api_error(401, "unauthorized.")),
            patch.object(run_scan, "get_stock_universe") as get_stock_universe,
        ):
            run_scan.run(dry_run=True, marker=marker)

        self.assertEqual(marker.status, "error")
        self.assertEqual(marker.fields["stage"], "initial_connection")
        self.assertEqual(marker.fields["error_category"], "auth")
        self.assertEqual(marker.fields["status_code"], 401)
        self.assertFalse(marker.fields["retryable"])
        get_stock_universe.assert_not_called()

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

    def test_run_skips_trade_log_reconciliation_in_dry_run(self):
        with (
            patch.object(run_scan.ac, "is_market_open", return_value=True),
            patch.object(run_scan, "get_open_symbols", return_value=[]),
            patch.object(run_scan.rm, "daily_loss_exceeded", return_value=False),
            patch.object(run_scan, "get_stock_universe") as get_stock_universe,
            patch.object(run_scan, "get_open_trades", return_value=[]),
            patch.object(run_scan, "print_snapshot"),
            patch.object(run_scan, "safe_reconcile") as safe_reconcile,
        ):
            run_scan.run(run_stocks=False, run_crypto=False, dry_run=True)

        get_stock_universe.assert_not_called()
        safe_reconcile.assert_not_called()

    def test_run_reconciles_trade_log_after_non_dry_run(self):
        with (
            patch.object(run_scan.ac, "is_market_open", return_value=True),
            patch.object(run_scan, "get_open_symbols", return_value=[]),
            patch.object(run_scan.rm, "daily_loss_exceeded", return_value=False),
            patch.object(run_scan, "_pending_entry_symbols", return_value=set()),
            patch.object(run_scan, "get_stock_universe") as get_stock_universe,
            patch.object(run_scan, "get_open_trades", return_value=[]),
            patch.object(run_scan, "print_snapshot"),
            patch.object(run_scan, "safe_reconcile", return_value={"positions": 0}) as safe_reconcile,
        ):
            run_scan.run(run_stocks=False, run_crypto=False, dry_run=False)

        get_stock_universe.assert_not_called()
        safe_reconcile.assert_called_once_with(context="run_scan.post_run", logger=run_scan.log)

    def test_run_dedupes_entries_planned_in_same_scan(self):
        class FakeMomentum:
            name = "momentum"
            asset_class = "stocks"

            def scan(self, universe, **kwargs):
                return [{"symbol": "AAPL", "action": "buy"}, {"symbol": "AAPL", "action": "buy"}]

        with (
            patch.object(run_scan.ac, "is_market_open", return_value=True),
            patch.object(run_scan, "get_open_symbols", side_effect=[[], []]),
            patch.object(run_scan.rm, "daily_loss_exceeded", return_value=False),
            patch.object(run_scan, "get_stock_universe", return_value=["AAPL"]),
            patch.object(run_scan, "STOCK_STRATEGIES", [FakeMomentum()]),
            patch.object(run_scan, "get_open_trades", return_value=[]),
            patch.object(run_scan, "print_snapshot"),
            patch.object(run_scan.oe, "enter_position", return_value={"symbol": "AAPL"}) as enter_position,
        ):
            run_scan.run(run_stocks=True, run_crypto=False, dry_run=True)

        enter_position.assert_called_once()

    def test_run_respects_max_positions_with_planned_entries(self):
        class FakeMomentum:
            name = "momentum"
            asset_class = "stocks"

            def scan(self, universe, **kwargs):
                return [{"symbol": "NVDA", "action": "buy"}, {"symbol": "MSFT", "action": "buy"}]

        open_symbols = [f"SYM{i}" for i in range(run_scan.CFG["trading"]["max_positions"] - 1)]

        with (
            patch.object(run_scan.ac, "is_market_open", return_value=True),
            patch.object(run_scan, "get_open_symbols", side_effect=[open_symbols, []]),
            patch.object(run_scan.rm, "daily_loss_exceeded", return_value=False),
            patch.object(run_scan, "get_stock_universe", return_value=["NVDA", "MSFT"]),
            patch.object(run_scan, "STOCK_STRATEGIES", [FakeMomentum()]),
            patch.object(run_scan, "get_open_trades", return_value=[]),
            patch.object(run_scan, "print_snapshot"),
            patch.object(run_scan.oe, "enter_position", return_value={"symbol": "NVDA"}) as enter_position,
        ):
            run_scan.run(run_stocks=True, run_crypto=False, dry_run=True)

        enter_position.assert_called_once()

    def test_pending_entry_lookup_failure_marks_scan_unhealthy_and_blocks_entries(self):
        marker = FakeMarker()

        with (
            patch.object(run_scan.ac, "is_market_open", return_value=True),
            patch.object(run_scan, "get_open_symbols", return_value=[]),
            patch.object(run_scan.rm, "daily_loss_exceeded", return_value=False),
            patch.object(run_scan.ac, "get_open_orders", side_effect=RuntimeError("timeout")),
            patch.object(run_scan.oe, "enter_position") as enter_position,
            patch.object(run_scan, "print_snapshot"),
        ):
            run_scan.run(run_stocks=False, run_crypto=True, dry_run=False, marker=marker)

        self.assertEqual(marker.status, "error")
        self.assertEqual(marker.fields["stage"], "pending_entry_order_check")
        self.assertEqual(marker.fields["error_type"], "PendingEntryOrderCheckFailed")
        enter_position.assert_not_called()

    def test_planned_crypto_entries_count_against_crypto_cap(self):
        class FakeCrypto:
            name = "ma_crossover"
            asset_class = "crypto"

            def scan(self, universe, **kwargs):
                return [
                    {"symbol": "BTC/USD", "action": "buy"},
                    {"symbol": "SOL/USD", "action": "buy"},
                ]

        with (
            patch.dict(run_scan.CFG["trading"], {"max_positions": 10, "max_crypto_positions": 1, "min_crypto_positions": 0}),
            patch.object(run_scan.ac, "is_market_open", return_value=True),
            patch.object(run_scan.ac, "get_crypto_bars", return_value={"BTC/USD": [object()] * 21}),
            patch.object(run_scan, "get_open_symbols", side_effect=[[], []]),
            patch.object(run_scan.rm, "daily_loss_exceeded", return_value=False),
            patch.object(run_scan, "CRYPTO_STRATEGIES", [FakeCrypto()]),
            patch.object(run_scan, "get_open_trades", return_value=[]),
            patch.object(run_scan, "print_snapshot"),
            patch.object(run_scan.oe, "enter_position", return_value={"symbol": "BTC/USD", "status": "dry_run"}) as enter_position,
        ):
            run_scan.run(run_stocks=False, run_crypto=True, dry_run=True)

        enter_position.assert_called_once()

    def test_planned_stock_entries_reserve_crypto_slots(self):
        planned = {"AAPL": "stock"}
        with patch.dict(run_scan.CFG["trading"], {"max_positions": 2, "max_crypto_positions": 2, "min_crypto_positions": 1}):
            self.assertTrue(run_scan._planned_asset_class_cap_reached("stock", planned))
            self.assertFalse(run_scan._planned_asset_class_cap_reached("crypto", planned))

    def test_entry_failure_marks_scan_marker_unhealthy(self):
        class FakeMomentum:
            name = "momentum"
            asset_class = "stocks"

            def scan(self, universe, **kwargs):
                return [{"symbol": "AAPL", "action": "buy"}]

        marker = FakeMarker()
        with (
            patch.object(run_scan.ac, "is_market_open", return_value=True),
            patch.object(
                run_scan.ac,
                "get_stock_bars",
                return_value={"SPY": [object()] * 252, "QQQ": [object()] * 51},
            ),
            patch.object(run_scan, "get_open_symbols", side_effect=[[], []]),
            patch.object(run_scan.rm, "daily_loss_exceeded", return_value=False),
            patch.object(run_scan, "get_stock_universe", return_value=["AAPL"]),
            patch.object(run_scan, "STOCK_STRATEGIES", [FakeMomentum()]),
            patch.object(run_scan, "get_open_trades", return_value=[]),
            patch.object(run_scan, "print_snapshot"),
            patch.object(
                run_scan.oe,
                "enter_position",
                return_value={
                    "symbol": "AAPL",
                    "status": "entry_failed",
                    "error_type": "RuntimeError",
                    "error": "order rejected",
                },
            ),
        ):
            run_scan.run(run_stocks=True, run_crypto=False, dry_run=True, marker=marker)

        self.assertEqual(marker.status, "error")
        self.assertEqual(marker.fields["stage"], "stock_entry")
        self.assertEqual(marker.fields["failed_entry_symbol"], "AAPL")

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

    def test_momentum_hold_price_fetch_failure_marks_error_and_continues(self):
        open_trades = [
            {
                "symbol": "AAPL",
                "strategy": "momentum",
                "asset_class": "stock",
                "entry_price": "100",
            },
            {
                "symbol": "MSFT",
                "strategy": "momentum",
                "asset_class": "stock",
                "entry_price": "100",
            },
        ]
        marker = FakeMarker()

        def latest_price(symbol, asset_class):
            if symbol == "AAPL":
                raise RuntimeError("quote timeout")
            return 99

        with (
            patch.object(run_scan, "get_open_trades", return_value=open_trades),
            patch.object(run_scan, "get_trade_age_days", return_value=4),
            patch.object(run_scan, "_latest_price_for_trade", side_effect=latest_price),
            patch.object(run_scan, "_estimate_peak_price_since_entry", return_value=104),
            patch.object(run_scan.oe, "exit_position", return_value={"symbol": "MSFT", "status": "dry_run"}) as exit_position,
        ):
            run_scan._check_hold_day_exits([], dry_run=True, marker=marker)

        self.assertEqual(marker.status, "error")
        self.assertEqual(marker.fields["stage"], "hold_day_exit")
        self.assertEqual(marker.fields["failed_exit_symbol"], "AAPL")
        self.assertEqual(marker.fields["error_type"], "RuntimeError")
        exit_position.assert_called_once()

    # ── Market-closed guard ───────────────────────────────────────────────────

    def test_hold_day_exit_skipped_for_stock_when_market_closed(self):
        open_trade = {
            "symbol": "AAPL",
            "strategy": "momentum",
            "asset_class": "stock",
            "entry_price": "100",
        }

        with (
            patch.object(run_scan, "get_open_trades", return_value=[open_trade]),
            patch.object(run_scan, "get_trade_age_days", return_value=5),
            patch.object(run_scan, "_latest_price_for_trade", return_value=99),
            patch.object(run_scan, "_estimate_peak_price_since_entry", return_value=104),
            patch.object(run_scan.oe, "exit_position") as exit_position,
        ):
            run_scan._check_hold_day_exits([], dry_run=True, market_open=False)

        exit_position.assert_not_called()

    def test_hold_day_exit_proceeds_for_crypto_when_market_closed(self):
        open_trade = {
            "symbol": "BTC/USD",
            "strategy": "range_breakout",
            "asset_class": "crypto",
            "entry_price": "50000",
        }

        with (
            patch.object(run_scan, "get_open_trades", return_value=[open_trade]),
            patch.object(run_scan, "get_trade_age_days", return_value=4),
            patch.object(run_scan.oe, "exit_position") as exit_position,
        ):
            run_scan._check_hold_day_exits([], dry_run=True, market_open=False)

        exit_position.assert_called_once()

    # ── MA crossover HOLD_DAYS (HIGH fix) ─────────────────────────────────────

    def test_hold_day_exit_fires_for_ma_crossover_after_hold_period(self):
        open_trade = {
            "symbol": "BTC/USD",
            "strategy": "ma_crossover",
            "asset_class": "crypto",
            "entry_price": "50000",
        }

        with (
            patch.object(run_scan, "get_open_trades", return_value=[open_trade]),
            patch.object(run_scan, "get_trade_age_days", return_value=13),  # > hold_days=12
            patch.object(run_scan, "_latest_price_for_trade", return_value=48000),  # flat/losing
            patch.object(run_scan, "_estimate_peak_price_since_entry", return_value=51000),
            patch.object(run_scan.oe, "exit_position") as exit_position,
        ):
            run_scan._check_hold_day_exits([], dry_run=True, market_open=True)

        exit_position.assert_called_once()

    def test_hold_day_exit_does_not_fire_for_ma_crossover_within_hold_period(self):
        open_trade = {
            "symbol": "BTC/USD",
            "strategy": "ma_crossover",
            "asset_class": "crypto",
            "entry_price": "50000",
        }

        with (
            patch.object(run_scan, "get_open_trades", return_value=[open_trade]),
            patch.object(run_scan, "get_trade_age_days", return_value=5),  # < hold_days=12
            patch.object(run_scan, "_latest_price_for_trade", return_value=48000),
            patch.object(run_scan, "_estimate_peak_price_since_entry", return_value=51000),
            patch.object(run_scan.oe, "exit_position") as exit_position,
        ):
            run_scan._check_hold_day_exits([], dry_run=True, market_open=True)

        exit_position.assert_not_called()


if __name__ == "__main__":
    unittest.main()
