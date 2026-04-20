"""Enforce that the read-only Alpaca wrapper never imports mutating functions."""
from __future__ import annotations

import unittest
from unittest.mock import patch

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
        with patch.object(alpaca_readonly, "get_portfolio_value", side_effect=RuntimeError("offline")):
            self.assertEqual(alpaca_readonly.get_account_summary(), {})

    def test_alpaca_reachable_returns_false_on_exception(self):
        with patch.object(alpaca_readonly, "get_account", side_effect=RuntimeError):
            self.assertFalse(alpaca_readonly.alpaca_reachable())


if __name__ == "__main__":
    unittest.main()
