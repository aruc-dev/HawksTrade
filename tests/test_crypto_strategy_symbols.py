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
        bars = [_bar(80 + idx * 0.4) for idx in range(50)]
        bars.append(_bar(100, high=101, low=99, volume=1000))
        bars.append(_bar(102, high=103, low=99, volume=3000))

        with (
            patch("strategies.range_breakout.ac.get_crypto_bars", return_value={"BTC/USD": bars}),
            patch("strategies.range_breakout.rm.crypto_regime_ok", return_value=True),
        ):
            signals = RangeBreakoutStrategy().scan(["BTCUSD"])

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["symbol"], "BTCUSD")

    def test_range_breakout_scan_skips_missing_symbol_without_warning(self):
        with (
            patch("strategies.range_breakout.ac.get_crypto_bars", return_value={"BTC/USD": []}),
            patch("strategies.range_breakout.rm.crypto_regime_ok", return_value=True),
            patch("strategies.range_breakout.log.warning") as warning,
        ):
            signals = RangeBreakoutStrategy().scan(["BTC/USD", "SOL/USD"])

        self.assertEqual(signals, [])
        warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
