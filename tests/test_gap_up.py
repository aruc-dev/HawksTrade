import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from strategies.gap_up import GapUpStrategy

OPEN_TS = datetime(2026, 4, 20, 13, 35, tzinfo=timezone.utc)

def _bar(open_, high, low, close, volume=1000, timestamp=None):
    values = dict(
        open=float(open_),
        high=float(high),
        low=float(low),
        close=float(close),
        volume=float(volume),
    )
    if timestamp is not None:
        values["timestamp"] = timestamp
    return SimpleNamespace(**values)


def _eligible_daily_bars():
    bars = [_bar(90, 91, 89, 90, 1000) for _ in range(199)]
    bars.append(_bar(99, 101, 98, 100, 1000))
    return bars


def _opening_bar(open_, volume=40, close=None):
    close = open_ if close is None else close
    high = max(open_, close)
    low = min(open_, close)
    return [_bar(open_, high, low, close, volume, timestamp=OPEN_TS)]


def _stock_bars_side_effect(daily_by_symbol, opening_by_symbol):
    def _get_stock_bars(symbols, timeframe="1Day", limit=60):
        source = daily_by_symbol if timeframe == "1Day" else opening_by_symbol
        return {symbol: source.get(symbol, []) for symbol in symbols}

    return _get_stock_bars


class GapUpStrategyTests(unittest.TestCase):
    def test_scan_requires_true_gap_when_configured(self):
        bars = [_bar(90, 91, 89, 90, 1000) for _ in range(199)]
        bars.append(_bar(97, 104, 96, 98, 1000))
        opening = _opening_bar(103, volume=40)

        with (
            patch(
                "strategies.gap_up.ac.get_stock_bars",
                side_effect=_stock_bars_side_effect({"AAPL": bars}, {"AAPL": opening}),
            ),
            patch("strategies.gap_up.rm.market_regime_ok", return_value=True),
            patch("strategies.gap_up.ac.get_portfolio_value") as get_portfolio_value,
            patch.dict(
                "strategies.gap_up.SCFG",
                {
                    "enabled": True,
                    "min_gap_pct": 0.04,
                    "max_gap_pct": 0.15,
                    "volume_multiplier": 1.5,
                    "volume_avg_period": 20,
                    "entry_window_minutes": 45,
                    "atr_period": 14,
                    "atr_multiplier": 2.0,
                    "trend_sma_period": 200,
                    "require_true_gap": True,
                    "risk_per_trade_pct": 0.01,
                    "max_signals": 0,
                },
            ),
        ):
            signals = GapUpStrategy().scan(["AAPL"], current_time=datetime(2026, 4, 20, 13, 35))

        self.assertEqual(signals, [])
        get_portfolio_value.assert_not_called()

    def test_scan_applies_configured_max_gap(self):
        bars = [_bar(90, 91, 89, 90, 1000) for _ in range(199)]
        bars.append(_bar(99, 101, 98, 100, 1000))
        opening = _opening_bar(116, volume=40)

        with (
            patch(
                "strategies.gap_up.ac.get_stock_bars",
                side_effect=_stock_bars_side_effect({"AAPL": bars}, {"AAPL": opening}),
            ),
            patch("strategies.gap_up.rm.market_regime_ok", return_value=True),
            patch("strategies.gap_up.ac.get_portfolio_value") as get_portfolio_value,
            patch.dict(
                "strategies.gap_up.SCFG",
                {
                    "enabled": True,
                    "min_gap_pct": 0.04,
                    "max_gap_pct": 0.10,
                    "volume_multiplier": 1.5,
                    "volume_avg_period": 20,
                    "entry_window_minutes": 45,
                    "atr_period": 14,
                    "atr_multiplier": 2.0,
                    "trend_sma_period": 200,
                    "require_true_gap": True,
                    "risk_per_trade_pct": 0.01,
                    "max_signals": 0,
                },
            ),
        ):
            signals = GapUpStrategy().scan(["AAPL"], current_time=datetime(2026, 4, 20, 13, 35))

        self.assertEqual(signals, [])
        get_portfolio_value.assert_not_called()

    def test_scan_sorts_candidates_by_confidence(self):
        low_confidence = [_bar(90, 91, 89, 90, 1000) for _ in range(199)]
        low_confidence.append(_bar(99, 101, 98, 100, 1000))
        low_opening = _opening_bar(104.5, volume=25)

        high_confidence = [_bar(90, 91, 89, 90, 1000) for _ in range(199)]
        high_confidence.append(_bar(99, 101, 98, 100, 1000))
        high_opening = _opening_bar(108, volume=80)

        with (
            patch(
                "strategies.gap_up.ac.get_stock_bars",
                side_effect=_stock_bars_side_effect(
                    {"LOW": low_confidence, "HIGH": high_confidence},
                    {"LOW": low_opening, "HIGH": high_opening},
                ),
            ),
            patch("strategies.gap_up.rm.market_regime_ok", return_value=True),
            patch("strategies.gap_up.ac.get_portfolio_value", return_value=10000.0),
            patch.dict(
                "strategies.gap_up.SCFG",
                {
                    "enabled": True,
                    "min_gap_pct": 0.04,
                    "max_gap_pct": 0.15,
                    "volume_multiplier": 1.5,
                    "volume_avg_period": 20,
                    "entry_window_minutes": 45,
                    "atr_period": 14,
                    "atr_multiplier": 2.0,
                    "trend_sma_period": 200,
                    "require_true_gap": True,
                    "risk_per_trade_pct": 0.01,
                    "max_signals": 0,
                },
            ),
        ):
            signals = GapUpStrategy().scan(["LOW", "HIGH"], current_time=datetime(2026, 4, 20, 13, 35))

        self.assertEqual([signal["symbol"] for signal in signals], ["HIGH", "LOW"])

    def test_scan_applies_configured_signal_cap_after_ranking(self):
        low_confidence = [_bar(90, 91, 89, 90, 1000) for _ in range(199)]
        low_confidence.append(_bar(99, 101, 98, 100, 1000))
        high_confidence = [_bar(90, 91, 89, 90, 1000) for _ in range(199)]
        high_confidence.append(_bar(99, 101, 98, 100, 1000))

        with (
            patch(
                "strategies.gap_up.ac.get_stock_bars",
                side_effect=_stock_bars_side_effect(
                    {"LOW": low_confidence, "HIGH": high_confidence},
                    {"LOW": _opening_bar(104.5, volume=25), "HIGH": _opening_bar(108, volume=80)},
                ),
            ),
            patch("strategies.gap_up.rm.market_regime_ok", return_value=True),
            patch("strategies.gap_up.ac.get_portfolio_value", return_value=10000.0),
            patch.dict(
                "strategies.gap_up.SCFG",
                {
                    "enabled": True,
                    "min_gap_pct": 0.04,
                    "max_gap_pct": 0.15,
                    "volume_multiplier": 1.5,
                    "volume_avg_period": 20,
                    "entry_window_minutes": 45,
                    "atr_period": 14,
                    "atr_multiplier": 2.0,
                    "trend_sma_period": 200,
                    "require_true_gap": True,
                    "risk_per_trade_pct": 0.01,
                    "max_signals": 1,
                },
            ),
        ):
            signals = GapUpStrategy().scan(["LOW", "HIGH"], current_time=datetime(2026, 4, 20, 13, 35))

        self.assertEqual([signal["symbol"] for signal in signals], ["HIGH"])

    def test_scan_reuses_single_portfolio_value_for_multiple_candidates(self):
        bars = _eligible_daily_bars()
        opening = _opening_bar(105, volume=40)

        with (
            patch(
                "strategies.gap_up.ac.get_stock_bars",
                side_effect=_stock_bars_side_effect(
                    {"AAPL": bars, "MSFT": bars},
                    {"AAPL": opening, "MSFT": opening},
                ),
            ),
            patch("strategies.gap_up.rm.market_regime_ok", return_value=True),
            patch("strategies.gap_up.ac.get_portfolio_value", return_value=10000.0) as get_portfolio_value,
            patch.dict(
                "strategies.gap_up.SCFG",
                {
                    "enabled": True,
                    "min_gap_pct": 0.03,
                    "max_gap_pct": 0.15,
                    "volume_multiplier": 1.5,
                    "volume_avg_period": 20,
                    "entry_window_minutes": 45,
                    "atr_period": 14,
                    "atr_multiplier": 2.0,
                    "trend_sma_period": 200,
                    "require_true_gap": True,
                    "risk_per_trade_pct": 0.01,
                    "max_signals": 0,
                },
            ),
        ):
            signals = GapUpStrategy().scan(
                ["AAPL", "MSFT"],
                current_time=datetime(2026, 4, 20, 13, 35),
            )

        self.assertEqual([signal["symbol"] for signal in signals], ["AAPL", "MSFT"])
        get_portfolio_value.assert_called_once()

    def test_should_exit_failed_gap_before_hold_cap(self):
        bars = [_bar(100, 101, 99, 100, 1000) for _ in range(10)]
        bars[-1] = _bar(102, 103, 96, 96.9, 1200)

        with (
            patch("strategies.gap_up.ac.get_stock_bars", return_value={"AAPL": bars}),
            patch.dict(
                "strategies.gap_up.SCFG",
                {
                    "timeframe": "1Day",
                    "trend_sma_period": 5,
                    "breakdown_exit_pct": 0.025,
                    "trend_exit_enabled": True,
                },
            ),
        ):
            should_exit, reason = GapUpStrategy().should_exit("AAPL", 100.0)

        self.assertTrue(should_exit)
        self.assertIn("Gap-up failed", reason)


if __name__ == "__main__":
    unittest.main()
