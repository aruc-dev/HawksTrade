import unittest
from types import SimpleNamespace
from unittest.mock import patch
import pandas as pd

from strategies.ma_crossover import (
    MACrossoverStrategy,
    _calc_atr,
    _detect_crossover,
)

def _bar(close, high=None, low=None, volume=1000):
    return SimpleNamespace(
        close=float(close),
        high=float(high if high is not None else close * 1.01),
        low=float(low if low is not None else close * 0.99),
        volume=float(volume),
        timestamp="2026-04-23T00:00:00+00:00",
    )

class MACrossoverStrategyTests(unittest.TestCase):

    def test_detect_crossover_bullish(self):
        fast = pd.Series([10, 12])
        slow = pd.Series([11, 11])
        self.assertEqual(_detect_crossover(fast, slow), "bullish")

    def test_detect_crossover_bearish(self):
        fast = pd.Series([12, 10])
        slow = pd.Series([11, 11])
        self.assertEqual(_detect_crossover(fast, slow), "bearish")

    def test_detect_crossover_none(self):
        fast = pd.Series([10, 10.5])
        slow = pd.Series([11, 11])
        self.assertEqual(_detect_crossover(fast, slow), "none")

    def test_calc_atr_crypto_scale(self):
        # Test with BTC-like prices
        prices = [60000] * 20
        bars = [_bar(p, high=p + 500, low=p - 500) for p in prices]
        atr = _calc_atr(bars, period=14)
        self.assertAlmostEqual(atr, 1000.0, delta=100)

    def test_scan_generates_signal_with_atr_stop(self):
        # 120 bars at 100, then 1 bar at 200.
        # fast(9) @ 119 = 100
        # slow(21) @ 119 = 100
        # Jump to 200 @ 120:
        # fast = 100 + 0.2*(200-100) = 120
        # slow = 100 + (2/22)*(100) = 109.09
        # To get a TRUE BULLISH CROSS (fast_prev < slow_prev), 
        # let's set bar 119 slightly lower to pull fast down.
        prices = [100.0] * 121
        prices[119] = 90.0 # Fast will be lower than slow
        prices[120] = 200.0 # Big jump to cross
        
        bars = [_bar(p, high=p+2, low=p-2) for p in prices]
        bars_data = {"BTC/USD": bars}

        with (
            patch("strategies.ma_crossover.ac.get_crypto_bars", return_value=bars_data),
            patch("strategies.ma_crossover.rm.crypto_regime_ok", return_value=True),
            patch("strategies.ma_crossover._calc_rsi", return_value=50.0),
        ):
            signals = MACrossoverStrategy().scan(["BTC/USD"])
            
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["symbol"], "BTC/USD")
        self.assertIn("atr_stop_price", signals[0])
        self.assertIn("ATR Stop=", signals[0]["reason"])

    def test_scan_blocks_low_volatility(self):
        # Trigger crossover but with tiny current range
        prices = [100.0] * 121
        prices[119] = 90.0
        prices[120] = 200.0
        
        # bars 110-119 have range 4 (high=102, low=98)
        bars = [_bar(p, high=p+2, low=p-2) for p in prices[:-1]]
        # current range is 0.1
        bars.append(_bar(prices[-1], high=prices[-1]+0.05, low=prices[-1]-0.05))
        
        bars_data = {"BTC/USD": bars}

        with (
            patch("strategies.ma_crossover.ac.get_crypto_bars", return_value=bars_data),
            patch("strategies.ma_crossover.rm.crypto_regime_ok", return_value=True),
            patch("strategies.ma_crossover._calc_rsi", return_value=50.0),
        ):
            signals = MACrossoverStrategy().scan(["BTC/USD"])
            
        self.assertEqual(len(signals), 0)

    # ── Config-driven RSI thresholds ─────────────────────────────────────────

    def test_scan_blocks_signal_when_rsi_below_config_min(self):
        prices = [100.0] * 121
        prices[119] = 90.0
        prices[120] = 200.0
        bars = [_bar(p, high=p+2, low=p-2) for p in prices]
        bars_data = {"BTC/USD": bars}

        with (
            patch("strategies.ma_crossover.ac.get_crypto_bars", return_value=bars_data),
            patch("strategies.ma_crossover.rm.crypto_regime_ok", return_value=True),
            patch("strategies.ma_crossover._calc_rsi", return_value=30.0),
            patch.dict("strategies.ma_crossover.SCFG", {"rsi_entry_min": 35, "rsi_entry_max": 70}),
        ):
            signals = MACrossoverStrategy().scan(["BTC/USD"])

        self.assertEqual(len(signals), 0, "RSI below rsi_entry_min must block signal")

    def test_scan_blocks_signal_when_rsi_above_config_max(self):
        prices = [100.0] * 121
        prices[119] = 90.0
        prices[120] = 200.0
        bars = [_bar(p, high=p+2, low=p-2) for p in prices]
        bars_data = {"BTC/USD": bars}

        with (
            patch("strategies.ma_crossover.ac.get_crypto_bars", return_value=bars_data),
            patch("strategies.ma_crossover.rm.crypto_regime_ok", return_value=True),
            patch("strategies.ma_crossover._calc_rsi", return_value=75.0),
            patch.dict("strategies.ma_crossover.SCFG", {"rsi_entry_min": 35, "rsi_entry_max": 70}),
        ):
            signals = MACrossoverStrategy().scan(["BTC/USD"])

        self.assertEqual(len(signals), 0, "RSI above rsi_entry_max must block signal")

    def test_scan_generates_signal_when_rsi_within_config_range(self):
        prices = [100.0] * 121
        prices[119] = 90.0
        prices[120] = 200.0
        bars = [_bar(p, high=p+2, low=p-2) for p in prices]
        bars_data = {"BTC/USD": bars}

        with (
            patch("strategies.ma_crossover.ac.get_crypto_bars", return_value=bars_data),
            patch("strategies.ma_crossover.rm.crypto_regime_ok", return_value=True),
            patch("strategies.ma_crossover._calc_rsi", return_value=50.0),
            patch.dict("strategies.ma_crossover.SCFG", {"rsi_entry_min": 35, "rsi_entry_max": 70}),
        ):
            signals = MACrossoverStrategy().scan(["BTC/USD"])

        self.assertEqual(len(signals), 1)

    def test_scan_blocks_downward_slope(self):
        # Force a crossover in a decisively downtrending slow EMA.
        # We need slow.iloc[-1] <= slow.iloc[-5].
        # Long downtrend
        prices = list(range(1000, 500, -10)) # 1000, 990, ... (50 bars)
        # fast is far below slow
        # At the end, force a fast spike to cross, but slow is still dropping from earlier high
        prices[-5] = 400.0
        prices[-4] = 400.0
        prices[-3] = 400.0
        prices[-2] = 400.0
        prices[-1] = 800.0 # Spike to cross fast above slow
        
        bars = [_bar(p) for p in prices]
        bars_data = {"BTC/USD": bars}

        with (
            patch("strategies.ma_crossover.ac.get_crypto_bars", return_value=bars_data),
            patch("strategies.ma_crossover.rm.crypto_regime_ok", return_value=True),
            patch("strategies.ma_crossover._calc_rsi", return_value=50.0),
        ):
            signals = MACrossoverStrategy().scan(["BTC/USD"])
            
        self.assertEqual(len(signals), 0)

if __name__ == "__main__":
    unittest.main()
