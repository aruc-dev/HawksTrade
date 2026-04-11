import unittest
from datetime import date
from unittest.mock import patch

from core import risk_manager


class RiskManagerTests(unittest.TestCase):
    def setUp(self):
        self.original_start = risk_manager._session_start_value
        self.original_date = risk_manager._session_date
        self.addCleanup(setattr, risk_manager, "_session_start_value", self.original_start)
        self.addCleanup(setattr, risk_manager, "_session_date", self.original_date)

    def test_daily_loss_check_handles_zero_start_value(self):
        risk_manager._session_start_value = 0
        risk_manager._session_date = date.today()

        with patch.object(risk_manager.ac, "get_portfolio_value", return_value=0):
            self.assertFalse(risk_manager.daily_loss_exceeded())

    def test_position_size_rejects_non_positive_price(self):
        self.assertEqual(risk_manager.calculate_position_size(0), 0.0)
        self.assertEqual(risk_manager.calculate_position_size(-5), 0.0)


if __name__ == "__main__":
    unittest.main()
