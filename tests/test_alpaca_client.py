import unittest
from datetime import timedelta
from unittest.mock import patch

from core import alpaca_client


class AlpacaClientTests(unittest.TestCase):
    def test_market_order_rejects_invalid_side_before_client_init(self):
        with patch.object(alpaca_client, "get_trading_client") as get_client:
            with self.assertRaises(ValueError):
                alpaca_client.place_market_order("AAPL", 1, "hold")

        get_client.assert_not_called()

    def test_limit_order_rejects_invalid_side_before_client_init(self):
        with patch.object(alpaca_client, "get_trading_client") as get_client:
            with self.assertRaises(ValueError):
                alpaca_client.place_limit_order("AAPL", 1, "hold", 100)

        get_client.assert_not_called()

    def test_lookback_delta_gives_stock_daily_buffer(self):
        self.assertGreaterEqual(
            alpaca_client._lookback_delta("1Day", 30, market="stock"),
            timedelta(days=90),
        )

    def test_lookback_delta_gives_crypto_daily_buffer(self):
        self.assertGreaterEqual(
            alpaca_client._lookback_delta("1Day", 30, market="crypto"),
            timedelta(days=60),
        )

    def test_normalize_symbol_removes_crypto_slash(self):
        self.assertEqual(alpaca_client.normalize_symbol("BTC/USD"), "BTCUSD")


if __name__ == "__main__":
    unittest.main()
