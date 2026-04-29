import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from core import risk_manager


class RiskManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.original_start = risk_manager._session_start_value
        self.original_date = risk_manager._session_date
        self.original_baseline_file = risk_manager.DAILY_BASELINE_FILE
        self.addCleanup(setattr, risk_manager, "_session_start_value", self.original_start)
        self.addCleanup(setattr, risk_manager, "_session_date", self.original_date)
        self.addCleanup(setattr, risk_manager, "DAILY_BASELINE_FILE", self.original_baseline_file)
        risk_manager.DAILY_BASELINE_FILE = Path(self.tmpdir.name) / "daily_loss_baseline.json"

    def test_daily_loss_check_handles_zero_start_value(self):
        today = date(2026, 4, 17)
        risk_manager._session_start_value = 0
        risk_manager._session_date = today

        with (
            patch.object(risk_manager, "_current_trading_session_date", return_value=today),
            patch.object(risk_manager.ac, "get_portfolio_value", return_value=0),
        ):
            self.assertFalse(risk_manager.daily_loss_exceeded())

    def test_position_size_rejects_non_positive_price(self):
        self.assertEqual(risk_manager.calculate_position_size(0), 0.0)
        self.assertEqual(risk_manager.calculate_position_size(-5), 0.0)

    def test_kelly_position_size_rejects_non_positive_price_before_division(self):
        with self.assertLogs("risk_manager", level="WARNING") as logs:
            qty = risk_manager.kelly_position_size(price=0)

        self.assertEqual(qty, 0.0)
        self.assertTrue(any("Invalid price 0" in message for message in logs.output))

    def test_daily_loss_baseline_persists_across_process_state_reset(self):
        risk_manager._session_start_value = None
        risk_manager._session_date = None

        with patch.object(risk_manager.ac, "get_portfolio_value", side_effect=[100000, 96000]):
            self.assertFalse(risk_manager.daily_loss_exceeded())

        risk_manager._session_start_value = None
        risk_manager._session_date = None

        with patch.object(risk_manager.ac, "get_portfolio_value", return_value=94000):
            self.assertTrue(risk_manager.daily_loss_exceeded())

    def test_trading_session_date_uses_new_york_not_host_utc(self):
        utc_evening = datetime(2026, 4, 18, 1, 30, tzinfo=timezone.utc)

        session_date = risk_manager._current_trading_session_date(utc_evening)

        self.assertEqual(session_date, date(2026, 4, 17))

    def test_daily_loss_baseline_does_not_reset_at_utc_midnight(self):
        session_date = date(2026, 4, 17)
        risk_manager._session_start_value = None
        risk_manager._session_date = None

        with (
            patch.object(risk_manager, "_current_trading_session_date", return_value=session_date),
            patch.object(risk_manager.ac, "get_portfolio_value", side_effect=[100000, 99000]),
        ):
            self.assertFalse(risk_manager.daily_loss_exceeded())

        risk_manager._session_start_value = None
        risk_manager._session_date = None

        with (
            patch.object(risk_manager, "_current_trading_session_date", return_value=session_date),
            patch.object(risk_manager.ac, "get_portfolio_value", return_value=94000),
        ):
            self.assertTrue(risk_manager.daily_loss_exceeded())

    def test_kelly_position_size_respects_configured_position_cap(self):
        with (
            patch.object(risk_manager.ac, "get_portfolio_value", return_value=100000),
            patch.object(risk_manager.ac, "get_cash", return_value=100000),
        ):
            qty = risk_manager.kelly_position_size(
                win_rate=0.9,
                avg_win_pct=0.20,
                avg_loss_pct=0.02,
                price=100,
            )

        self.assertEqual(qty, 70)

    def test_cap_position_qty_clamps_requested_quantity(self):
        with (
            patch.object(risk_manager.ac, "get_portfolio_value", return_value=100000),
            patch.object(risk_manager.ac, "get_cash", return_value=100000),
        ):
            qty = risk_manager.cap_position_qty(price=100, qty=80)

        self.assertEqual(qty, 70)


class _FakePosition:
    """Minimal stand-in for an Alpaca position object."""

    def __init__(self, symbol, asset_class=None):
        self.symbol = symbol
        self.asset_class = asset_class


class CryptoPositionCapsTests(unittest.TestCase):
    """Tests for max_crypto_positions / min_crypto_positions (asset-class-aware caps)."""

    def setUp(self):
        # Snapshot and restore the module-level T dict so mutations don't leak.
        self.orig_T = dict(risk_manager.T)
        self.addCleanup(self._restore_trading_config)

    def _restore_trading_config(self):
        risk_manager.T.clear()
        risk_manager.T.update(self.orig_T)

    def _set_limits(self, max_positions=10, max_crypto=3, min_crypto=0):
        risk_manager.T["max_positions"] = max_positions
        risk_manager.T["max_crypto_positions"] = max_crypto
        risk_manager.T["min_crypto_positions"] = min_crypto

    def test_is_crypto_symbol_detects_slash_and_asset_class(self):
        self.assertTrue(risk_manager._is_crypto_symbol("BTC/USD"))
        self.assertTrue(risk_manager._is_crypto_symbol("DOGEUSD", "crypto"))
        self.assertFalse(risk_manager._is_crypto_symbol("AAPL"))
        self.assertFalse(risk_manager._is_crypto_symbol("AAPL", "us_equity"))

    def test_classify_position_handles_object_and_dict(self):
        self.assertEqual(
            risk_manager._classify_position(_FakePosition("BTC/USD")),
            "crypto",
        )
        self.assertEqual(
            risk_manager._classify_position(_FakePosition("AAPL", "us_equity")),
            "stock",
        )
        self.assertEqual(
            risk_manager._classify_position({"symbol": "ETH/USD"}),
            "crypto",
        )
        self.assertEqual(
            risk_manager._classify_position({"symbol": "MSFT", "asset_class": "us_equity"}),
            "stock",
        )

    def test_crypto_limits_invariant_enforced(self):
        self._set_limits(max_positions=10, max_crypto=15, min_crypto=20)
        max_c, min_c = risk_manager._get_crypto_limits()
        # max_crypto clamped to max_positions, min_crypto clamped to max_crypto
        self.assertEqual(max_c, 10)
        self.assertEqual(min_c, 10)

    def test_crypto_disabled_when_max_crypto_zero(self):
        self._set_limits(max_positions=10, max_crypto=0, min_crypto=0)
        with patch.object(risk_manager.ac, "get_all_positions", return_value=[]):
            self.assertTrue(
                risk_manager.max_positions_reached(asset_class="crypto", symbol="BTC/USD")
            )
            # Stocks still allowed
            self.assertFalse(
                risk_manager.max_positions_reached(asset_class="us_equity", symbol="AAPL")
            )

    def test_crypto_entry_blocked_at_max_crypto(self):
        self._set_limits(max_positions=10, max_crypto=3, min_crypto=0)
        positions = [_FakePosition(s) for s in ("BTC/USD", "ETH/USD", "SOL/USD")]
        with patch.object(risk_manager.ac, "get_all_positions", return_value=positions):
            self.assertTrue(
                risk_manager.max_positions_reached(asset_class="crypto", symbol="DOGE/USD")
            )

    def test_crypto_entry_allowed_below_max_crypto(self):
        self._set_limits(max_positions=10, max_crypto=3, min_crypto=0)
        positions = [_FakePosition("BTC/USD"), _FakePosition("AAPL", "us_equity")]
        with patch.object(risk_manager.ac, "get_all_positions", return_value=positions):
            self.assertFalse(
                risk_manager.max_positions_reached(asset_class="crypto", symbol="ETH/USD")
            )

    def test_stock_entry_blocked_when_crypto_reservation_saturated(self):
        # 10 global cap, 2 reserved for crypto → stocks cannot exceed 8 slots.
        self._set_limits(max_positions=10, max_crypto=3, min_crypto=2)
        positions = [_FakePosition(f"STK{i}", "us_equity") for i in range(8)]
        with patch.object(risk_manager.ac, "get_all_positions", return_value=positions):
            self.assertTrue(
                risk_manager.max_positions_reached(asset_class="us_equity", symbol="AAPL")
            )
            # Crypto still welcome — reserved slots are FOR it.
            self.assertFalse(
                risk_manager.max_positions_reached(asset_class="crypto", symbol="BTC/USD")
            )

    def test_reservation_does_not_force_crypto_entries(self):
        # min_crypto=2 should reserve slots but NOT require crypto to fill them.
        # If crypto declines to enter (e.g., no signal), stocks are merely capped at 8 — fine.
        self._set_limits(max_positions=10, max_crypto=3, min_crypto=2)
        positions = [_FakePosition(f"STK{i}", "us_equity") for i in range(5)]
        with patch.object(risk_manager.ac, "get_all_positions", return_value=positions):
            # Stocks still have 8-5=3 slots available
            self.assertFalse(
                risk_manager.max_positions_reached(asset_class="us_equity", symbol="AAPL")
            )

    def test_global_cap_still_enforced(self):
        self._set_limits(max_positions=3, max_crypto=3, min_crypto=0)
        positions = [_FakePosition("AAPL", "us_equity")] * 3
        with patch.object(risk_manager.ac, "get_all_positions", return_value=positions):
            self.assertTrue(
                risk_manager.max_positions_reached(asset_class="us_equity", symbol="MSFT")
            )
            self.assertTrue(
                risk_manager.max_positions_reached(asset_class="crypto", symbol="BTC/USD")
            )

    def test_legacy_call_with_no_args_returns_global_cap_only(self):
        self._set_limits(max_positions=10, max_crypto=0, min_crypto=0)
        # max_crypto=0 would block crypto, but legacy call has no symbol so only global cap applies
        with patch.object(risk_manager.ac, "get_all_positions", return_value=[]):
            self.assertFalse(risk_manager.max_positions_reached())

    def test_pre_trade_check_blocks_crypto_when_disabled(self):
        self._set_limits(max_positions=10, max_crypto=0, min_crypto=0)
        with (
            patch.object(risk_manager, "daily_loss_exceeded", return_value=False),
            patch.object(risk_manager.ac, "get_all_positions", return_value=[]),
        ):
            result = risk_manager.pre_trade_check(
                price=100.0, symbol="BTC/USD", asset_class="crypto"
            )
        self.assertFalse(result["approved"])
        self.assertIn("disabled", result["reason"].lower())


if __name__ == "__main__":
    unittest.main()
