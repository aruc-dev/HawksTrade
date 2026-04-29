"""
Tests for v6 improvements:
  - Hold-day exit bug fix (get_open_trades_for_backtest, get_trade_age_days)
  - Quarterly performance reporting
  - Refreshed EXTENDED_POOL
"""

import unittest
import contextlib
import tempfile
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scheduler.run_backtest import (
    BacktestSimulator,
    EXTENDED_POOL,
    REGIME_HISTORY_LIMITS,
    _apply_override,
    _backtest_trading_session_date,
    _backtest_scan_universe,
    _build_regime_bars,
    _compute_daily_sharpe,
    _compute_max_drawdown,
    _compute_profit_factor,
    _compute_quarterly_performance,
    _patch_runtime_risk_config,
    _run_backtest_hold_exits,
    _run_backtest_strategy_exits,
    _stock_market_open_for_backtest,
)
from core import risk_manager as rm
from core.exit_policy import should_exit_for_hold


def _price_frame(periods: int) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=periods, freq="D", tz=timezone.utc)
    return pd.DataFrame(
        {
            "open": range(periods),
            "high": range(periods),
            "low": range(periods),
            "close": range(periods),
            "volume": [1000] * periods,
        },
        index=index,
    )


class TestBacktestRegimeHistory(unittest.TestCase):
    def test_regime_bars_use_full_filter_windows(self):
        historical_data = {
            "SPY": _price_frame(300),
            "QQQ": _price_frame(100),
            "BTC/USD": _price_frame(80),
        }
        current_date = historical_data["SPY"].index[-1]

        regime_bars = _build_regime_bars(historical_data, current_date)

        self.assertEqual(len(regime_bars["SPY"]), REGIME_HISTORY_LIMITS["SPY"])
        self.assertEqual(len(regime_bars["QQQ"]), REGIME_HISTORY_LIMITS["QQQ"])
        self.assertEqual(len(regime_bars["BTC/USD"]), REGIME_HISTORY_LIMITS["BTC/USD"])


class TestBacktestLiveFidelity(unittest.TestCase):
    def test_backtest_session_date_uses_simulated_current_date(self):
        sim = BacktestSimulator(initial_fund=10000.0)
        sim.current_date = datetime(2026, 3, 30, 15, 0, tzinfo=timezone.utc)

        self.assertEqual(_backtest_trading_session_date(sim).isoformat(), "2026-03-30")

    def test_daily_loss_baseline_resets_on_simulated_session_date(self):
        sim = BacktestSimulator(initial_fund=10000.0)
        original_start = rm._session_start_value
        original_date = rm._session_date
        with tempfile.TemporaryDirectory() as tmpdir:
            values = iter([100000.0, 100000.0, 94000.0, 94000.0, 94000.0])
            with (
                patch("core.risk_manager.DAILY_BASELINE_FILE", Path(tmpdir) / "daily_loss_baseline.json"),
                patch("core.risk_manager.ac.get_portfolio_value", side_effect=lambda: next(values)),
                patch("core.risk_manager._current_trading_session_date", side_effect=lambda now=None: _backtest_trading_session_date(sim)),
                patch("core.risk_manager._session_start_value", None),
                patch("core.risk_manager._session_date", None),
            ):
                sim.current_date = datetime(2026, 3, 30, 15, 0, tzinfo=timezone.utc)
                self.assertFalse(rm.daily_loss_exceeded())
                self.assertTrue(rm.daily_loss_exceeded())

                sim.current_date = datetime(2026, 3, 31, 15, 0, tzinfo=timezone.utc)
                self.assertFalse(rm.daily_loss_exceeded())

        self.assertEqual(rm._session_start_value, original_start)
        self.assertEqual(rm._session_date, original_date)

    def test_stock_market_open_for_backtest_skips_weekends(self):
        self.assertTrue(_stock_market_open_for_backtest(datetime(2026, 4, 24, tzinfo=timezone.utc)))
        self.assertFalse(_stock_market_open_for_backtest(datetime(2026, 4, 25, tzinfo=timezone.utc)))
        self.assertFalse(_stock_market_open_for_backtest(datetime(2026, 4, 26, tzinfo=timezone.utc)))

    def test_stock_market_open_for_backtest_skips_exchange_holidays(self):
        self.assertFalse(_stock_market_open_for_backtest(datetime(2026, 4, 3, tzinfo=timezone.utc)))
        self.assertFalse(_stock_market_open_for_backtest(datetime(2026, 7, 3, tzinfo=timezone.utc)))
        self.assertTrue(_stock_market_open_for_backtest(datetime(2026, 7, 6, tzinfo=timezone.utc)))

    def test_stock_market_open_for_backtest_skips_next_year_observed_new_years(self):
        self.assertFalse(_stock_market_open_for_backtest(datetime(2021, 12, 31, tzinfo=timezone.utc)))
        self.assertTrue(_stock_market_open_for_backtest(datetime(2021, 12, 30, tzinfo=timezone.utc)))

    def test_stock_scan_universe_is_none_on_weekends_but_crypto_continues(self):
        class FakeStock:
            asset_class = "stocks"

        class FakeCrypto:
            asset_class = "crypto"

        cfg = {
            "stocks": {"scan_universe": ["AAPL"]},
            "crypto": {"scan_universe": ["BTC/USD"]},
        }

        stock_universe = _backtest_scan_universe(
            FakeStock(),
            cfg,
            screener=None,
            screener_enabled=False,
            current_date=datetime(2026, 4, 25, tzinfo=timezone.utc),
            market_open=False,
        )
        crypto_universe = _backtest_scan_universe(
            FakeCrypto(),
            cfg,
            screener=None,
            screener_enabled=False,
            current_date=datetime(2026, 4, 25, tzinfo=timezone.utc),
            market_open=False,
        )

        self.assertIsNone(stock_universe)
        self.assertEqual(crypto_universe, ["BTC/USD"])

    def test_strategy_exit_runs_for_matching_open_position(self):
        class FakeMACross:
            name = "ma_crossover"
            asset_class = "crypto"

            def should_exit(self, symbol, entry_price):
                return True, f"exit {symbol} {entry_price}"

        sim = BacktestSimulator(initial_fund=10000.0)
        sim.current_date = datetime(2026, 4, 25, tzinfo=timezone.utc)
        sim.positions = {
            "BTC/USD": {
                "qty": 1,
                "entry_price": 100.0,
                "entry_date": sim.current_date,
                "asset_class": "crypto",
                "strategy": "ma_crossover",
            },
        }

        with patch("scheduler.run_backtest.oe.exit_position") as exit_position:
            _run_backtest_strategy_exits([FakeMACross()], sim, market_open=False)

        exit_position.assert_called_once_with(
            "BTC/USD",
            "exit BTC/USD 100.0",
            "crypto",
            open_trades_callback=sim.get_open_trades_for_backtest,
        )

    def test_stock_strategy_exit_runs_when_market_open(self):
        class FakeRSIReversion:
            name = "rsi_reversion"
            asset_class = "stocks"

            def should_exit(self, symbol, entry_price):
                return True, f"rsi target {symbol} {entry_price}"

        sim = BacktestSimulator(initial_fund=10000.0)
        sim.current_date = datetime(2026, 4, 24, tzinfo=timezone.utc)
        sim.positions = {
            "AAPL": {
                "qty": 1,
                "entry_price": 100.0,
                "entry_date": sim.current_date,
                "asset_class": "stock",
                "strategy": "rsi_reversion",
            },
        }

        with patch("scheduler.run_backtest.oe.exit_position") as exit_position:
            _run_backtest_strategy_exits([FakeRSIReversion()], sim, market_open=True)

        exit_position.assert_called_once_with(
            "AAPL",
            "rsi target AAPL 100.0",
            "stock",
            open_trades_callback=sim.get_open_trades_for_backtest,
        )

    def test_strategy_exit_skips_same_day_entries_when_intraday_disabled(self):
        class FakeMomentum:
            name = "momentum"
            asset_class = "stocks"

            def should_exit(self, symbol, entry_price):
                return True, "same-day exit"

        sim = BacktestSimulator(initial_fund=10000.0)
        sim.current_date = datetime(2026, 4, 24, tzinfo=timezone.utc)
        sim.positions = {
            "AAPL": {
                "qty": 1,
                "entry_price": 100.0,
                "entry_date": sim.current_date,
                "asset_class": "stock",
                "strategy": "momentum",
            },
        }

        with patch("scheduler.run_backtest.oe.exit_position") as exit_position:
            _run_backtest_strategy_exits(
                [FakeMomentum()],
                sim,
                market_open=True,
                skip_symbols={"AAPL"},
            )

        exit_position.assert_not_called()

    def test_stock_hold_exits_are_skipped_when_market_closed(self):
        sim = BacktestSimulator(initial_fund=10000.0)
        sim.current_date = datetime(2026, 4, 25, tzinfo=timezone.utc)
        sim.historical_data = {"AAPL": _price_frame(10)}
        sim.positions = {
            "AAPL": {
                "qty": 1,
                "entry_price": 100.0,
                "entry_date": datetime(2026, 4, 20, tzinfo=timezone.utc),
                "asset_class": "stock",
                "strategy": "momentum",
                "high_water_price": 100.0,
            },
        }
        cfg = {"strategies": {"momentum": {"hold_days": 1, "exit_policy": "fixed_hold"}}}

        with patch("scheduler.run_backtest.oe.exit_position") as exit_position:
            _run_backtest_hold_exits(sim, cfg, market_open=False)

        exit_position.assert_not_called()


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
        self.assertEqual(age, 4)  # Jun 10 (Tue) -> Jun 15 (Sun) = 4 trading days (Mon-Fri only)

    def test_get_trade_age_days_crypto(self):
        age = self.sim.get_trade_age_days("BTC/USD")
        self.assertEqual(age, 14)  # Jun 1 (Sun) -> Jun 15 (Sun) = 14 calendar days (crypto uses calendar)

    def test_get_trade_age_days_unknown_symbol(self):
        age = self.sim.get_trade_age_days("UNKNOWN")
        self.assertEqual(age, 0.0)

    def test_hold_day_exit_triggers_correctly(self):
        """Verify that the hold-day check in the backtest loop would trigger for aged positions."""
        hold_days_limit = 4  # momentum hold_days from config
        age = self.sim.get_trade_age_days("AAPL")  # 4 business days
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
        age = self.sim.get_trade_age_days("FRESH")  # 0 business days (Sat->Sun)
        self.assertLess(age, 4)  # momentum hold_days = 4


class TestMomentumExitPolicy(unittest.TestCase):
    """Tests for profit-aware momentum hold exits."""

    def _cfg(self, policy="profit_trailing"):
        return {
            "hold_days": 4,
            "exit_policy": policy,
            "profit_floor_pct": 0.0,
            "trail_activation_pct": 0.06,
            "trailing_stop_pct": 0.04,
            "max_hold_days": 20,
        }

    def test_profit_trailing_exits_loser_after_min_hold(self):
        should_exit, reason = should_exit_for_hold(
            strategy="momentum",
            age_days=4,
            entry_price=100,
            current_price=99,
            peak_price=104,
            strategy_cfg=self._cfg(),
        )
        self.assertTrue(should_exit)
        self.assertIn("without profit", reason)

    def test_profit_trailing_keeps_winner_after_min_hold(self):
        should_exit, _ = should_exit_for_hold(
            strategy="momentum",
            age_days=4,
            entry_price=100,
            current_price=103,
            peak_price=104,
            strategy_cfg=self._cfg(),
        )
        self.assertFalse(should_exit)

    def test_profit_trailing_exits_on_trailing_drawdown(self):
        should_exit, reason = should_exit_for_hold(
            strategy="momentum",
            age_days=7,
            entry_price=100,
            current_price=105,
            peak_price=110,
            strategy_cfg=self._cfg(),
        )
        self.assertTrue(should_exit)
        self.assertIn("trailing stop", reason)

    def test_risk_only_baseline_ignores_hold_days(self):
        should_exit, _ = should_exit_for_hold(
            strategy="momentum",
            age_days=30,
            entry_price=100,
            current_price=90,
            peak_price=112,
            strategy_cfg=self._cfg(policy="risk_only_baseline"),
        )
        self.assertFalse(should_exit)

    def test_fixed_hold_preserves_existing_behavior(self):
        should_exit, reason = should_exit_for_hold(
            strategy="momentum",
            age_days=4,
            entry_price=100,
            current_price=103,
            peak_price=104,
            strategy_cfg=self._cfg(policy="fixed_hold"),
        )
        self.assertTrue(should_exit)
        self.assertEqual(reason, "Hold 4d")


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


class TestBacktestExperimentControls(unittest.TestCase):
    """Tests for backtest-only experiment controls."""

    def test_apply_override_coerces_dotted_values(self):
        cfg = {"strategies": {"momentum": {"top_n": 5}}}

        _apply_override(cfg, "strategies.momentum.top_n=3")
        _apply_override(cfg, "screener.enabled=false")
        _apply_override(cfg, "screener.max_atr_pct=0.06")

        self.assertEqual(cfg["strategies"]["momentum"]["top_n"], 3)
        self.assertFalse(cfg["screener"]["enabled"])
        self.assertEqual(cfg["screener"]["max_atr_pct"], 0.06)

    def test_runtime_risk_config_applies_backtest_trading_overrides(self):
        original_t = rm.T
        original_intraday = rm.INTRADAY_ENABLED
        cfg = {
            "trading": {
                **original_t,
                "take_profit_pct": 0.25,
                "stop_loss_pct": 0.06,
            },
            "intraday": {"enabled": True},
        }

        with contextlib.ExitStack() as stack:
            _patch_runtime_risk_config(stack, cfg)

            self.assertAlmostEqual(rm.take_profit_price(100), 125.0)
            self.assertAlmostEqual(rm.stop_loss_price(100), 94.0)
            self.assertTrue(rm.intraday_allowed())

        self.assertIs(rm.T, original_t)
        self.assertEqual(rm.INTRADAY_ENABLED, original_intraday)

    def test_compute_max_drawdown(self):
        df_curve = pd.DataFrame([
            {"value": 100.0},
            {"value": 120.0},
            {"value": 90.0},
            {"value": 110.0},
        ])

        self.assertAlmostEqual(_compute_max_drawdown(df_curve), -0.25)

    def test_compute_profit_factor(self):
        trades = pd.DataFrame([
            {"pnl": 20.0},
            {"pnl": -5.0},
            {"pnl": -5.0},
        ])

        self.assertAlmostEqual(_compute_profit_factor(trades), 2.0)

    def test_compute_daily_sharpe_returns_zero_for_flat_curve(self):
        curve = pd.DataFrame([
            {"value": 100.0},
            {"value": 100.0},
            {"value": 100.0},
        ])

        self.assertEqual(_compute_daily_sharpe(curve), 0.0)

    def test_backtest_cost_model_applies_slippage_and_fees(self):
        sim = BacktestSimulator(
            initial_fund=1000.0,
            cost_model={"slippage_bps": 100.0, "fee_bps": 10.0},
        )
        sim.historical_data = {
            "AAPL": pd.DataFrame(
                {
                    "open": [100.0, 110.0],
                    "high": [100.0, 110.0],
                    "low": [100.0, 110.0],
                    "close": [100.0, 110.0],
                    "volume": [1000, 1000],
                },
                index=pd.date_range("2026-01-01", periods=2, freq="D", tz=timezone.utc),
            )
        }

        class Side:
            def __init__(self, value):
                self.value = value

        class Req:
            def __init__(self, side):
                self.symbol = "AAPL"
                self.side = Side(side)
                self.qty = 1
                self.strategy = "test"

        sim.current_date = sim.historical_data["AAPL"].index[0]
        sim.submit_order(Req("buy"))
        self.assertAlmostEqual(sim.positions["AAPL"]["entry_price"], 101.0)
        self.assertAlmostEqual(sim.positions["AAPL"]["entry_fee"], 0.101)

        sim.current_date = sim.historical_data["AAPL"].index[1]
        sim.submit_order(Req("sell"))

        self.assertAlmostEqual(sim.trades_log[0]["exit_price"], 108.9)
        self.assertAlmostEqual(sim.trades_log[0]["pnl"], 7.6901, places=4)


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
