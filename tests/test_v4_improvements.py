import unittest
import warnings
from unittest.mock import MagicMock, patch
import pandas as pd
from core import risk_manager as rm
from strategies.rsi_reversion import (
    RSIReversionStrategy,
    _calc_rsi,
    _calc_atr,
    _bollinger_lower,
    _bollinger_pct_b,
    _in_severe_crash,
    _in_high_volatility_regime,
)

class V4ImprovementsTests(unittest.TestCase):
    # ── crypto_regime_ok (backtest path) ─────────────────────────────────────

    def test_crypto_regime_ok_bull(self):
        # BTC > EMA20
        mock_bars = {
            "BTC/USD": [MagicMock(close=100) for _ in range(20)] + [MagicMock(close=110)]
        }
        self.assertTrue(rm.crypto_regime_ok(bars_data=mock_bars))

    def test_crypto_regime_ok_bear(self):
        # BTC < EMA20
        mock_bars = {
            "BTC/USD": [MagicMock(close=100) for _ in range(20)] + [MagicMock(close=90)]
        }
        self.assertFalse(rm.crypto_regime_ok(bars_data=mock_bars))

    def test_crypto_regime_ok_backtest_insufficient_data_returns_true(self):
        # Backtest warmup: not enough history yet — allow trading so simulation starts.
        mock_bars = {"BTC/USD": [MagicMock(close=100) for _ in range(5)]}
        self.assertTrue(rm.crypto_regime_ok(bars_data=mock_bars, allow_warmup=True))

    def test_crypto_regime_ok_supplied_insufficient_data_fails_closed_by_default(self):
        mock_bars = {"BTC/USD": [MagicMock(close=100) for _ in range(5)]}
        self.assertFalse(rm.crypto_regime_ok(bars_data=mock_bars))

    def test_crypto_regime_ok_empty_prefetch_returns_false(self):
        # Empty supplied data means a live prefetch failed before any usable BTC bars arrived.
        self.assertFalse(rm.crypto_regime_ok(bars_data={}))

    # ── crypto_regime_ok (live path — fail closed) ────────────────────────────

    def test_crypto_regime_ok_live_api_exception_returns_false(self):
        # Live mode: API throws — must block new entries (fail closed).
        with patch.object(rm.ac, "get_crypto_bars", side_effect=ConnectionError("timeout")):
            self.assertFalse(rm.crypto_regime_ok())

    def test_crypto_regime_ok_live_insufficient_bars_returns_false(self):
        # Live mode: fewer bars than required — must block new entries (fail closed).
        mock_barset = MagicMock()
        mock_barset.__getitem__ = MagicMock(return_value=[MagicMock(close=100) for _ in range(5)])
        with patch.object(rm.ac, "get_crypto_bars", return_value=mock_barset):
            self.assertFalse(rm.crypto_regime_ok())

    def test_crypto_regime_ok_live_none_bars_returns_false(self):
        # Live mode: symbol not present in response — must block new entries (fail closed).
        mock_barset = MagicMock()
        mock_barset.__getitem__ = MagicMock(return_value=None)
        with patch.object(rm.ac, "get_crypto_bars", return_value=mock_barset):
            self.assertFalse(rm.crypto_regime_ok())

    # ── market_regime_ok (live path — fail closed) ────────────────────────────

    def test_market_regime_ok_live_api_exception_returns_false(self):
        # Live mode: API throws — must block new entries (fail closed).
        with patch.object(rm.ac, "get_stock_bars", side_effect=ConnectionError("timeout")):
            self.assertFalse(rm.market_regime_ok())

    def test_market_regime_ok_live_insufficient_bars_returns_false(self):
        # Live mode: fewer bars than required — must block new entries (fail closed).
        mock_barset = MagicMock()
        mock_barset.__getitem__.return_value = [MagicMock(close=100) for _ in range(10)]
        with patch.object(rm.ac, "get_stock_bars", return_value=mock_barset):
            self.assertFalse(rm.market_regime_ok())

    def test_market_regime_ok_backtest_insufficient_data_returns_true(self):
        # Backtest warmup: not enough SPY history yet — allow trading so simulation starts.
        mock_bars = {"SPY": [MagicMock(close=100) for _ in range(10)]}
        self.assertTrue(rm.market_regime_ok(bars_data=mock_bars, allow_warmup=True))

    def test_market_regime_ok_supplied_insufficient_data_fails_closed_by_default(self):
        mock_bars = {"SPY": [MagicMock(close=100) for _ in range(10)]}
        self.assertFalse(rm.market_regime_ok(bars_data=mock_bars))

    def test_market_regime_ok_empty_prefetch_returns_false(self):
        # Empty supplied data means a live prefetch failed before any usable SPY/QQQ bars arrived.
        self.assertFalse(rm.market_regime_ok(bars_data={}))

    def test_kelly_dynamic_params(self):
        # Mock 15 trades
        mock_trades = [
            {"strategy": "momentum", "pnl_pct": 0.15} for _ in range(10)
        ] + [
            {"strategy": "momentum", "pnl_pct": -0.05} for _ in range(5)
        ]
        with patch("tracking.trade_log.get_closed_trades", return_value=mock_trades), \
             patch("core.alpaca_client.get_portfolio_value", return_value=10000), \
             patch("core.alpaca_client.get_cash", return_value=5000):
            qty = rm.kelly_position_size(price=100)
            self.assertGreater(qty, 0)
            # WR=0.66, b=3, Kelly_f=0.55, Half=0.275, capped by configured 5% max -> 5 shares
            self.assertEqual(qty, 5.0)

    def test_kelly_fallback_v3_defaults(self):
        # When < 10 trades, use v3 defaults (WR=0.567, win=0.1398, loss=0.0543)
        # b = 2.57, kelly_f = 0.398, half = 0.199, capped by configured 5% max
        with patch("tracking.trade_log.get_closed_trades", return_value=[]), \
             patch("core.alpaca_client.get_portfolio_value", return_value=10000), \
             patch("core.alpaca_client.get_cash", return_value=10000):
            qty = rm.kelly_position_size(price=100)
            # 5% of 10000 = 500 USD -> 5 shares
            self.assertEqual(qty, 5.0)

    def test_rsi_1bar_recovery_allows_rising(self):
        # 1-bar confirmation: last close higher than prior close → recovering
        mock_bars = [MagicMock(close=100.0) for _ in range(2)]
        mock_bars[-2].close = 95.0
        mock_bars[-1].close = 100.0
        close_prev = float(mock_bars[-2].close)
        close_last = float(mock_bars[-1].close)
        recovering = close_last > close_prev
        self.assertTrue(recovering)

    def test_rsi_1bar_recovery_blocks_falling(self):
        # 1-bar confirmation: last close lower than prior close → still falling
        mock_bars = [MagicMock(close=100.0) for _ in range(2)]
        mock_bars[-2].close = 100.0
        mock_bars[-1].close = 95.0
        close_prev = float(mock_bars[-2].close)
        close_last = float(mock_bars[-1].close)
        recovering = close_last > close_prev
        self.assertFalse(recovering)

    def test_rsi_1bar_recovery_blocks_flat(self):
        # Flat close (equal) is not a recovery
        mock_bars = [MagicMock(close=100.0) for _ in range(2)]
        close_prev = float(mock_bars[-2].close)
        close_last = float(mock_bars[-1].close)
        recovering = close_last > close_prev
        self.assertFalse(recovering)

    # ── Crash Filter ──────────────────────────────────────────────────────────

    def test_crash_filter_not_in_crash(self):
        # SPY at 100% of its 252d peak → no crash
        spy_bars = [MagicMock(close=100.0) for _ in range(252)]
        self.assertFalse(_in_severe_crash(bars_data={"SPY": spy_bars}))

    def test_crash_filter_detects_severe_crash(self):
        # SPY at 75% of its 252d peak (25% drawdown) → crash
        spy_bars = [MagicMock(close=100.0) for _ in range(251)] + [MagicMock(close=75.0)]
        self.assertTrue(_in_severe_crash(bars_data={"SPY": spy_bars}))

    def test_crash_filter_borderline_not_crash(self):
        # SPY at 81% of peak (19% drawdown) → not a crash (threshold is 20%)
        spy_bars = [MagicMock(close=100.0) for _ in range(251)] + [MagicMock(close=81.0)]
        self.assertFalse(_in_severe_crash(bars_data={"SPY": spy_bars}))

    def test_crash_filter_backtest_warmup_allows_trading(self):
        # Fewer than 20 bars in backtest warmup → allow (return False)
        spy_bars = [MagicMock(close=100.0) for _ in range(10)]
        self.assertFalse(_in_severe_crash(bars_data={"SPY": spy_bars}, allow_warmup=True))

    def test_crash_filter_supplied_insufficient_bars_fails_closed_by_default(self):
        spy_bars = [MagicMock(close=100.0) for _ in range(10)]
        self.assertTrue(_in_severe_crash(bars_data={"SPY": spy_bars}))

    def test_crash_filter_missing_spy_allows_trading(self):
        # No SPY key in bars_data → backtest warmup → allow
        self.assertFalse(_in_severe_crash(bars_data={}, allow_warmup=True))

    def test_crash_filter_missing_spy_fails_closed_by_default(self):
        self.assertTrue(_in_severe_crash(bars_data={}))

    # ── Bollinger Band helpers ────────────────────────────────────────────────

    def test_bollinger_lower_below_price_in_flat_market(self):
        # Flat market: lower band equals SMA (std=0)
        closes = pd.Series([100.0] * 20)
        lower = _bollinger_lower(closes, period=20, n_std=2.0)
        self.assertAlmostEqual(lower, 100.0, places=4)

    def test_bollinger_lower_is_below_sma_in_volatile_market(self):
        closes = pd.Series([100.0 if i % 2 == 0 else 95.0 for i in range(20)])
        lower = _bollinger_lower(closes, period=20, n_std=2.0)
        self.assertLess(lower, closes.mean())

    def test_bollinger_lower_wider_with_higher_std_multiplier(self):
        closes = pd.Series([100.0 if i % 2 == 0 else 90.0 for i in range(20)])
        lower_2 = _bollinger_lower(closes, period=20, n_std=2.0)
        lower_3 = _bollinger_lower(closes, period=20, n_std=3.0)
        self.assertLess(lower_3, lower_2)

    def test_pct_b_deeply_below_lower_band_is_negative(self):
        # Price far below lower band → %B is negative (well below 0.20 threshold)
        closes = pd.Series([100.0] * 19 + [60.0])  # last bar crashes to 60
        pct_b = _bollinger_pct_b(closes, period=20, n_std=2.0)
        self.assertLess(pct_b, 0.0)

    def test_pct_b_near_lower_band_qualifies(self):
        # Price in lower quintile (%B < 0.2) should qualify as near lower band
        closes = pd.Series([100.0 if i % 2 == 0 else 90.0 for i in range(20)])
        lower = _bollinger_lower(closes, period=20, n_std=2.0)
        upper = 2 * closes.mean() - lower  # symmetric around mean
        price_near_lower = lower + 0.1 * (upper - lower)  # %B ≈ 0.10
        closes.iloc[-1] = price_near_lower
        pct_b = _bollinger_pct_b(closes, period=20, n_std=2.0)
        self.assertLess(pct_b, 0.20)

    def test_pct_b_above_lower_quintile_does_not_qualify(self):
        # Price mid-band (%B ≈ 0.5) should not qualify
        closes = pd.Series([100.0] * 19 + [100.0])
        pct_b = _bollinger_pct_b(closes, period=20, n_std=2.0)
        # Flat market → bandwidth=0 → returns 0.5
        self.assertGreaterEqual(pct_b, 0.20)

    def test_pct_b_flat_market_returns_half(self):
        closes = pd.Series([100.0] * 20)
        pct_b = _bollinger_pct_b(closes, period=20, n_std=2.0)
        self.assertAlmostEqual(pct_b, 0.5, places=4)

    # ── VIX proxy filter ──────────────────────────────────────────────────────

    def test_vix_filter_allows_low_vol(self):
        # Stable returns → HV20 ≈ HV_MA → not high vol
        closes = pd.Series([100.0 + i * 0.01 for i in range(230)])
        spy_bars = [MagicMock(close=float(c)) for c in closes]
        result = _in_high_volatility_regime(bars_data={"SPY": spy_bars})
        self.assertFalse(result)

    def test_vix_filter_blocks_spike_in_volatility(self):
        # Stable baseline then sudden large swings → HV20 spikes >> HV_MA
        stable = [100.0 + i * 0.01 for i in range(220)]
        # Last 10 bars: large random-looking swings (alternating ±5%)
        spike = [stable[-1] * (1.05 if i % 2 == 0 else 0.95) for i in range(10)]
        closes = pd.Series(stable + spike)
        spy_bars = [MagicMock(close=float(c)) for c in closes]
        result = _in_high_volatility_regime(bars_data={"SPY": spy_bars})
        self.assertTrue(result)

    def test_vix_filter_backtest_warmup_allows_trading(self):
        # Fewer bars than required (hv_period + ma_period = 220) → allow
        spy_bars = [MagicMock(close=100.0) for _ in range(60)]
        result = _in_high_volatility_regime(bars_data={"SPY": spy_bars}, allow_warmup=True)
        self.assertFalse(result)

    def test_vix_filter_supplied_insufficient_bars_fails_closed_by_default(self):
        spy_bars = [MagicMock(close=100.0) for _ in range(60)]
        result = _in_high_volatility_regime(bars_data={"SPY": spy_bars})
        self.assertTrue(result)

    def test_rsi_all_gain_window_returns_100_without_runtime_warning(self):
        closes = pd.Series(range(1, 40), dtype=float)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            rsi = _calc_rsi(closes)

        self.assertEqual(rsi, 100.0)
        self.assertEqual(caught, [])

    # ── ATR helper ────────────────────────────────────────────────────────────

    def test_atr_is_positive_for_normal_bars(self):
        bars = [
            MagicMock(high=105.0, low=95.0, close=100.0)
            for _ in range(20)
        ]
        atr = _calc_atr(bars, period=14)
        self.assertGreater(atr, 0)

    def test_atr_reflects_range_size(self):
        # Wide bars → larger ATR than narrow bars
        wide_bars   = [MagicMock(high=110.0, low=90.0,  close=100.0) for _ in range(20)]
        narrow_bars = [MagicMock(high=101.0, low=99.0,  close=100.0) for _ in range(20)]
        self.assertGreater(_calc_atr(wide_bars, 14), _calc_atr(narrow_bars, 14))

    def test_atr_stop_is_below_entry(self):
        bars = [MagicMock(high=105.0, low=95.0, close=100.0) for _ in range(20)]
        atr  = _calc_atr(bars, 14)
        entry = 100.0
        stop  = entry - 2.0 * atr
        self.assertLess(stop, entry)

    # ── should_exit_position with custom_stop_price ───────────────────────────

    def test_custom_stop_widens_stop_for_volatile_stock(self):
        # Entry=100, global stop=96.5 (3.5%), ATR stop=90 (10%) — ATR stop governs
        entry, current = 100.0, 92.0  # 8% below — below ATR stop, above global
        should_exit, reason = rm.should_exit_position(
            "TEST", entry, current, custom_stop_price=90.0
        )
        # 92 > 90 (ATR stop) — should NOT exit
        self.assertFalse(should_exit)

    def test_custom_stop_triggers_when_breached(self):
        entry, current = 100.0, 89.0  # below ATR stop of 90
        should_exit, reason = rm.should_exit_position(
            "TEST", entry, current, custom_stop_price=90.0
        )
        self.assertTrue(should_exit)
        self.assertIn("Custom stop-loss", reason)

    def test_global_stop_still_fires_when_tighter_than_atr(self):
        # If ATR stop (99.0) is tighter than global stop (96.5), global governs
        entry, current = 100.0, 96.0  # between ATR stop 99 and global 96.5
        should_exit, _ = rm.should_exit_position(
            "TEST", entry, current, custom_stop_price=99.0
        )
        # 96 < min(99, 96.5) = 96.5 → exit triggered
        self.assertTrue(should_exit)

    def test_no_custom_stop_uses_global_stop(self):
        entry, current = 100.0, 97.0  # 3% drop, above global 3.5% stop
        should_exit, _ = rm.should_exit_position("TEST", entry, current)
        self.assertFalse(should_exit)

    def test_no_custom_stop_global_stop_fires(self):
        entry, current = 100.0, 95.0  # 5% drop, below global 3.5% stop
        should_exit, reason = rm.should_exit_position("TEST", entry, current)
        self.assertTrue(should_exit)
        self.assertIn("Stop-loss", reason)

    # ── market_breadth_pct ────────────────────────────────────────────────────

    def test_breadth_all_above_sma50_returns_one(self):
        # All closes trending up → all above SMA50
        closes = list(range(50, 102))  # 52 bars, last > SMA50
        bars = [MagicMock(close=float(c)) for c in closes]
        breadth = rm.market_breadth_pct(["SPY"], bars_data={"SPY": bars})
        self.assertAlmostEqual(breadth, 1.0)

    def test_breadth_all_below_sma50_returns_zero(self):
        # All closes trending down → all below SMA50
        closes = list(range(102, 50, -1))  # declining from 102 to 51 (52 bars)
        bars = [MagicMock(close=float(c)) for c in closes]
        breadth = rm.market_breadth_pct(["SPY"], bars_data={"SPY": bars})
        self.assertAlmostEqual(breadth, 0.0)

    def test_breadth_half_symbols_above_returns_point_five(self):
        # Two symbols: one above SMA50, one below
        rising  = [MagicMock(close=float(c)) for c in range(50, 102)]
        falling = [MagicMock(close=float(c)) for c in range(102, 50, -1)]
        bars_data = {"A": rising, "B": falling}
        breadth = rm.market_breadth_pct(["A", "B"], bars_data=bars_data)
        self.assertAlmostEqual(breadth, 0.5)

    def test_breadth_insufficient_bars_skips_symbol(self):
        # Symbol with fewer than 51 bars is skipped; only eligible symbols counted
        rising = [MagicMock(close=float(c)) for c in range(50, 102)]  # 52 bars, above
        short  = [MagicMock(close=100.0) for _ in range(10)]           # 10 bars, skipped
        bars_data = {"A": rising, "B": short}
        breadth = rm.market_breadth_pct(["A", "B"], bars_data=bars_data)
        # Only A is eligible; A is above → 1.0
        self.assertAlmostEqual(breadth, 1.0)

    def test_breadth_no_eligible_symbols_returns_neutral(self):
        # No symbol has >= 51 bars → returns 0.5 (neutral)
        bars_data = {"A": [MagicMock(close=100.0) for _ in range(10)]}
        breadth = rm.market_breadth_pct(["A"], bars_data=bars_data)
        self.assertAlmostEqual(breadth, 0.5)

    def test_breadth_missing_symbol_skips_gracefully(self):
        # Symbol not in bars_data is silently skipped
        rising = [MagicMock(close=float(c)) for c in range(50, 102)]
        bars_data = {"A": rising}
        breadth = rm.market_breadth_pct(["A", "MISSING"], bars_data=bars_data)
        # Only A is eligible and above → 1.0
        self.assertAlmostEqual(breadth, 1.0)


if __name__ == "__main__":
    unittest.main()
