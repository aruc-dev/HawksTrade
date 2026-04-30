import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from strategies.gap_up import GapUpStrategy


def _bar(open_, high, low, close, volume=1000):
    return SimpleNamespace(
        open=float(open_),
        high=float(high),
        low=float(low),
        close=float(close),
        volume=float(volume),
    )


def _eligible_gap_bars():
    bars = [_bar(90, 91, 89, 90, 1000) for _ in range(199)]
    bars.append(_bar(99, 101, 98, 100, 1000))
    bars.append(_bar(105, 108, 104, 106, 3000))
    return bars


class GapUpStrategyTests(unittest.TestCase):
    def test_scan_reuses_single_portfolio_value_for_multiple_candidates(self):
        bars = _eligible_gap_bars()

        with (
            patch("strategies.gap_up.ac.get_stock_bars", return_value={"AAPL": bars, "MSFT": bars}),
            patch("strategies.gap_up.rm.market_regime_ok", return_value=True),
            patch("strategies.gap_up.ac.get_portfolio_value", return_value=10000.0) as get_portfolio_value,
            patch.dict(
                "strategies.gap_up.SCFG",
                {
                    "enabled": True,
                    "min_gap_pct": 0.03,
                    "volume_multiplier": 1.5,
                    "entry_window_minutes": 45,
                    "atr_period": 14,
                    "atr_multiplier": 2.0,
                    "risk_per_trade_pct": 0.01,
                },
            ),
        ):
            signals = GapUpStrategy().scan(
                ["AAPL", "MSFT"],
                current_time=datetime(2026, 4, 20, 13, 35),
            )

        self.assertEqual([signal["symbol"] for signal in signals], ["AAPL", "MSFT"])
        get_portfolio_value.assert_called_once()


if __name__ == "__main__":
    unittest.main()
