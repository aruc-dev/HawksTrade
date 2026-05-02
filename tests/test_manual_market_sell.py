import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts import manual_market_sell


def _input_responses(*responses):
    pending = list(responses)

    def _input(_prompt):
        if not pending:
            raise AssertionError("input requested after test responses were exhausted")
        return pending.pop(0)

    return _input


class ManualMarketSellTests(unittest.TestCase):
    def test_selected_position_is_sold_at_market_and_reconciled(self):
        positions = [
            SimpleNamespace(
                symbol="AAPL",
                qty="2",
                asset_class="us_equity",
                avg_entry_price="100",
                current_price="110",
                market_value="220",
                unrealized_pl="20",
                unrealized_plpc="0.1",
            )
        ]
        output = io.StringIO()

        with (
            patch.object(manual_market_sell.ac, "get_all_positions", side_effect=[positions, []]),
            patch.object(
                manual_market_sell.oe,
                "exit_position",
                return_value={
                    "symbol": "AAPL",
                    "status": "closed",
                    "order_id": "exit-1",
                    "qty": 2,
                    "pnl_pct": 0.1,
                },
            ) as exit_position,
            patch.object(manual_market_sell, "safe_reconcile") as safe_reconcile,
        ):
            status = manual_market_sell.run_interactive(
                input_fn=_input_responses("1", "x"),
                output_fn=lambda line: print(line, file=output),
            )

        self.assertEqual(status, 0)
        exit_position.assert_called_once_with(
            "AAPL",
            manual_market_sell.MANUAL_SELL_REASON,
            asset_class="stock",
            dry_run=False,
            force_market=True,
        )
        safe_reconcile.assert_called_once_with(
            context="manual_market_sell.post_exit",
            logger=manual_market_sell.log,
        )
        rendered = output.getvalue()
        self.assertIn("1) AAPL", rendered)
        self.assertIn("X) Exit", rendered)
        self.assertIn("Market sell filled for AAPL", rendered)

    def test_crypto_position_uses_crypto_asset_class(self):
        positions = [
            SimpleNamespace(
                symbol="DOGE/USD",
                qty="100",
                asset_class="crypto",
                avg_entry_price="0.09",
                current_price="0.10",
            )
        ]

        with (
            patch.object(manual_market_sell.ac, "get_all_positions", side_effect=[positions, []]),
            patch.object(
                manual_market_sell.oe,
                "exit_position",
                return_value={"symbol": "DOGE/USD", "status": "submitted", "order_id": "exit-2", "qty": 100},
            ) as exit_position,
            patch.object(manual_market_sell, "safe_reconcile"),
        ):
            status = manual_market_sell.run_interactive(
                input_fn=_input_responses("1", "x"),
                output_fn=lambda _line: None,
            )

        self.assertEqual(status, 0)
        self.assertEqual(exit_position.call_args.kwargs["asset_class"], "crypto")

    def test_dry_run_passes_dry_run_and_skips_reconciliation(self):
        positions = [SimpleNamespace(symbol="MSFT", qty="3", asset_class="us_equity")]

        with (
            patch.object(manual_market_sell.ac, "get_all_positions", side_effect=[positions, []]),
            patch.object(
                manual_market_sell.oe,
                "exit_position",
                return_value={"symbol": "MSFT", "status": "dry_run", "qty": 3},
            ) as exit_position,
            patch.object(manual_market_sell, "safe_reconcile") as safe_reconcile,
        ):
            status = manual_market_sell.run_interactive(
                dry_run=True,
                input_fn=_input_responses("1", "x"),
                output_fn=lambda _line: None,
            )

        self.assertEqual(status, 0)
        self.assertTrue(exit_position.call_args.kwargs["dry_run"])
        safe_reconcile.assert_not_called()

    def test_invalid_selection_reprompts_without_selling(self):
        positions = [SimpleNamespace(symbol="AAPL", qty="2", asset_class="us_equity")]
        output = io.StringIO()

        with (
            patch.object(manual_market_sell.ac, "get_all_positions", return_value=positions),
            patch.object(manual_market_sell.oe, "exit_position") as exit_position,
            patch.object(manual_market_sell, "safe_reconcile") as safe_reconcile,
        ):
            status = manual_market_sell.run_interactive(
                input_fn=_input_responses("9", "x"),
                output_fn=lambda line: print(line, file=output),
            )

        self.assertEqual(status, 0)
        exit_position.assert_not_called()
        safe_reconcile.assert_not_called()
        self.assertIn("Invalid selection", output.getvalue())

    def test_no_positions_can_exit_cleanly(self):
        output = io.StringIO()

        with (
            patch.object(manual_market_sell.ac, "get_all_positions", return_value=[]),
            patch.object(manual_market_sell.oe, "exit_position") as exit_position,
        ):
            status = manual_market_sell.run_interactive(
                input_fn=_input_responses("x"),
                output_fn=lambda line: print(line, file=output),
            )

        self.assertEqual(status, 0)
        exit_position.assert_not_called()
        self.assertIn("No open positions.", output.getvalue())


if __name__ == "__main__":
    unittest.main()
