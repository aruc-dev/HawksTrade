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
            patch.dict("strategies.rsi_reversion.SCFG", {"enabled": True, "market_regime_mode": "normal"}),
        ):
            signals = RSIReversionStrategy().scan(["AAPL"], allow_regime_warmup=True)

        self.assertEqual(len(signals), 1)
        self.assertIn("atr_risk_qty", signals[0])
        # Default atr_multiplier=0.8: atr_stop = 96 - 0.8*2 = 94.4;
        # risk_per_share = 1.6; risk_dollars = 100 -> qty = 62.5.
        self.assertAlmostEqual(signals[0]["atr_risk_qty"], 62.5, places=4)

    def test_bear_or_chop_mode_stands_down_in_bull_regime(self):
        bars = _make_bars()

        def _get_stock_bars(symbols, timeframe="1Day", limit=210):
            if symbols == ["SPY", "QQQ"]:
                return {
                    "SPY": [_bar(100) for _ in range(30)],
                    "QQQ": [_bar(100) for _ in range(30)],
                }
            return {"AAPL": bars}

        with (
            patch("strategies.rsi_reversion.ac.get_stock_bars", side_effect=_get_stock_bars),
            patch("strategies.rsi_reversion.ac.get_portfolio_value") as get_portfolio_value,
            patch("strategies.rsi_reversion.rm.market_regime_ok", return_value=True) as market_regime_ok,
            patch("strategies.rsi_reversion._calc_rsi", return_value=25.0),
            patch("strategies.rsi_reversion._bollinger_pct_b", return_value=0.10),
            patch("strategies.rsi_reversion._calc_atr", return_value=2.0),
            patch.dict(
                "strategies.rsi_reversion.SCFG",
                {"enabled": True, "market_regime_mode": "bear_or_chop_only"},
            ),
        ):
            signals = RSIReversionStrategy().scan(["AAPL"], allow_regime_warmup=True)

        self.assertEqual(signals, [])
        get_portfolio_value.assert_not_called()
        self.assertEqual(set(market_regime_ok.call_args.kwargs["bars_data"]), {"SPY", "QQQ"})

    def test_bear_or_chop_mode_allows_non_bull_regime(self):
        bars = _make_bars()

        def _get_stock_bars(symbols, timeframe="1Day", limit=210):
            if symbols == ["SPY", "QQQ"]:
                return {
                    "SPY": [_bar(100) for _ in range(30)],
                    "QQQ": [_bar(100) for _ in range(30)],
                }
            return {"AAPL": bars}

        with (
            patch("strategies.rsi_reversion.ac.get_stock_bars", side_effect=_get_stock_bars),
            patch("strategies.rsi_reversion.ac.get_portfolio_value", return_value=10000.0),
            patch("strategies.rsi_reversion.rm.market_regime_ok", return_value=False),
            patch("strategies.rsi_reversion._calc_rsi", return_value=25.0),
            patch("strategies.rsi_reversion._bollinger_pct_b", return_value=0.10),
            patch("strategies.rsi_reversion._calc_atr", return_value=2.0),
            patch.dict(
                "strategies.rsi_reversion.SCFG",
                {"enabled": True, "market_regime_mode": "bear_or_chop_only"},
            ),
        ):
            signals = RSIReversionStrategy().scan(["AAPL"], allow_regime_warmup=True)

        self.assertEqual(len(signals), 1)

    def test_bear_or_chop_mode_stands_down_when_regime_data_is_insufficient(self):
        bars = _make_bars()

        def _get_stock_bars(symbols, timeframe="1Day", limit=210):
            if symbols == ["SPY", "QQQ"]:
                return {
                    "SPY": [_bar(100) for _ in range(30)],
                    "QQQ": [_bar(100) for _ in range(30)],
                }
            return {"AAPL": bars}

        with (
            patch("strategies.rsi_reversion.ac.get_stock_bars", side_effect=_get_stock_bars),
            patch("strategies.rsi_reversion.ac.get_portfolio_value") as get_portfolio_value,
            patch("strategies.rsi_reversion.rm.market_regime_ok") as market_regime_ok,
            patch("strategies.rsi_reversion._calc_rsi", return_value=25.0),
            patch("strategies.rsi_reversion._bollinger_pct_b", return_value=0.10),
            patch("strategies.rsi_reversion._calc_atr", return_value=2.0),
            patch.dict(
                "strategies.rsi_reversion.SCFG",
                {"enabled": True, "market_regime_mode": "bear_or_chop_only"},
            ),
        ):
            signals = RSIReversionStrategy().scan(["AAPL"])

        self.assertEqual(signals, [])
        market_regime_ok.assert_not_called()
        get_portfolio_value.assert_not_called()

    def test_scan_skips_signal_when_atr_risk_qty_below_notional_minimum(self):
        # price≈96, atr=20 and 6% stop cap → risk_per_share≈5.76,
        # risk_dollars=1 → qty≈0.17; notional remains below min_trade_value=100.
        bars = _make_bars()

        def _get_stock_bars(symbols, timeframe="1Day", limit=210):
            if symbols == ["SPY"]:
                return {"SPY": [_bar(100) for _ in range(30)]}
            return {"AAPL": bars}

        with (
            patch("strategies.rsi_reversion.ac.get_stock_bars", side_effect=_get_stock_bars),
            patch("strategies.rsi_reversion.ac.get_portfolio_value", return_value=100.0),
            patch("strategies.rsi_reversion._calc_rsi", return_value=25.0),
            patch("strategies.rsi_reversion._bollinger_pct_b", return_value=0.10),
            patch("strategies.rsi_reversion._calc_atr", return_value=20.0),
            patch.dict("strategies.rsi_reversion.SCFG", {"enabled": True, "market_regime_mode": "normal"}),
        ):
            signals = RSIReversionStrategy().scan(["AAPL"], allow_regime_warmup=True)

        self.assertEqual(len(signals), 0)

    def test_scan_caps_rsi_atr_stop_distance(self):
        bars = _make_bars()

        def _get_stock_bars(symbols, timeframe="1Day", limit=210):
            if symbols == ["SPY"]:
                return {"SPY": [_bar(100) for _ in range(30)]}
            return {"AAPL": bars}

        with (
            patch("strategies.rsi_reversion.ac.get_stock_bars", side_effect=_get_stock_bars),
            patch("strategies.rsi_reversion.ac.get_portfolio_value", return_value=10000.0),
            patch("strategies.rsi_reversion._calc_rsi", return_value=25.0),
            patch("strategies.rsi_reversion._bollinger_pct_b", return_value=0.10),
            patch("strategies.rsi_reversion._calc_atr", return_value=20.0),
            patch.dict(
                "strategies.rsi_reversion.SCFG",
                {
                    "enabled": True,
                    "market_regime_mode": "normal",
                    "max_entry_atr_pct": 0,
                    "max_stop_loss_pct": 0.06,
                },
            ),
        ):
            signals = RSIReversionStrategy().scan(["AAPL"], allow_regime_warmup=True)

        self.assertEqual(len(signals), 1)
        self.assertAlmostEqual(signals[0]["atr_stop_price"], 90.24, places=2)

    def test_scan_blocks_entry_when_atr_pct_exceeds_strategy_ceiling(self):
        bars = _make_bars()

        def _get_stock_bars(symbols, timeframe="1Day", limit=210):
            if symbols == ["SPY"]:
                return {"SPY": [_bar(100) for _ in range(30)]}
            return {"AAPL": bars}

        with (
            patch("strategies.rsi_reversion.ac.get_stock_bars", side_effect=_get_stock_bars),
            patch("strategies.rsi_reversion.ac.get_portfolio_value") as get_portfolio_value,
            patch("strategies.rsi_reversion._calc_rsi", return_value=25.0),
            patch("strategies.rsi_reversion._bollinger_pct_b", return_value=0.10),
            patch("strategies.rsi_reversion._calc_atr", return_value=7.0),
            patch.dict(
                "strategies.rsi_reversion.SCFG",
                {"enabled": True, "market_regime_mode": "normal", "max_entry_atr_pct": 0.06},
            ),
        ):
            signals = RSIReversionStrategy().scan(["AAPL"], allow_regime_warmup=True)

        self.assertEqual(signals, [])
        get_portfolio_value.assert_called_once()

    def test_scan_blocks_signals_when_portfolio_value_unavailable_for_atr_sizing(self):
        bars = _make_bars()

        def _get_stock_bars(symbols, timeframe="1Day", limit=210):
            if symbols == ["SPY"]:
                return {"SPY": [_bar(100) for _ in range(30)]}
            return {"AAPL": bars}

        with (
            patch("strategies.rsi_reversion.ac.get_stock_bars", side_effect=_get_stock_bars),
            patch("strategies.rsi_reversion.ac.get_portfolio_value", side_effect=RuntimeError("account unavailable")),
            patch("strategies.rsi_reversion._calc_rsi", return_value=25.0),
            patch("strategies.rsi_reversion._bollinger_pct_b", return_value=0.10),
            patch("strategies.rsi_reversion._calc_atr", return_value=2.0),
            patch.dict("strategies.rsi_reversion.SCFG", {"enabled": True, "market_regime_mode": "normal"}),
        ):
            signals = RSIReversionStrategy().scan(["AAPL"], allow_regime_warmup=True)

        self.assertEqual(signals, [])

    def test_scan_uses_configured_volume_spike_ratio(self):
        bars = _make_bars(last_vol=1400)

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
            patch.dict(
                "strategies.rsi_reversion.SCFG",
                {"enabled": True, "market_regime_mode": "normal", "volume_spike_ratio": 1.3},
            ),
        ):
            signals = RSIReversionStrategy().scan(["AAPL"], allow_regime_warmup=True)

        self.assertEqual(len(signals), 1)

        with (
            patch("strategies.rsi_reversion.ac.get_stock_bars", side_effect=_get_stock_bars),
            patch("strategies.rsi_reversion.ac.get_portfolio_value", return_value=10000.0),
            patch("strategies.rsi_reversion._calc_rsi", return_value=25.0),
            patch("strategies.rsi_reversion._bollinger_pct_b", return_value=0.10),
            patch("strategies.rsi_reversion._calc_atr", return_value=2.0),
            patch.dict(
                "strategies.rsi_reversion.SCFG",
                {"enabled": True, "market_regime_mode": "normal", "volume_spike_ratio": 1.5},
            ),
        ):
            signals = RSIReversionStrategy().scan(["AAPL"], allow_regime_warmup=True)

        self.assertEqual(signals, [])

    def test_scan_requires_configured_recovery_bars(self):
        failing_bars = [_bar(95.0, volume=1000) for _ in range(208)]
        failing_bars.extend([_bar(95.0, volume=1000), _bar(96.0, volume=1500)])

        passing_bars = [_bar(95.0, volume=1000) for _ in range(207)]
        passing_bars.extend([_bar(94.0, volume=1000), _bar(95.0, volume=1000), _bar(96.0, volume=1500)])

        def _get_stock_bars(symbols, timeframe="1Day", limit=210):
            if symbols == ["SPY"]:
                return {"SPY": [_bar(100) for _ in range(30)]}
            return {"AAPL": failing_bars}

        with (
            patch("strategies.rsi_reversion.ac.get_stock_bars", side_effect=_get_stock_bars),
            patch("strategies.rsi_reversion.ac.get_portfolio_value", return_value=10000.0),
            patch("strategies.rsi_reversion._calc_rsi", return_value=25.0),
            patch("strategies.rsi_reversion._bollinger_pct_b", return_value=0.10),
            patch("strategies.rsi_reversion._calc_atr", return_value=2.0),
            patch.dict(
                "strategies.rsi_reversion.SCFG",
                {"enabled": True, "market_regime_mode": "normal", "min_recovery_bars": 2},
            ),
        ):
            signals = RSIReversionStrategy().scan(["AAPL"], allow_regime_warmup=True)

        self.assertEqual(signals, [])

        def _get_passing_stock_bars(symbols, timeframe="1Day", limit=210):
            if symbols == ["SPY"]:
                return {"SPY": [_bar(100) for _ in range(30)]}
            return {"AAPL": passing_bars}

        with (
            patch("strategies.rsi_reversion.ac.get_stock_bars", side_effect=_get_passing_stock_bars),
            patch("strategies.rsi_reversion.ac.get_portfolio_value", return_value=10000.0),
            patch("strategies.rsi_reversion._calc_rsi", return_value=25.0),
            patch("strategies.rsi_reversion._bollinger_pct_b", return_value=0.10),
            patch("strategies.rsi_reversion._calc_atr", return_value=2.0),
            patch.dict(
                "strategies.rsi_reversion.SCFG",
                {"enabled": True, "market_regime_mode": "normal", "min_recovery_bars": 2},
            ),
        ):
            signals = RSIReversionStrategy().scan(["AAPL"], allow_regime_warmup=True)

        self.assertEqual(len(signals), 1)

    def test_scan_blocks_weak_close_location_when_configured(self):
        bars = [_bar(95.0, volume=1000) for _ in range(208)]
        bars.extend([
            _bar(93.0, volume=1000),
            _bar(94.0, high=100.0, low=90.0, volume=1500),
        ])

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
            patch.dict(
                "strategies.rsi_reversion.SCFG",
                {"enabled": True, "market_regime_mode": "normal", "min_close_location": 0.60},
            ),
        ):
            signals = RSIReversionStrategy().scan(["AAPL"], allow_regime_warmup=True)

        self.assertEqual(signals, [])

    def test_scan_blocks_recent_waterfall_drawdown_when_configured(self):
        bars = [_bar(100.0, volume=1000) for _ in range(205)]
        bars.extend([
            _bar(104.0, volume=1000),
            _bar(102.0, volume=1000),
            _bar(99.0, volume=1000),
            _bar(95.0, volume=1000),
            _bar(96.0, volume=1500),
        ])

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
            patch.dict(
                "strategies.rsi_reversion.SCFG",
                {
                    "enabled": True,
                    "recent_drawdown_lookback_days": 5,
                    "max_recent_drawdown_pct": 0.05,
                },
            ),
        ):
            signals = RSIReversionStrategy().scan(["AAPL"], allow_regime_warmup=True)

        self.assertEqual(signals, [])

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
            patch.dict("strategies.rsi_reversion.SCFG", {"enabled": True, "market_regime_mode": "normal"}),
        ):
            signals = RSIReversionStrategy().scan(["AAPL"], allow_regime_warmup=True)

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
            patch.dict("strategies.rsi_reversion.SCFG", {"enabled": True, "market_regime_mode": "normal"}),
        ):
            signals = RSIReversionStrategy().scan(["AAPL"], allow_regime_warmup=True)

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
            patch.dict("strategies.rsi_reversion.SCFG", {"enabled": True, "market_regime_mode": "normal"}),
        ):
            RSIReversionStrategy().scan(["AAPL"], allow_regime_warmup=True)

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

    def test_should_exit_triggers_on_configured_max_loss(self):
        bars = [SimpleNamespace(close=100.0, volume=1000.0) for _ in range(24)]
        bars.append(SimpleNamespace(close=93.5, volume=1000.0))

        with (
            patch("strategies.rsi_reversion.ac.get_stock_bars", return_value={"AAPL": bars}),
            patch.dict("strategies.rsi_reversion.SCFG", {"max_loss_exit_pct": 0.06}),
        ):
            should_exit, reason = RSIReversionStrategy().should_exit("AAPL", entry_price=100.0)

        self.assertTrue(should_exit)
        self.assertIn("max-loss", reason)


if __name__ == "__main__":
    unittest.main()
