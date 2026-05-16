import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core import broker_stops


def _trade(symbol="AAPL", asset_class="stock", stop_loss="95", qty="2"):
    return {
        "symbol": symbol,
        "asset_class": asset_class,
        "side": "buy",
        "qty": qty,
        "stop_loss": stop_loss,
        "status": "open",
    }


def _position(symbol="AAPL", qty="2", asset_class="us_equity"):
    return SimpleNamespace(symbol=symbol, qty=qty, asset_class=asset_class)


class BrokerStopsTests(unittest.TestCase):
    def _live_cfg(self):
        return {
            "mode": "live",
            "broker_stops": {
                "enabled": True,
                "submit_in_paper": False,
                "crypto_stop_limit_offset_pct": 0.005,
            },
        }

    def test_existing_sell_order_is_not_duplicated(self):
        existing_order = SimpleNamespace(symbol="AAPL", side="sell", type="stop")

        with (
            patch.dict(broker_stops.CFG, self._live_cfg(), clear=False),
            patch.object(broker_stops.ac, "get_open_orders", return_value=[existing_order]),
            patch.object(broker_stops.ac, "place_stop_order") as place_stop_order,
        ):
            summary = broker_stops.sync_broker_stops(
                positions=[_position()],
                open_trades=[_trade()],
            )

        self.assertEqual(summary["existing"], 1)
        self.assertEqual(summary["placed"], 0)
        place_stop_order.assert_not_called()

    def test_open_limit_sell_does_not_count_as_protective_stop(self):
        existing_order = SimpleNamespace(symbol="AAPL", side="sell", type="limit")

        with (
            patch.dict(broker_stops.CFG, self._live_cfg(), clear=False),
            patch.object(broker_stops.ac, "get_open_orders", return_value=[existing_order]),
            patch.object(
                broker_stops.ac,
                "place_stop_order",
                return_value=SimpleNamespace(id="stop-1"),
            ) as place_stop_order,
        ):
            summary = broker_stops.sync_broker_stops(
                positions=[_position()],
                open_trades=[_trade()],
            )

        self.assertEqual(summary["existing"], 0)
        self.assertEqual(summary["placed"], 1)
        place_stop_order.assert_called_once()

    def test_open_order_lookup_failure_does_not_submit_blindly(self):
        with (
            patch.dict(broker_stops.CFG, self._live_cfg(), clear=False),
            patch.object(broker_stops.ac, "get_open_orders", side_effect=RuntimeError("timeout")),
            patch.object(broker_stops.ac, "place_stop_order") as place_stop_order,
        ):
            summary = broker_stops.sync_broker_stops(
                positions=[_position()],
                open_trades=[_trade()],
            )

        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["placed"], 0)
        place_stop_order.assert_not_called()

    def test_missing_stock_stop_is_placed(self):
        with (
            patch.dict(broker_stops.CFG, self._live_cfg(), clear=False),
            patch.object(broker_stops.ac, "get_open_orders", return_value=[]),
            patch.object(broker_stops.ac, "place_stop_order", return_value=SimpleNamespace(id="stop-1")) as place_stop_order,
        ):
            summary = broker_stops.sync_broker_stops(
                positions=[_position()],
                open_trades=[_trade()],
            )

        self.assertEqual(summary["placed"], 1)
        place_stop_order.assert_called_once_with(
            symbol="AAPL",
            qty=2.0,
            side="sell",
            stop_price=95.0,
            strategy="broker_stop",
            asset_class="stock",
        )

    def test_crypto_uses_stop_limit_with_limit_below_stop(self):
        with (
            patch.dict(broker_stops.CFG, self._live_cfg(), clear=False),
            patch.object(broker_stops.ac, "get_open_orders", return_value=[]),
            patch.object(
                broker_stops.ac,
                "place_stop_limit_order",
                return_value=SimpleNamespace(id="stop-limit-1"),
            ) as place_stop_limit_order,
        ):
            summary = broker_stops.sync_broker_stops(
                positions=[_position("DOGEUSD", qty="100", asset_class="crypto")],
                open_trades=[_trade("DOGE/USD", asset_class="crypto", stop_loss="0.10", qty="100")],
            )

        self.assertEqual(summary["placed"], 1)
        place_stop_limit_order.assert_called_once_with(
            symbol="DOGE/USD",
            qty=100.0,
            side="sell",
            stop_price=0.10,
            limit_price=0.0995,
            strategy="broker_stop",
            asset_class="crypto",
        )

    def test_paper_default_is_log_only(self):
        with (
            patch.dict(
                broker_stops.CFG,
                {
                    "mode": "paper",
                    "broker_stops": {"enabled": True, "submit_in_paper": False},
                },
                clear=False,
            ),
            patch.object(broker_stops.ac, "get_open_orders") as get_open_orders,
            patch.object(broker_stops.ac, "place_stop_order") as place_stop_order,
        ):
            summary = broker_stops.sync_broker_stops(
                positions=[_position()],
                open_trades=[_trade()],
            )

        self.assertEqual(summary["would_place"], 1)
        self.assertEqual(summary["placed"], 0)
        self.assertFalse(summary["submit_orders"])
        get_open_orders.assert_not_called()
        place_stop_order.assert_not_called()

    def test_dry_run_does_not_submit_even_in_live_mode(self):
        with (
            patch.dict(broker_stops.CFG, self._live_cfg(), clear=False),
            patch.object(broker_stops.ac, "get_open_orders") as get_open_orders,
            patch.object(broker_stops.ac, "place_stop_order") as place_stop_order,
        ):
            summary = broker_stops.sync_broker_stops(
                dry_run=True,
                positions=[_position()],
                open_trades=[_trade()],
            )

        self.assertEqual(summary["would_place"], 1)
        self.assertFalse(summary["submit_orders"])
        get_open_orders.assert_not_called()
        place_stop_order.assert_not_called()
