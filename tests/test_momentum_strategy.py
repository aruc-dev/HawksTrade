import unittest
from types import SimpleNamespace
from unittest.mock import patch

from strategies.momentum import MomentumStrategy


def _bar(close, volume=1000):
    return SimpleNamespace(
        close=float(close),
        volume=float(volume),
        timestamp="2026-04-23T00:00:00+00:00",
    )


class MomentumStrategyTests(unittest.TestCase):
    def test_scan_falls_back_to_single_symbol_fetch_when_batch_response_is_sparse(self):
        batch_bars = {"AAPL": [_bar(price) for price in (100, 101, 102, 103, 104, 105, 110)]}
        msft_bars = {"MSFT": [_bar(price) for price in (200, 201, 202, 203, 204, 205, 226)]}

        def _get_stock_bars(symbols, timeframe="1Day", limit=10):
            if symbols == ["AAPL", "MSFT"]:
                return batch_bars
            if symbols == ["MSFT"]:
                return msft_bars
            raise AssertionError(f"Unexpected symbols: {symbols}")

        with (
            patch("strategies.momentum.ac.get_stock_bars", side_effect=_get_stock_bars),
            patch("strategies.momentum.rm.market_regime_ok", return_value=True),
            patch("strategies.momentum.log.warning") as warning,
        ):
            signals = MomentumStrategy().scan(["AAPL", "MSFT"])

        self.assertEqual({signal["symbol"] for signal in signals}, {"AAPL", "MSFT"})
        warning.assert_not_called()

    def test_scan_skips_missing_symbol_without_warning_when_fallback_is_also_missing(self):
        batch_bars = {"AAPL": [_bar(price) for price in (100, 101, 102, 103, 104, 105, 110)]}

        def _get_stock_bars(symbols, timeframe="1Day", limit=10):
            if symbols == ["AAPL", "MSFT"]:
                return batch_bars
            if symbols == ["MSFT"]:
                return {}
            raise AssertionError(f"Unexpected symbols: {symbols}")

        with (
            patch("strategies.momentum.ac.get_stock_bars", side_effect=_get_stock_bars),
            patch("strategies.momentum.rm.market_regime_ok", return_value=True),
            patch("strategies.momentum.log.warning") as warning,
        ):
            signals = MomentumStrategy().scan(["AAPL", "MSFT"])

        self.assertEqual([signal["symbol"] for signal in signals], ["AAPL"])
        warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
