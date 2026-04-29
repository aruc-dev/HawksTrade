import unittest
from types import SimpleNamespace
from unittest.mock import patch

from strategies.ma_crossover import MACrossoverStrategy
from strategies.range_breakout import RangeBreakoutStrategy


def _bar(close, high=None, low=None, volume=1000):
    return SimpleNamespace(
        close=float(close),
        high=float(high if high is not None else close + 2),
        low=float(low if low is not None else close - 2),
        volume=float(volume),
    )


class CryptoStrategySymbolTests(unittest.TestCase):
    def test_ma_crossover_scan_accepts_slashless_crypto_universe_symbol(self):
        bars = [_bar(100) for _ in range(25)] + [_bar(80)] + [_bar(120)]

        with (
            patch("strategies.ma_crossover.ac.get_crypto_bars", return_value={"BTC/USD": bars}),
            patch("strategies.ma_crossover.rm.crypto_regime_ok", return_value=True),
            patch.dict("strategies.ma_crossover.SCFG", {"volume_spike_ratio": 0}),
        ):
            signals = MACrossoverStrategy().scan(["BTCUSD"])

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["symbol"], "BTCUSD")

    def test_ma_crossover_exit_accepts_slashless_crypto_symbol(self):
        bars = [_bar(100) for _ in range(25)] + [_bar(120)] + [_bar(80)]

        with patch("strategies.ma_crossover.ac.get_crypto_bars", return_value={"BTC/USD": bars}):
            should_exit, reason = MACrossoverStrategy().should_exit("BTCUSD", entry_price=100)

        self.assertTrue(should_exit)
        self.assertIn("crossed below", reason)

    def test_ma_crossover_scan_skips_missing_symbol_without_warning(self):
        with (
            patch("strategies.ma_crossover.ac.get_crypto_bars", return_value={"BTC/USD": []}),
            patch("strategies.ma_crossover.rm.crypto_regime_ok", return_value=True),
            patch("strategies.ma_crossover.log.warning") as warning,
        ):
            signals = MACrossoverStrategy().scan(["BTC/USD", "SOL/USD"])

        self.assertEqual(signals, [])
        warning.assert_not_called()

    def test_range_breakout_scan_accepts_slashless_crypto_universe_symbol(self):
        bars = [_bar(80 + idx * 0.4) for idx in range(60)]
        bars.append(_bar(100, high=101, low=99, volume=1000))
        bars.append(_bar(102, high=103, low=99, volume=3000))

        with (
            patch("strategies.range_breakout.ac.get_crypto_bars", return_value={"BTC/USD": bars}),
            patch("strategies.range_breakout.rm.crypto_regime_ok", return_value=True),
            patch("strategies.range_breakout.ac.get_portfolio_value", return_value=10000.0),
            patch.dict("strategies.range_breakout.SCFG", {
                "volume_multiplier": 0,
                "vol_filter_period": 0,
                "rsi_entry_max": 100,
            }),
        ):
            signals = RangeBreakoutStrategy().scan(["BTCUSD"])

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["symbol"], "BTCUSD")

    def test_range_breakout_allows_fractional_crypto_position_size(self):
        bars = [_bar(48000 + idx * 10, high=48100 + idx * 10, low=47900 + idx * 10) for idx in range(60)]
        bars.append(_bar(50000, high=50100, low=49900, volume=1000))
        bars.append(_bar(51000, high=51100, low=50000, volume=3000))

        with (
            patch("strategies.range_breakout.ac.get_crypto_bars", return_value={"BTC/USD": bars}),
            patch("strategies.range_breakout.rm.crypto_regime_ok", return_value=True),
            patch("strategies.range_breakout.ac.get_portfolio_value", return_value=10000.0),
            patch.dict("strategies.range_breakout.SCFG", {
                "volume_multiplier": 0,
                "vol_filter_period": 0,
                "rsi_entry_max": 100,
            }),
        ):
            signals = RangeBreakoutStrategy().scan(["BTC/USD"])

        self.assertEqual(len(signals), 1)
        self.assertGreater(signals[0]["atr_risk_qty"], 0)
        self.assertLess(signals[0]["atr_risk_qty"], 1)

    def test_range_breakout_scan_skips_missing_symbol_without_warning(self):
        with (
            patch("strategies.range_breakout.ac.get_crypto_bars", return_value={"BTC/USD": []}),
            patch("strategies.range_breakout.rm.crypto_regime_ok", return_value=True),
            patch("strategies.range_breakout.ac.get_portfolio_value") as get_portfolio_value,
            patch("strategies.range_breakout.log.warning") as warning,
        ):
            signals = RangeBreakoutStrategy().scan(["BTC/USD", "SOL/USD"])

        self.assertEqual(signals, [])
        get_portfolio_value.assert_not_called()
        warning.assert_not_called()

    def test_range_breakout_skips_malformed_ohlc_window(self):
        bars = [_bar(80 + idx * 0.4) for idx in range(60)]
        bars.append(_bar(100, high=101, low=99, volume=1000))
        bars.append(_bar(102, high=100, low=103, volume=3000))

        with (
            patch("strategies.range_breakout.ac.get_crypto_bars", return_value={"BTC/USD": bars}),
            patch("strategies.range_breakout.rm.crypto_regime_ok", return_value=True),
            patch("strategies.range_breakout.ac.get_portfolio_value") as get_portfolio_value,
            patch.dict("strategies.range_breakout.SCFG", {
                "volume_multiplier": 0,
                "vol_filter_period": 0,
                "rsi_entry_max": 100,
            }),
        ):
            signals = RangeBreakoutStrategy().scan(["BTC/USD"])

        self.assertEqual(signals, [])
        get_portfolio_value.assert_not_called()

    def test_range_breakout_skips_zero_average_volume_confirmation(self):
        bars = [_bar(80 + idx * 0.2, volume=0) for idx in range(60)]
        bars.append(_bar(100, high=101, low=99, volume=0))
        bars.append(_bar(103, high=104, low=100, volume=5000))

        with (
            patch("strategies.range_breakout.ac.get_crypto_bars", return_value={"BTC/USD": bars}),
            patch("strategies.range_breakout.rm.crypto_regime_ok", return_value=True),
            patch("strategies.range_breakout.ac.get_portfolio_value", return_value=10000.0),
            patch.dict("strategies.range_breakout.SCFG", {
                "volume_multiplier": 1.8,
                "vol_filter_period": 0,
                "rsi_entry_max": 100,
            }),
        ):
            signals = RangeBreakoutStrategy().scan(["BTC/USD"])

        self.assertEqual(signals, [])

    def test_range_breakout_scan_fail_closed_when_portfolio_value_unavailable(self):
        bars = [_bar(80 + idx * 0.4) for idx in range(60)]
        bars.append(_bar(100, high=101, low=99, volume=1000))
        bars.append(_bar(102, high=103, low=99, volume=3000))

        with (
            patch("strategies.range_breakout.ac.get_crypto_bars", return_value={"BTC/USD": bars}),
            patch("strategies.range_breakout.rm.crypto_regime_ok", return_value=True),
            patch("strategies.range_breakout.ac.get_portfolio_value", side_effect=RuntimeError("account unavailable")),
            patch.dict("strategies.range_breakout.SCFG", {
                "volume_multiplier": 0,
                "vol_filter_period": 0,
                "rsi_entry_max": 100,
            }),
        ):
            signals = RangeBreakoutStrategy().scan(["BTC/USD"])

        self.assertEqual(signals, [])

    def test_range_breakout_skips_overextended_breakout_close(self):
        bars = [_bar(80 + idx * 0.2) for idx in range(60)]
        bars.append(_bar(100, high=101, low=99, volume=1000))
        bars.append(_bar(112, high=113, low=100, volume=5000))

        with (
            patch("strategies.range_breakout.ac.get_crypto_bars", return_value={"BTC/USD": bars}),
            patch("strategies.range_breakout.rm.crypto_regime_ok", return_value=True),
            patch("strategies.range_breakout.ac.get_portfolio_value", return_value=10000.0),
            patch.dict("strategies.range_breakout.SCFG", {
                "volume_multiplier": 1.8,
                "vol_filter_period": 0,
                "rsi_entry_max": 100,
                "max_breakout_extension_pct": 0.05,
            }),
        ):
            signals = RangeBreakoutStrategy().scan(["BTC/USD"])

        self.assertEqual(signals, [])

    def test_range_breakout_ranks_stronger_crypto_signals_first(self):
        weak = [_bar(80 + idx * 0.3) for idx in range(60)]
        weak.append(_bar(100, high=101, low=99, volume=1000))
        weak.append(_bar(102, high=103, low=99, volume=2500))
        strong = [_bar(70 + idx * 0.3) for idx in range(60)]
        strong.append(_bar(100, high=101, low=99, volume=1000))
        strong.append(_bar(105, high=106, low=99, volume=6000))

        with (
            patch("strategies.range_breakout.ac.get_crypto_bars", return_value={
                "BTC/USD": weak,
                "SOL/USD": strong,
            }),
            patch("strategies.range_breakout.rm.crypto_regime_ok", return_value=True),
            patch("strategies.range_breakout.ac.get_portfolio_value", return_value=10000.0),
            patch.dict("strategies.range_breakout.SCFG", {
                "volume_multiplier": 1.8,
                "vol_filter_period": 0,
                "rsi_entry_max": 100,
            }),
        ):
            signals = RangeBreakoutStrategy().scan(["BTC/USD", "SOL/USD"])

        self.assertEqual([sig["symbol"] for sig in signals], ["SOL/USD", "BTC/USD"])
        self.assertGreater(signals[0]["confidence"], signals[1]["confidence"])

    def test_range_breakout_exit_fires_on_failed_breakout(self):
        bars = [_bar(105, high=106, low=104) for _ in range(60)]
        bars.append(_bar(97, high=99, low=96))

        with (
            patch("strategies.range_breakout.ac.get_crypto_bars", return_value={"BTC/USD": bars}),
            patch.dict("strategies.range_breakout.SCFG", {
                "breakdown_exit_pct": 0.02,
                "trend_exit_enabled": False,
            }),
        ):
            should_exit, reason = RangeBreakoutStrategy().should_exit("BTC/USD", entry_price=100)

        self.assertTrue(should_exit)
        self.assertIn("failed", reason)

    def test_range_breakout_exit_fires_when_trend_filter_fails(self):
        bars = [_bar(120, high=121, low=119) for _ in range(60)]
        bars.append(_bar(101, high=103, low=100))

        with (
            patch("strategies.range_breakout.ac.get_crypto_bars", return_value={"BTC/USD": bars}),
            patch.dict("strategies.range_breakout.SCFG", {
                "breakdown_exit_pct": 0.05,
                "trend_exit_enabled": True,
            }),
        ):
            should_exit, reason = RangeBreakoutStrategy().should_exit("BTC/USD", entry_price=100)

        self.assertTrue(should_exit)
        self.assertIn("trend failed", reason)


if __name__ == "__main__":
    unittest.main()
