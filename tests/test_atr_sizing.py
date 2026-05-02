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

    def test_max_stop_loss_pct_caps_wide_atr_stop(self):
        sized = atr_stop_and_qty(
            symbol="AAPL",
            price=100,
            atr=10,
            atr_multiplier=2,
            portfolio_equity=10000,
            risk_per_trade_pct=0.01,
            min_trade_value=100,
            logger=Mock(),
            prefix="[Test]",
            max_stop_loss_pct=0.06,
        )

        self.assertEqual(sized, (94, 16.666667))

    def test_invalid_max_stop_loss_pct_skips_signal(self):
        logger = Mock()

        sized = atr_stop_and_qty(
            symbol="AAPL",
            price=100,
            atr=10,
            atr_multiplier=2,
            portfolio_equity=10000,
            risk_per_trade_pct=0.01,
            min_trade_value=100,
            logger=logger,
            prefix="[Test]",
            max_stop_loss_pct=1.2,
        )

        self.assertIsNone(sized)
        logger.info.assert_called_once()
        message = logger.info.call_args.args[0]
        self.assertIn("1.2", message)
        self.assertIn("expected 0 < pct < 1", message)


if __name__ == "__main__":
    unittest.main()
