import tempfile
import unittest
from pathlib import Path

from tracking import order_intents


class OrderIntentTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.original_path = order_intents.ORDER_INTENTS
        order_intents.ORDER_INTENTS = Path(self.tmpdir.name) / "order_intents.csv"
        self.addCleanup(setattr, order_intents, "ORDER_INTENTS", self.original_path)

    def test_get_or_create_reuses_existing_intent_for_same_run_symbol_side_strategy(self):
        first, created_first = order_intents.get_or_create_order_intent(
            run_id="run-1",
            symbol="DOGE/USD",
            side="buy",
            strategy="ma_crossover",
            asset_class="crypto",
            qty="10",
            limit_price="0.10",
        )
        second, created_second = order_intents.get_or_create_order_intent(
            run_id="run-1",
            symbol="DOGEUSD",
            side="buy",
            strategy="ma_crossover",
            asset_class="crypto",
            qty="10",
            limit_price="0.10",
        )

        rows = order_intents.read_order_intents()

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first["client_order_id"], second["client_order_id"])
        self.assertLessEqual(len(first["client_order_id"]), 48)
        self.assertEqual(len(rows), 1)

    def test_update_order_intent_records_broker_status(self):
        intent, _ = order_intents.get_or_create_order_intent(
            run_id="run-1",
            symbol="AAPL",
            side="sell",
            strategy="momentum",
            asset_class="stock",
            qty="2",
        )

        updated = order_intents.update_order_intent(
            intent["client_order_id"],
            status="submitted",
            broker_order_id="broker-1",
        )
        rows = order_intents.read_order_intents()

        self.assertTrue(updated)
        self.assertEqual(rows[0]["status"], "submitted")
        self.assertEqual(rows[0]["broker_order_id"], "broker-1")


if __name__ == "__main__":
    unittest.main()
