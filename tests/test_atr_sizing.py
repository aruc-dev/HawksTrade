import unittest
from unittest.mock import Mock

from strategies.atr_sizing import atr_stop_and_qty


class ATRSizingTests(unittest.TestCase):
    def test_returns_stop_and_qty_for_valid_inputs(self):
        sized = atr_stop_and_qty(
            symbol="AAPL",
            price=100,
            atr=2,
            atr_multiplier=2,
            portfolio_equity=10000,
            risk_per_trade_pct=0.01,
            min_trade_value=100,
            logger=Mock(),
            prefix="[Test]",
        )

        self.assertEqual(sized, (96, 25.0))

    def test_invalid_numeric_inputs_skip_without_raising(self):
        logger = Mock()

        sized = atr_stop_and_qty(
            symbol="AAPL",
            price=None,
            atr=2,
            atr_multiplier=2,
            portfolio_equity=10000,
            risk_per_trade_pct=0.01,
            min_trade_value=100,
            logger=logger,
            prefix="[Test]",
        )

        self.assertIsNone(sized)
        logger.info.assert_called_once()

    def test_nonpositive_risk_inputs_skip_without_qty(self):
        logger = Mock()

        sized = atr_stop_and_qty(
            symbol="AAPL",
            price=100,
            atr=2,
            atr_multiplier=2,
            portfolio_equity=10000,
            risk_per_trade_pct=0,
            min_trade_value=100,
            logger=logger,
            prefix="[Test]",
        )

        self.assertIsNone(sized)
        logger.info.assert_called_once()


if __name__ == "__main__":
    unittest.main()
