import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from core.order_governor import OrderGovernor, OrderIntent


class OrderGovernorTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 5, 15, 14, 30, tzinfo=timezone.utc)
        self.account = SimpleNamespace(portfolio_value="10000", cash="8000", buying_power="8000")

    def _governor(self, **kwargs):
        defaults = {
            "order_history_provider": lambda: [],
            "now_provider": lambda: self.now,
        }
        defaults.update(kwargs)
        return OrderGovernor(**defaults)

    def test_allows_clean_market_entry(self):
        intent = OrderIntent("AAPL", "buy", 2, "market", price=100)

        decision = self._governor().evaluate(intent, self.account, [])

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.status, "allow")

    def test_blocks_invalid_quantity(self):
        intent = OrderIntent("AAPL", "buy", 0, "market", price=100)

        decision = self._governor().evaluate(intent, self.account, [])

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "invalid_qty")

    def test_blocks_missing_account_state(self):
        intent = OrderIntent("AAPL", "buy", 1, "market", price=100)

        decision = self._governor().evaluate(intent, None, [])

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "missing_account_state")

    def test_blocks_empty_account_state(self):
        intent = OrderIntent("AAPL", "buy", 1, "market", price=100)

        decision = self._governor().evaluate(intent, {}, [])

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "missing_account_state")

    def test_blocks_missing_broker_orders(self):
        intent = OrderIntent("AAPL", "buy", 1, "market", price=100)

        decision = self._governor().evaluate(intent, self.account, None)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "missing_broker_orders")

    def test_blocks_duplicate_pending_entry(self):
        intent = OrderIntent("AAPL", "buy", 1, "limit", price=100, limit_price=100.1)
        pending = SimpleNamespace(symbol="AAPL", side="buy", status="new", id="pending-buy")

        decision = self._governor().evaluate(intent, self.account, [pending])

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "duplicate_pending_entry")
        self.assertEqual(decision.context["order_id"], "pending-buy")

    def test_unknown_broker_order_status_counts_as_active(self):
        intent = OrderIntent("AAPL", "buy", 1, "limit", price=100, limit_price=100.1)
        pending = SimpleNamespace(symbol="AAPL", side="buy", status="broker_open_status", id="pending-buy")

        decision = self._governor().evaluate(intent, self.account, [pending])

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "duplicate_pending_entry")

    def test_terminal_broker_order_status_does_not_count_as_active(self):
        intent = OrderIntent("AAPL", "buy", 1, "limit", price=100, limit_price=100.1)
        filled = SimpleNamespace(symbol="AAPL", side="buy", status="filled", id="filled-buy")

        decision = self._governor().evaluate(intent, self.account, [filled])

        self.assertTrue(decision.allowed)

    def test_blocks_max_active_orders_for_entries(self):
        orders = [
            SimpleNamespace(symbol=f"SYM{i}", side="buy", status="new", id=f"order-{i}")
            for i in range(2)
        ]
        intent = OrderIntent("AAPL", "buy", 1, "market", price=100)

        decision = self._governor(max_active_orders=2).evaluate(intent, self.account, orders)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "max_active_orders")

    def test_blocks_order_rate_window_for_entries(self):
        history = [
            {"timestamp": (self.now - timedelta(seconds=10)).isoformat()},
            {"timestamp": (self.now - timedelta(seconds=20)).isoformat()},
        ]
        intent = OrderIntent("AAPL", "buy", 1, "market", price=100)

        decision = self._governor(
            max_orders_per_window=2,
            order_rate_window_seconds=60,
            order_history_provider=lambda: history,
        ).evaluate(intent, self.account, [])

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "order_rate_limit")

    def test_multi_leg_history_counts_as_one_logical_order(self):
        history = [
            {
                "timestamp": (self.now - timedelta(seconds=10)).isoformat(),
                "parent_intent_id": "parent-1",
                "leg_number": "1",
            },
            {
                "timestamp": (self.now - timedelta(seconds=20)).isoformat(),
                "parent_intent_id": "parent-1",
                "leg_number": "2",
            },
        ]
        intent = OrderIntent("AAPL", "buy", 1, "market", price=100)

        decision = self._governor(
            max_orders_per_window=2,
            order_rate_window_seconds=60,
            order_history_provider=lambda: history,
        ).evaluate(intent, self.account, [])

        self.assertTrue(decision.allowed)

    def test_blocks_daily_order_count_for_entries(self):
        history = [
            {"timestamp": self.now.isoformat()},
            {"timestamp": (self.now - timedelta(hours=1)).isoformat()},
        ]
        intent = OrderIntent("AAPL", "buy", 1, "market", price=100)

        decision = self._governor(
            max_orders_per_window=None,
            max_daily_orders=2,
            order_history_provider=lambda: history,
        ).evaluate(intent, self.account, [])

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "daily_order_limit")

    def test_blocks_max_notional_for_entries(self):
        intent = OrderIntent("AAPL", "buy", 20, "limit", price=100, limit_price=101)

        decision = self._governor(max_notional_usd=1000).evaluate(intent, self.account, [])

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "max_notional")

    def test_entry_only_limits_do_not_block_exit(self):
        orders = [
            SimpleNamespace(symbol=f"SYM{i}", side="buy", status="new", id=f"order-{i}")
            for i in range(20)
        ]
        history = [{"timestamp": self.now.isoformat()} for _ in range(20)]
        intent = OrderIntent("AAPL", "sell", 5, "market", price=100, position_qty=5)

        decision = self._governor(
            max_active_orders=1,
            max_orders_per_window=1,
            max_daily_orders=1,
            max_notional_usd=1,
            order_history_provider=lambda: history,
        ).evaluate(intent, self.account, orders)

        self.assertTrue(decision.allowed)

    def test_blocks_duplicate_pending_exit(self):
        intent = OrderIntent("AAPL", "sell", 2, "market", price=100, position_qty=2)
        pending = {"symbol": "AAPL", "side": "sell", "status": "accepted", "id": "pending-sell"}

        decision = self._governor().evaluate(intent, self.account, [pending])

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "duplicate_pending_exit")

    def test_blocks_oversell_quantity(self):
        intent = OrderIntent("AAPL", "sell", 3, "market", price=100, position_qty=2)

        decision = self._governor().evaluate(intent, self.account, [])

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "oversell_qty")


if __name__ == "__main__":
    unittest.main()
