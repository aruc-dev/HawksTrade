"""Enforce that the read-only Alpaca wrapper never imports mutating functions."""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from dashboard import alpaca_readonly


class AlpacaReadOnlyImportGuardTests(unittest.TestCase):
    def test_allowlist_and_denylist_do_not_overlap(self):
        overlap = alpaca_readonly.ALLOWED_FUNCTIONS & alpaca_readonly.FORBIDDEN_FUNCTIONS
        self.assertEqual(overlap, set(), f"Overlapping names: {overlap}")

    def test_forbidden_functions_are_not_importable_from_wrapper(self):
        for name in alpaca_readonly.FORBIDDEN_FUNCTIONS:
            self.assertFalse(
                hasattr(alpaca_readonly, name),
                f"dashboard.alpaca_readonly must not expose {name!r}",
            )

    def test_every_allowed_function_actually_exists(self):
        for name in alpaca_readonly.ALLOWED_FUNCTIONS:
            self.assertTrue(
                hasattr(alpaca_readonly, name),
                f"dashboard.alpaca_readonly.{name} should exist",
            )

    def test_get_positions_returns_empty_list_on_error(self):
        with patch.object(alpaca_readonly, "get_all_positions", side_effect=RuntimeError("boom")):
            self.assertEqual(alpaca_readonly.get_positions_as_dicts(), [])

    def test_position_to_dict_handles_object_and_dict(self):
        class FakePos:
            symbol = "BTC/USD"
            qty = "0.5"
            avg_entry_price = "30000"
            current_price = "31000"
            market_value = "15500"
            cost_basis = "15000"
            unrealized_pl = "500"
            unrealized_plpc = "0.0333"
            asset_class = "crypto"
            side = "long"

        obj_dict = alpaca_readonly._position_to_dict(FakePos())
        self.assertEqual(obj_dict["symbol"], "BTC/USD")
        self.assertEqual(obj_dict["unrealized_pl"], 500.0)

        plain_dict = alpaca_readonly._position_to_dict({
            "symbol": "AAPL",
            "qty": "10",
            "avg_entry_price": "150",
            "unrealized_pl": "-25",
            "asset_class": "us_equity",
        })
        self.assertEqual(plain_dict["symbol"], "AAPL")
        self.assertEqual(plain_dict["unrealized_pl"], -25.0)
        # Missing fields default to 0.0, not KeyError.
        self.assertEqual(plain_dict["market_value"], 0.0)

    def test_account_summary_returns_empty_dict_on_error(self):
        with patch.object(alpaca_readonly, "get_account", side_effect=RuntimeError("offline")):
            self.assertEqual(alpaca_readonly.get_account_summary(), {})

    def test_alpaca_reachable_returns_false_on_exception(self):
        with patch.object(alpaca_readonly, "get_account", side_effect=RuntimeError):
            self.assertFalse(alpaca_readonly.alpaca_reachable())

    def test_account_summary_reuses_supplied_account_object(self):
        account = {"portfolio_value": "100000", "cash": "50000", "buying_power": "200000"}

        with patch.object(alpaca_readonly, "get_account") as get_account:
            summary = alpaca_readonly.get_account_summary(account)

        get_account.assert_not_called()
        self.assertEqual(
            summary,
            {"portfolio_value": 100000.0, "cash": 50000.0, "buying_power": 200000.0},
        )

    def test_alpaca_reachable_reuses_supplied_account_object(self):
        with patch.object(alpaca_readonly, "get_account") as get_account:
            self.assertTrue(alpaca_readonly.alpaca_reachable({"portfolio_value": "1"}))

        get_account.assert_not_called()

    def test_get_trading_client_uses_dashboard_env_only(self):
        fake_client = MagicMock(name="TradingClient")
        with patch.dict(
            os.environ,
            {
                "ALPACA_PAPER_API_KEY": "paper-key",
                "ALPACA_PAPER_SECRET_KEY": "paper-secret",
            },
            clear=False,
        ), patch.object(alpaca_readonly, "_trading_client", None), \
                patch.object(alpaca_readonly, "cfg") as mock_cfg, \
                patch.object(alpaca_readonly, "TradingClient", return_value=fake_client) as mock_client:
            mock_cfg.return_value.mode = "paper"
            client = alpaca_readonly._get_trading_client()

        self.assertIs(client, fake_client)
        mock_client.assert_called_once_with("paper-key", "paper-secret", paper=True)

    def test_missing_dashboard_credentials_raise_clear_error(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(alpaca_readonly, "_trading_client", None), \
                patch.object(alpaca_readonly, "cfg") as mock_cfg:
            mock_cfg.return_value.mode = "paper"
            with self.assertRaises(RuntimeError):
                alpaca_readonly._get_trading_client()


if __name__ == "__main__":
    unittest.main()
