import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from core import risk_manager


class RiskManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.original_start = risk_manager._session_start_value
        self.original_date = risk_manager._session_date
        self.original_baseline_file = risk_manager.DAILY_BASELINE_FILE
        self.addCleanup(setattr, risk_manager, "_session_start_value", self.original_start)
        self.addCleanup(setattr, risk_manager, "_session_date", self.original_date)
        self.addCleanup(setattr, risk_manager, "DAILY_BASELINE_FILE", self.original_baseline_file)
        risk_manager.DAILY_BASELINE_FILE = Path(self.tmpdir.name) / "daily_loss_baseline.json"

    def test_daily_loss_check_handles_zero_start_value(self):
        risk_manager._session_start_value = 0
        risk_manager._session_date = date.today()

        with patch.object(risk_manager.ac, "get_portfolio_value", return_value=0):
            self.assertFalse(risk_manager.daily_loss_exceeded())

    def test_position_size_rejects_non_positive_price(self):
        self.assertEqual(risk_manager.calculate_position_size(0), 0.0)
        self.assertEqual(risk_manager.calculate_position_size(-5), 0.0)

    def test_daily_loss_baseline_persists_across_process_state_reset(self):
        risk_manager._session_start_value = None
        risk_manager._session_date = None

        with patch.object(risk_manager.ac, "get_portfolio_value", side_effect=[100000, 96000]):
            self.assertFalse(risk_manager.daily_loss_exceeded())

        risk_manager._session_start_value = None
        risk_manager._session_date = None

        with patch.object(risk_manager.ac, "get_portfolio_value", return_value=94000):
            self.assertTrue(risk_manager.daily_loss_exceeded())

    def test_kelly_position_size_respects_configured_position_cap(self):
        with (
            patch.object(risk_manager.ac, "get_portfolio_value", return_value=100000),
            patch.object(risk_manager.ac, "get_cash", return_value=100000),
        ):
            qty = risk_manager.kelly_position_size(
                win_rate=0.9,
                avg_win_pct=0.20,
                avg_loss_pct=0.02,
                price=100,
            )

        self.assertEqual(qty, 50)

    def test_cap_position_qty_clamps_requested_quantity(self):
        with (
            patch.object(risk_manager.ac, "get_portfolio_value", return_value=100000),
            patch.object(risk_manager.ac, "get_cash", return_value=100000),
        ):
            qty = risk_manager.cap_position_qty(price=100, qty=80)

        self.assertEqual(qty, 50)


if __name__ == "__main__":
    unittest.main()
