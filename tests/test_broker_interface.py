import unittest
from types import SimpleNamespace

from core import alpaca_client
from core.broker_interface import (
    AccountBroker,
    BrokerInterface,
    MarketDataBroker,
    OrderBroker,
    PositionBroker,
)


class FakeBroker:
    def get_account(self):
        return SimpleNamespace()

    def get_portfolio_value(self):
        return 10000.0

    def get_cash(self):
        return 5000.0

    def get_buying_power(self):
        return 5000.0

    def get_all_positions(self):
        return []

    def get_position(self, symbol):
        return None

    def get_open_orders(self):
        return []

    def get_closed_orders(self, limit=200):
        return []

    def place_market_order(
        self,
        symbol,
        qty,
        side,
        time_in_force="day",
        strategy="unknown",
        asset_class=None,
        client_order_id=None,
    ):
        return SimpleNamespace(id="market-order")

    def place_limit_order(
        self,
        symbol,
        qty,
        side,
        limit_price,
        time_in_force="gtc",
        strategy="unknown",
        asset_class=None,
        client_order_id=None,
    ):
        return SimpleNamespace(id="limit-order")

    def place_stop_order(
        self,
        symbol,
        qty,
        side,
        stop_price,
        time_in_force="gtc",
        strategy="unknown",
        asset_class=None,
        client_order_id=None,
    ):
        return SimpleNamespace(id="stop-order")

    def place_stop_limit_order(
        self,
        symbol,
        qty,
        side,
        stop_price,
        limit_price,
        time_in_force="gtc",
        strategy="unknown",
        asset_class=None,
        client_order_id=None,
    ):
        return SimpleNamespace(id="stop-limit-order")

    def get_stock_bars(self, symbols, timeframe="1Day", limit=60, start=None, end=None):
        return {}

    def get_crypto_bars(self, symbols, timeframe="1Day", limit=60):
        return {}

    def get_stock_latest_price(self, symbol):
        return 0.0

    def get_crypto_latest_price(self, symbol):
        return 0.0

    def is_market_open(self):
        return True


class BrokerInterfaceTests(unittest.TestCase):
    def test_fake_broker_satisfies_full_contract(self):
        broker = FakeBroker()

        self.assertIsInstance(broker, AccountBroker)
        self.assertIsInstance(broker, PositionBroker)
        self.assertIsInstance(broker, OrderBroker)
        self.assertIsInstance(broker, MarketDataBroker)
        self.assertIsInstance(broker, BrokerInterface)

    def test_alpaca_client_module_satisfies_full_contract(self):
        self.assertIsInstance(alpaca_client, BrokerInterface)

    def test_partial_broker_does_not_satisfy_full_contract(self):
        class PartialBroker:
            def get_account(self):
                return SimpleNamespace()

        self.assertNotIsInstance(PartialBroker(), BrokerInterface)


if __name__ == "__main__":
    unittest.main()
