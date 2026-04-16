import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from alpaca.trading.enums import TimeInForce

from core import alpaca_client


class AlpacaClientTests(unittest.TestCase):
    def setUp(self):
        alpaca_client._crypto_price_increment_cache.clear()

    def _capture_limit_order(
        self,
        symbol,
        limit_price,
        asset_class=None,
        price_increment=None,
        qty=1,
        time_in_force="gtc",
    ):
        class FakeClient:
            def get_asset(self, symbol):
                if price_increment is None:
                    raise AttributeError("no asset metadata")
                return SimpleNamespace(price_increment=price_increment)

            def submit_order(self, req):
                self.req = req
                return SimpleNamespace(id="order-1")

        fake_client = FakeClient()
        with patch.object(alpaca_client, "get_trading_client", return_value=fake_client):
            alpaca_client.place_limit_order(
                symbol,
                qty,
                "buy",
                limit_price,
                time_in_force=time_in_force,
                asset_class=asset_class,
            )
        return fake_client.req

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

    def test_limit_order_rounds_stock_over_one_dollar_to_cents(self):
        req = self._capture_limit_order("TQQQ", 51.9369, asset_class="stock")

        self.assertEqual(req.limit_price, 51.94)

    def test_limit_order_allows_sub_dollar_stock_four_decimals(self):
        req = self._capture_limit_order("PENNY", 0.12345, asset_class="stock")

        self.assertEqual(req.limit_price, 0.1235)

    def test_limit_order_preserves_crypto_precision(self):
        req = self._capture_limit_order("DOGE/USD", 0.0946102345, asset_class="crypto")

        self.assertEqual(req.limit_price, 0.094610235)

    def test_limit_order_uses_crypto_asset_price_increment_when_available(self):
        req = self._capture_limit_order(
            "DOGE/USD",
            0.0946102345,
            asset_class="crypto",
            price_increment="0.01",
        )

        self.assertEqual(req.limit_price, 0.09)

    def test_fractional_stock_limit_order_uses_day_time_in_force(self):
        req = self._capture_limit_order("TQQQ", 51.9369, asset_class="stock", qty=94.396685)

        self.assertEqual(req.time_in_force, TimeInForce.DAY)

    def test_whole_share_stock_limit_order_keeps_requested_time_in_force(self):
        req = self._capture_limit_order("TQQQ", 51.9369, asset_class="stock", qty=94)

        self.assertEqual(req.time_in_force, TimeInForce.GTC)

    def test_fractional_crypto_limit_order_keeps_requested_time_in_force(self):
        req = self._capture_limit_order("DOGE/USD", 0.0946102345, asset_class="crypto", qty=10.5)

        self.assertEqual(req.time_in_force, TimeInForce.GTC)

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

    def test_to_crypto_pair_symbol_adds_slash_to_broker_symbol(self):
        self.assertEqual(alpaca_client.to_crypto_pair_symbol("DOGEUSD"), "DOGE/USD")

    def test_stock_latest_price_uses_latest_trade_when_quote_side_missing(self):
        class FakeDataClient:
            def get_stock_latest_quote(self, req):
                return {
                    "AMZN": SimpleNamespace(
                        bid_price=234.73,
                        ask_price=0.0,
                    )
                }

            def get_stock_latest_trade(self, req):
                return {"AMZN": SimpleNamespace(price=249.07)}

        with patch.object(alpaca_client, "get_stock_data_client", return_value=FakeDataClient()):
            price = alpaca_client.get_stock_latest_price("AMZN")

        self.assertEqual(price, 249.07)

    def test_stock_latest_price_averages_valid_bid_ask(self):
        class FakeDataClient:
            def get_stock_latest_quote(self, req):
                return {
                    "AMZN": SimpleNamespace(
                        bid_price=248.0,
                        ask_price=250.0,
                    )
                }

            def get_stock_latest_trade(self, req):
                raise AssertionError("latest trade should not be fetched when quote is valid")

        with patch.object(alpaca_client, "get_stock_data_client", return_value=FakeDataClient()):
            price = alpaca_client.get_stock_latest_price("AMZN")

        self.assertEqual(price, 249.0)



class SecretsSourceShmTests(unittest.TestCase):
    """Tests for secrets_source: shm path in alpaca_client module load."""

    def test_shm_source_loads_keys_from_temp_file(self):
        """secrets_source=shm calls load_dotenv with /dev/shm/.hawkstrade.env at module load."""
        import importlib
        from pathlib import Path
        from unittest.mock import MagicMock

        fake_cfg = {"mode": "paper", "secrets_source": "shm"}
        mock_load_dotenv = MagicMock()
        _real_exists = Path.exists

        def _exists_shm_true(self):
            if str(self) == "/dev/shm/.hawkstrade.env":
                return True
            return _real_exists(self)

        try:
            with patch("yaml.safe_load", return_value=fake_cfg), \
                 patch("dotenv.load_dotenv", mock_load_dotenv), \
                 patch.object(Path, "exists", _exists_shm_true):
                importlib.reload(alpaca_client)

            mock_load_dotenv.assert_called_once_with(Path("/dev/shm/.hawkstrade.env"))
        finally:
            importlib.reload(alpaca_client)

    def test_shm_source_missing_file_raises_environment_error(self):
        """secrets_source=shm with missing /dev/shm file raises EnvironmentError."""
        from pathlib import Path
        missing = Path("/dev/shm/.hawkstrade_nonexistent_test.env")

        # Simulate the guard that runs at import time
        with self.assertRaises(EnvironmentError) as ctx:
            if not missing.exists():
                raise EnvironmentError(
                    "secrets_source is \'shm\' but /dev/shm/.hawkstrade.env does not exist. "
                    "Run scripts/fetch_secrets.sh first (or ensure the systemd boot unit ran)."
                )
        self.assertIn("fetch_secrets.sh", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
