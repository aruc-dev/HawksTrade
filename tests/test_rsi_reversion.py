import unittest
from types import SimpleNamespace
from unittest.mock import patch

from strategies.rsi_reversion import RSIReversionStrategy


def _bar(close, high=None, low=None, volume=1000):
    return SimpleNamespace(
        close=float(close),
        high=float(high if high is not None else close * 1.01),
        low=float(low if low is not None else close * 0.99),
        volume=float(volume),
    )


def _make_bars(n=210, base=95.0, last_close=96.0, last_vol=1500, vol=1000):
    """Build a minimal bar list that satisfies all numeric RSI entry conditions."""
    bars = [_bar(base, volume=vol) for _ in range(n - 1)]
    bars.append(_bar(last_close, volume=last_vol))
    return bars


class RSIReversionScanTests(unittest.TestCase):

    # ── atr_risk_qty (HIGH fix) ───────────────────────────────────────────────

    def test_scan_includes_atr_risk_qty_when_atr_is_valid(self):
        bars = _make_bars()  # 210 bars, last vol=1500 (1.5× avg=1000), recovery (96>95)

        def _get_stock_bars(symbols, timeframe="1Day", limit=210):
            if symbols == ["SPY"]:
                return {"SPY": [_bar(100) for _ in range(30)]}
            return {"AAPL": bars}

        with (
            patch("strategies.rsi_reversion.ac.get_stock_bars", side_effect=_get_stock_bars),
            patch("strategies.rsi_reversion.ac.get_portfolio_value", return_value=10000.0),
            patch("strategies.rsi_reversion._calc_rsi", return_value=25.0),
            patch("strategies.rsi_reversion._bollinger_pct_b", return_value=0.10),
            patch("strategies.rsi_reversion._calc_atr", return_value=2.0),
        ):
            signals = RSIReversionStrategy().scan(["AAPL"])

        self.assertEqual(len(signals), 1)
        self.assertIn("atr_risk_qty", signals[0])
        # atr_stop = 96 - 2*2 = 92; risk_per_share = 4; risk_dollars = 100 → qty = 25
        self.assertAlmostEqual(signals[0]["atr_risk_qty"], 25.0, places=4)

    def test_scan_skips_signal_when_atr_risk_qty_below_notional_minimum(self):
        # price≈96, atr=20 → atr_stop=56, risk_per_share=40, risk_dollars=10 → qty=0.25
        # 0.25 * 96 = 24 < min_trade_value=100 → skipped
        bars = _make_bars()

        def _get_stock_bars(symbols, timeframe="1Day", limit=210):
            if symbols == ["SPY"]:
                return {"SPY": [_bar(100) for _ in range(30)]}
            return {"AAPL": bars}

        with (
            patch("strategies.rsi_reversion.ac.get_stock_bars", side_effect=_get_stock_bars),
            patch("strategies.rsi_reversion.ac.get_portfolio_value", return_value=1000.0),
            patch("strategies.rsi_reversion._calc_rsi", return_value=25.0),
            patch("strategies.rsi_reversion._bollinger_pct_b", return_value=0.10),
            patch("strategies.rsi_reversion._calc_atr", return_value=20.0),
        ):
            signals = RSIReversionStrategy().scan(["AAPL"])

        self.assertEqual(len(signals), 0)

    # ── SMA200 upper bound (MEDIUM fix) ──────────────────────────────────────

    def test_scan_blocks_entry_when_price_above_sma200_upper_bound(self):
        # 200 bars at 100, last bar at 120; SMA200 ≈ 100.1
        # 120 > 100.1 * 1.15 = 115.1 → blocked
        bars = [_bar(100.0, volume=1000) for _ in range(200)]
        bars.append(_bar(120.0, volume=2000))

        def _get_stock_bars(symbols, timeframe="1Day", limit=210):
            if symbols == ["SPY"]:
                return {"SPY": [_bar(100) for _ in range(30)]}
            return {"AAPL": bars}

        with (
            patch("strategies.rsi_reversion.ac.get_stock_bars", side_effect=_get_stock_bars),
            patch("strategies.rsi_reversion.ac.get_portfolio_value", return_value=10000.0),
            patch("strategies.rsi_reversion._calc_rsi", return_value=25.0),
        ):
            signals = RSIReversionStrategy().scan(["AAPL"])

        self.assertEqual(len(signals), 0)

    def test_scan_blocks_entry_when_price_below_sma200_lower_bound(self):
        # 200 bars at 100, last bar at 80; 80 < 100 * 0.85 = 85 → blocked
        bars = [_bar(100.0, volume=1000) for _ in range(200)]
        bars.append(_bar(80.0, volume=2000))

        def _get_stock_bars(symbols, timeframe="1Day", limit=210):
            if symbols == ["SPY"]:
                return {"SPY": [_bar(100) for _ in range(30)]}
            return {"AAPL": bars}

        with (
            patch("strategies.rsi_reversion.ac.get_stock_bars", side_effect=_get_stock_bars),
            patch("strategies.rsi_reversion.ac.get_portfolio_value", return_value=10000.0),
            patch("strategies.rsi_reversion._calc_rsi", return_value=25.0),
        ):
            signals = RSIReversionStrategy().scan(["AAPL"])

        self.assertEqual(len(signals), 0)

    # ── SPY single fetch (MEDIUM fix) ────────────────────────────────────────

    def test_scan_fetches_spy_exactly_once_for_both_regime_filters(self):
        bars = _make_bars()
        call_log = []

        def _get_stock_bars(symbols, timeframe="1Day", limit=210):
            call_log.append(tuple(symbols))
            if symbols == ["SPY"]:
                return {"SPY": [_bar(100) for _ in range(30)]}
            return {"AAPL": bars}

        with (
            patch("strategies.rsi_reversion.ac.get_stock_bars", side_effect=_get_stock_bars),
            patch("strategies.rsi_reversion.ac.get_portfolio_value", return_value=10000.0),
            patch("strategies.rsi_reversion._calc_rsi", return_value=25.0),
            patch("strategies.rsi_reversion._bollinger_pct_b", return_value=0.10),
            patch("strategies.rsi_reversion._calc_atr", return_value=2.0),
        ):
            RSIReversionStrategy().scan(["AAPL"])

        spy_calls = [c for c in call_log if c == ("SPY",)]
        self.assertEqual(len(spy_calls), 1, "SPY bars must be fetched exactly once and shared between crash and VIX filters")

    # ── profit_floor_pct config-driven (MEDIUM fix) ──────────────────────────

    def test_should_exit_reads_profit_floor_pct_from_config(self):
        # entry=100, sma_target≈100.2 (24 bars at 100 + last at 104)
        # With profit_floor_pct=0.05: effective_target = max(100.2, 105) = 105
        # price=104 < 105 → should NOT exit
        # Old hardcoded 1.015: effective_target = max(100.2, 101.5) = 101.5 → 104 ≥ 101.5 → exits
        bars = [SimpleNamespace(close=100.0, volume=1000.0) for _ in range(24)]
        bars.append(SimpleNamespace(close=104.0, volume=1000.0))

        with (
            patch("strategies.rsi_reversion.ac.get_stock_bars", return_value={"AAPL": bars}),
            patch("strategies.rsi_reversion._calc_rsi", return_value=40.0),
            patch.dict("strategies.rsi_reversion.SCFG", {"profit_floor_pct": 0.05}),
        ):
            should_exit, _ = RSIReversionStrategy().should_exit("AAPL", entry_price=100.0)

        self.assertFalse(should_exit)

    def test_should_exit_triggers_when_price_clears_profit_floor(self):
        # With profit_floor_pct=0.01: effective_target = max(100.2, 101) = 101
        # price=104 ≥ 101 → should exit
        bars = [SimpleNamespace(close=100.0, volume=1000.0) for _ in range(24)]
        bars.append(SimpleNamespace(close=104.0, volume=1000.0))

        with (
            patch("strategies.rsi_reversion.ac.get_stock_bars", return_value={"AAPL": bars}),
            patch("strategies.rsi_reversion._calc_rsi", return_value=40.0),
            patch.dict("strategies.rsi_reversion.SCFG", {"profit_floor_pct": 0.01}),
        ):
            should_exit, reason = RSIReversionStrategy().should_exit("AAPL", entry_price=100.0)

        self.assertTrue(should_exit)
        self.assertIn("Mean target reached", reason)


if __name__ == "__main__":
    unittest.main()
