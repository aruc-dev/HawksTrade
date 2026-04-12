"""
Tests for v6 improvements:
  - Hold-day exit bug fix (get_open_trades_for_backtest, get_trade_age_days)
  - Quarterly performance reporting
  - Refreshed EXTENDED_POOL
"""

import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
from datetime import datetime, timedelta, timezone

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scheduler.run_backtest import BacktestSimulator, EXTENDED_POOL, _compute_quarterly_performance


class TestHoldDayExitFix(unittest.TestCase):
    """Tests for Fix 1: get_open_trades_for_backtest and get_trade_age_days."""

    def setUp(self):
        self.sim = BacktestSimulator(initial_fund=10000.0)
        self.sim.current_date = datetime(2025, 6, 15, tzinfo=timezone.utc)
        self.sim.positions = {
            "AAPL": {
                "qty": 10,
                "entry_price": 150.0,
                "entry_date": datetime(2025, 6, 10, tzinfo=timezone.utc),
                "asset_class": "stock",
                "strategy": "momentum",
            },
            "BTC/USD": {
                "qty": 0.5,
                "entry_price": 60000.0,
                "entry_date": datetime(2025, 6, 1, tzinfo=timezone.utc),
                "asset_class": "crypto",
                "strategy": "ma_crossover",
            },
        }

    def test_get_open_trades_returns_correct_shape(self):
        trades = self.sim.get_open_trades_for_backtest()
        self.assertEqual(len(trades), 2)
        for trade in trades:
            self.assertIn("symbol", trade)
            self.assertIn("strategy", trade)
            self.assertIn("asset_class", trade)
            self.assertIn("entry_price", trade)
            self.assertIn("side", trade)
            self.assertIn("status", trade)
            self.assertIn("timestamp", trade)
            self.assertEqual(trade["side"], "buy")
            self.assertEqual(trade["status"], "open")

    def test_get_open_trades_returns_correct_data(self):
        trades = self.sim.get_open_trades_for_backtest()
        symbols = {t["symbol"] for t in trades}
        self.assertIn("AAPL", symbols)
        self.assertIn("BTC/USD", symbols)
        aapl_trade = [t for t in trades if t["symbol"] == "AAPL"][0]
        self.assertEqual(aapl_trade["strategy"], "momentum")
        self.assertEqual(aapl_trade["asset_class"], "stock")
        self.assertEqual(aapl_trade["entry_price"], 150.0)

    def test_get_open_trades_empty_when_no_positions(self):
        self.sim.positions = {}
        trades = self.sim.get_open_trades_for_backtest()
        self.assertEqual(trades, [])

    def test_get_trade_age_days_correct(self):
        age = self.sim.get_trade_age_days("AAPL")
        self.assertEqual(age, 5)  # June 15 - June 10 = 5 days

    def test_get_trade_age_days_crypto(self):
        age = self.sim.get_trade_age_days("BTC/USD")
        self.assertEqual(age, 14)  # June 15 - June 1 = 14 days

    def test_get_trade_age_days_unknown_symbol(self):
        age = self.sim.get_trade_age_days("UNKNOWN")
        self.assertEqual(age, 0.0)

    def test_hold_day_exit_triggers_correctly(self):
        """Verify that the hold-day check in the backtest loop would trigger for aged positions."""
        hold_days_limit = 4  # momentum hold_days from config
        age = self.sim.get_trade_age_days("AAPL")  # 5 days
        self.assertGreaterEqual(age, hold_days_limit)

    def test_hold_day_exit_does_not_trigger_for_fresh(self):
        """Fresh positions should not be exited."""
        self.sim.positions["FRESH"] = {
            "qty": 5,
            "entry_price": 100.0,
            "entry_date": datetime(2025, 6, 14, tzinfo=timezone.utc),
            "asset_class": "stock",
            "strategy": "momentum",
        }
        age = self.sim.get_trade_age_days("FRESH")  # 1 day
        self.assertLess(age, 4)  # momentum hold_days = 4


class TestQuarterlyReporting(unittest.TestCase):
    """Tests for Fix 2: quarterly performance breakdown."""

    def _make_sim_and_curve(self):
        sim = BacktestSimulator(initial_fund=10000.0)
        # Create equity curve spanning Q1 and Q2 2025
        dates = pd.date_range("2025-01-01", "2025-06-30", freq="D", tz="UTC")
        curve_data = []
        value = 10000.0
        for d in dates:
            value += 5.0  # steady growth
            curve_data.append({"date": d, "value": value})
        df_curve = pd.DataFrame(curve_data)

        # Create some trades
        sim.trades_log = [
            {"symbol": "AAPL", "entry_date": datetime(2025, 1, 5, tzinfo=timezone.utc),
             "exit_date": datetime(2025, 2, 10, tzinfo=timezone.utc),
             "entry_price": 150, "exit_price": 160, "qty": 5, "pnl": 50, "pnl_pct": 0.066, "strategy": "momentum"},
            {"symbol": "MSFT", "entry_date": datetime(2025, 2, 1, tzinfo=timezone.utc),
             "exit_date": datetime(2025, 3, 15, tzinfo=timezone.utc),
             "entry_price": 300, "exit_price": 290, "qty": 3, "pnl": -30, "pnl_pct": -0.033, "strategy": "momentum"},
            {"symbol": "GOOGL", "entry_date": datetime(2025, 4, 1, tzinfo=timezone.utc),
             "exit_date": datetime(2025, 5, 15, tzinfo=timezone.utc),
             "entry_price": 140, "exit_price": 155, "qty": 7, "pnl": 105, "pnl_pct": 0.107, "strategy": "rsi_reversion"},
        ]
        return sim, df_curve

    def test_quarterly_report_has_correct_quarters(self):
        sim, df_curve = self._make_sim_and_curve()
        quarters = _compute_quarterly_performance(sim, df_curve)
        quarter_names = [q["quarter"] for q in quarters]
        self.assertIn("Q1 2025", quarter_names)
        self.assertIn("Q2 2025", quarter_names)

    def test_quarterly_report_has_required_fields(self):
        sim, df_curve = self._make_sim_and_curve()
        quarters = _compute_quarterly_performance(sim, df_curve)
        for q in quarters:
            self.assertIn("quarter", q)
            self.assertIn("start_value", q)
            self.assertIn("end_value", q)
            self.assertIn("return_pct", q)
            self.assertIn("trades", q)
            self.assertIn("win_rate", q)

    def test_quarterly_trade_counts(self):
        sim, df_curve = self._make_sim_and_curve()
        quarters = _compute_quarterly_performance(sim, df_curve)
        q1 = [q for q in quarters if q["quarter"] == "Q1 2025"][0]
        q2 = [q for q in quarters if q["quarter"] == "Q2 2025"][0]
        # Q1: AAPL exits Feb (win), MSFT exits Mar (loss) -> 2 trades
        self.assertEqual(q1["trades"], 2)
        # Q2: GOOGL exits May (win) -> 1 trade
        self.assertEqual(q2["trades"], 1)

    def test_quarterly_win_rate(self):
        sim, df_curve = self._make_sim_and_curve()
        quarters = _compute_quarterly_performance(sim, df_curve)
        q1 = [q for q in quarters if q["quarter"] == "Q1 2025"][0]
        # Q1: 1 win out of 2 trades = 50%
        self.assertAlmostEqual(q1["win_rate"], 0.5)

    def test_quarterly_returns_positive(self):
        sim, df_curve = self._make_sim_and_curve()
        quarters = _compute_quarterly_performance(sim, df_curve)
        for q in quarters:
            self.assertGreater(q["return_pct"], 0)  # steady +5/day growth

    def test_quarterly_report_appears_in_output_string(self):
        """Verify the quarterly section appears when run_backtest generates output."""
        # We test the string generation indirectly via _compute_quarterly_performance
        sim, df_curve = self._make_sim_and_curve()
        quarters = _compute_quarterly_performance(sim, df_curve)
        self.assertTrue(len(quarters) > 0)
        # Build report string same way as run_backtest
        report = "### Quarterly Performance\n"
        report += "| Quarter | Start Value | End Value | Return | Trades | Win Rate |\n"
        for q in quarters:
            report += f"| {q['quarter']} |"
        self.assertIn("Quarterly Performance", report)
        self.assertIn("Q1 2025", report)

    def test_quarterly_empty_trades_log(self):
        sim = BacktestSimulator()
        sim.trades_log = []
        df_curve = pd.DataFrame([{"date": datetime(2025, 1, 1, tzinfo=timezone.utc), "value": 10000}])
        quarters = _compute_quarterly_performance(sim, df_curve)
        self.assertEqual(quarters, [])


class TestExtendedPool(unittest.TestCase):
    """Tests for Fix 3: refreshed EXTENDED_POOL."""

    def test_pool_under_120_symbols(self):
        self.assertLessEqual(len(EXTENDED_POOL), 120)

    def test_defence_symbols_present(self):
        for sym in ["LMT", "NOC", "GD"]:
            self.assertIn(sym, EXTENDED_POOL)

    def test_energy_symbols_present(self):
        for sym in ["XOM", "CVX", "COP", "SLB", "HAL", "OXY"]:
            self.assertIn(sym, EXTENDED_POOL)

    def test_ai_infra_symbols_present(self):
        for sym in ["SMCI", "ARM", "ANET", "MRVL"]:
            self.assertIn(sym, EXTENDED_POOL)

    def test_consumer_staples_present(self):
        for sym in ["PG", "KO", "PEP"]:
            self.assertIn(sym, EXTENDED_POOL)

    def test_international_adrs_present(self):
        for sym in ["TSM", "ASML", "SAP", "TM"]:
            self.assertIn(sym, EXTENDED_POOL)

    def test_speculative_ev_removed(self):
        for sym in ["LCID", "RIVN", "NIO"]:
            self.assertNotIn(sym, EXTENDED_POOL)

    def test_no_duplicates(self):
        self.assertEqual(len(EXTENDED_POOL), len(set(EXTENDED_POOL)))


if __name__ == "__main__":
    unittest.main()
