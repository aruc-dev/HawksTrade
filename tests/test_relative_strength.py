import unittest
from types import SimpleNamespace
from unittest.mock import patch

from strategies.relative_strength import RelativeStrengthStrategy


def _bar(close, high=None, low=None, volume=1000):
    return SimpleNamespace(
        close=float(close),
        high=float(high if high is not None else close * 1.01),
        low=float(low if low is not None else close * 0.99),
        volume=float(volume),
        timestamp="2026-04-23T00:00:00+00:00",
    )


def _bars_with_lookback_return(start=100.0, end=110.0, length=60):
    prefix = [_bar(start, volume=1000) for _ in range(length - 21)]
    path = []
    for idx in range(21):
        close = start + (end - start) * idx / 20
        volume = 3000 if idx == 20 else 1000
        path.append(_bar(close, volume=volume))
    return prefix + path


class RelativeStrengthStrategyTests(unittest.TestCase):
    def _base_scfg(self):
        return {
            "enabled": True,
            "top_n": 2,
            "lookback_days": 20,
            "benchmark_symbol": "SPY",
            "min_rs_pct": 0.02,
            "min_abs_return_pct": 0.03,
            "recent_lookback_days": 3,
            "max_recent_return_pct": 0.50,
            "require_price_above_sma": True,
            "trend_sma_days": 50,
            "max_trend_extension_pct": 0.50,
            "atr_period": 14,
            "atr_multiplier": 1.2,
            "max_stop_loss_pct": 0.05,
            "risk_per_trade_pct": 0.01,
            "max_positions_per_sector": 1,
            "breadth_green_threshold": 0.50,
            "breadth_red_threshold": 0.25,
            "min_breadth_coverage_pct": 0.0,
            "yellow_max_positions": 1,
            "volume_confirmation_mode": "daily",
            "volume_spike_ratio": 1.2,
            "volume_avg_period": 20,
        }

    def test_scan_emits_top_relative_strength_leader(self):
        bars_resp = {
            "AAPL": _bars_with_lookback_return(100.0, 116.0),
            "MSFT": _bars_with_lookback_return(100.0, 109.0),
        }
        regime_bars = {"SPY": _bars_with_lookback_return(100.0, 104.0)}

        with (
            patch("strategies.relative_strength.ac.get_stock_bars", return_value=bars_resp),
            patch("strategies.relative_strength.rm.market_regime_ok", return_value=True),
            patch("strategies.relative_strength.rm.market_breadth_pct", return_value=0.8),
            patch("strategies.relative_strength.ac.get_portfolio_value", return_value=100000.0),
            patch("strategies.momentum.get_sector", side_effect=lambda symbol: f"Sector_{symbol}"),
            patch("strategies.relative_strength.get_sector", side_effect=lambda symbol: f"Sector_{symbol}"),
            patch.dict("strategies.relative_strength.SCFG", self._base_scfg()),
        ):
            signals = RelativeStrengthStrategy().scan(["AAPL", "MSFT"], regime_bars=regime_bars)

        self.assertEqual([signal["symbol"] for signal in signals], ["AAPL", "MSFT"])
        self.assertEqual(signals[0]["strategy"], "relative_strength")
        self.assertGreater(signals[0]["relative_strength_score"], signals[1]["relative_strength_score"])
        self.assertIn("20d RS vs SPY", signals[0]["reason"])

    def test_scan_rejects_stock_that_does_not_beat_benchmark(self):
        bars_resp = {"AAPL": _bars_with_lookback_return(100.0, 105.0)}
        regime_bars = {"SPY": _bars_with_lookback_return(100.0, 105.0)}

        with (
            patch("strategies.relative_strength.ac.get_stock_bars", return_value=bars_resp),
            patch("strategies.relative_strength.rm.market_regime_ok", return_value=True),
            patch("strategies.relative_strength.rm.market_breadth_pct", return_value=0.8),
            patch("strategies.relative_strength.ac.get_portfolio_value") as get_portfolio_value,
            patch.dict("strategies.relative_strength.SCFG", self._base_scfg()),
        ):
            signals = RelativeStrengthStrategy().scan(["AAPL"], regime_bars=regime_bars)

        self.assertEqual(signals, [])
        get_portfolio_value.assert_not_called()

    def test_scan_counts_existing_symbols_against_sector_cap(self):
        bars_resp = {
            "NVDA": _bars_with_lookback_return(100.0, 118.0),
            "JPM": _bars_with_lookback_return(100.0, 112.0),
        }
        regime_bars = {"SPY": _bars_with_lookback_return(100.0, 103.0)}

        def mock_sector(symbol):
            if symbol in {"AAPL", "NVDA"}:
                return "Technology"
            return "Financials"

        with (
            patch("strategies.relative_strength.ac.get_stock_bars", return_value=bars_resp),
            patch("strategies.relative_strength.rm.market_regime_ok", return_value=True),
            patch("strategies.relative_strength.rm.market_breadth_pct", return_value=0.8),
            patch("strategies.relative_strength.ac.get_portfolio_value", return_value=100000.0),
            patch("strategies.momentum.get_sector", side_effect=mock_sector),
            patch("strategies.relative_strength.get_sector", side_effect=mock_sector),
            patch.dict("strategies.relative_strength.SCFG", self._base_scfg()),
        ):
            signals = RelativeStrengthStrategy().scan(["NVDA", "JPM"], regime_bars=regime_bars, existing_symbols=["AAPL"])

        self.assertEqual([signal["symbol"] for signal in signals], ["JPM"])

    def test_should_exit_is_scheduler_managed(self):
        self.assertEqual(RelativeStrengthStrategy().should_exit("AAPL", 100.0), (False, ""))


if __name__ == "__main__":
    unittest.main()
