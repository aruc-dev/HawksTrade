import unittest
from types import SimpleNamespace
from unittest.mock import patch

from strategies import crypto_rsi_reversion as module
from strategies.crypto_rsi_reversion import CryptoRSIReversionStrategy, _symbol_bars


def _bar(close, high=None, low=None, volume=1000):
    return SimpleNamespace(
        close=float(close),
        high=float(high if high is not None else close * 1.01),
        low=float(low if low is not None else close * 0.99),
        volume=float(volume),
        timestamp="2026-04-23T00:00:00+00:00",
    )


class MockBarSet:
    def __init__(self, data):
        self.data = data

    def __getitem__(self, key):
        return self.data.get(key)


class CryptoRSIReversionStrategyTests(unittest.TestCase):

    def test_symbol_bars_supports_mock_barset_without_get(self):
        bars = [_bar(100)]
        self.assertIs(_symbol_bars(MockBarSet({"BTCUSD": bars}), "BTC/USD"), bars)

    def test_scan_generates_regime_gated_signal(self):
        bars = [_bar(100.0) for _ in range(80)]
        bars_data = MockBarSet({"BTC/USD": bars})

        with (
            patch("strategies.crypto_rsi_reversion.ac.get_crypto_bars", return_value=bars_data),
            patch("strategies.crypto_rsi_reversion.rm.crypto_regime_ok", return_value=True),
            patch("strategies.crypto_rsi_reversion._calc_rsi", return_value=25.0),
            patch("strategies.crypto_rsi_reversion._bollinger_pct_b", return_value=-0.10),
            patch.dict(
                module.SCFG,
                {
                    "enabled": True,
                    "use_regime_filter": True,
                    "rsi_period": 14,
                    "oversold_threshold": 35,
                    "bb_period": 20,
                    "bb_std": 2.0,
                    "max_bollinger_pct_b": 0.40,
                    "max_loss_exit_pct": 0.10,
                    "max_signals": 3,
                    "timeframe": "1Day",
                },
            ),
        ):
            signals = CryptoRSIReversionStrategy().scan(["BTC/USD"], regime_bars={"BTC/USD": bars})

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["symbol"], "BTC/USD")
        self.assertEqual(signals[0]["strategy"], "crypto_rsi_reversion")
        self.assertIn("atr_stop_price", signals[0])
        self.assertIn("Crypto RSI mean reversion", signals[0]["reason"])

    def test_scan_blocks_when_crypto_regime_is_closed(self):
        bars = [_bar(100.0) for _ in range(80)]

        with (
            patch("strategies.crypto_rsi_reversion.ac.get_crypto_bars", return_value={"BTC/USD": bars}),
            patch("strategies.crypto_rsi_reversion.rm.crypto_regime_ok", return_value=False),
            patch.dict(module.SCFG, {"enabled": True, "use_regime_filter": True}),
        ):
            signals = CryptoRSIReversionStrategy().scan(["BTC/USD"], regime_bars={"BTC/USD": bars})

        self.assertEqual(signals, [])

    def test_should_exit_on_daily_close_max_loss(self):
        bars = [_bar(100.0) for _ in range(24)] + [_bar(89.0)]

        with (
            patch("strategies.crypto_rsi_reversion.ac.get_crypto_bars", return_value=MockBarSet({"BTC/USD": bars})),
            patch.dict(
                module.SCFG,
                {
                    "rsi_period": 14,
                    "bb_period": 20,
                    "overbought_threshold": 55,
                    "profit_floor_pct": 0.03,
                    "max_loss_exit_pct": 0.10,
                    "timeframe": "1Day",
                },
            ),
        ):
            should_exit, reason = CryptoRSIReversionStrategy().should_exit("BTC/USD", 100.0)

        self.assertTrue(should_exit)
        self.assertIn("max-loss exit", reason)


if __name__ == "__main__":
    unittest.main()
