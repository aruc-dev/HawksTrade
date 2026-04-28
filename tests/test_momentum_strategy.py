import unittest
from types import SimpleNamespace
from unittest.mock import patch
from strategies.momentum import (
    MomentumStrategy,
    _calc_atr,
)


def _bar(close, high=None, low=None, volume=2000):
    return SimpleNamespace(
        close=float(close),
        high=float(high if high is not None else close * 1.01),
        low=float(low if low is not None else close * 0.99),
        volume=float(volume),
        timestamp="2026-04-23T00:00:00+00:00",
    )


class MomentumStrategyTests(unittest.TestCase):

    # ── Fallback fetching ─────────────────────────────────────────────────────

    def test_scan_falls_back_to_single_symbol_fetch_when_batch_response_is_sparse(self):
        prices = [100.0] * 100 + [110.0]
        bars = [_bar(p, volume=1000) for p in prices[:-1]] + [_bar(prices[-1], volume=2000)]
        batch_bars = {"AAPL": bars}
        jpm_bars   = {"JPM": bars}

        def _get_stock_bars(symbols, timeframe="1Day", limit=60):
            if symbols == ["SPY"]:
                return {"SPY": [_bar(100) for _ in range(30)]}
            if set(symbols) == {"AAPL", "JPM"}:
                return batch_bars
            if symbols == ["JPM"]:
                return jpm_bars
            raise AssertionError(f"Unexpected symbols: {symbols}")

        with (
            patch("strategies.momentum.ac.get_stock_bars", side_effect=_get_stock_bars),
            patch("strategies.momentum.rm.market_regime_ok", return_value=True),
            patch("strategies.momentum.rm.market_breadth_pct", return_value=0.6),
            patch("strategies.momentum.ac.get_portfolio_value", return_value=10000.0),
            patch("strategies.momentum.get_sector", side_effect=lambda x: f"Sector_{x}"),
            patch("strategies.momentum.log.warning") as warning,
        ):
            with patch.dict("strategies.momentum.SCFG", {"top_n": 5, "enabled": True, "min_momentum_pct": 0.01}):
                signals = MomentumStrategy().scan(["AAPL", "JPM"])

        self.assertEqual({signal["symbol"] for signal in signals}, {"AAPL", "JPM"})
        warning.assert_not_called()

    def test_scan_skips_missing_symbol_without_warning_when_fallback_is_also_missing(self):
        prices = [100.0] * 100 + [110.0]
        bars = [_bar(p, volume=1000) for p in prices[:-1]] + [_bar(prices[-1], volume=2000)]
        batch_bars = {"AAPL": bars}

        def _get_stock_bars(symbols, timeframe="1Day", limit=60):
            if symbols == ["SPY"]:
                return {"SPY": [_bar(100) for _ in range(30)]}
            if set(symbols) == {"AAPL", "JPM"}:
                return batch_bars
            if symbols == ["JPM"]:
                return {}
            raise AssertionError(f"Unexpected symbols: {symbols}")

        with (
            patch("strategies.momentum.ac.get_stock_bars", side_effect=_get_stock_bars),
            patch("strategies.momentum.rm.market_regime_ok", return_value=True),
            patch("strategies.momentum.rm.market_breadth_pct", return_value=0.6),
            patch("strategies.momentum.ac.get_portfolio_value", return_value=10000.0),
            patch("strategies.momentum.get_sector", side_effect=lambda x: f"Sector_{x}"),
            patch("strategies.momentum.log.warning") as warning,
        ):
            with patch.dict("strategies.momentum.SCFG", {"top_n": 5, "enabled": True, "min_momentum_pct": 0.01}):
                signals = MomentumStrategy().scan(["AAPL", "JPM"])

        self.assertEqual([signal["symbol"] for signal in signals], ["AAPL"])
        warning.assert_not_called()

    # ── Phase 1: ATR helpers ──────────────────────────────────────────────────

    def test_calc_atr_positive_for_normal_bars(self):
        bars = [_bar(close=100.0, high=105.0, low=95.0) for _ in range(21)]
        atr = _calc_atr(bars, period=14)
        self.assertGreater(atr, 0)

    def test_calc_atr_wider_bars_give_larger_atr(self):
        wide   = [_bar(100, high=110, low=90)  for _ in range(21)]
        narrow = [_bar(100, high=101, low=99)  for _ in range(21)]
        self.assertGreater(_calc_atr(wide, 14), _calc_atr(narrow, 14))

    def test_calc_atr_returns_zero_for_insufficient_bars(self):
        bars = [_bar(100) for _ in range(1)]
        self.assertEqual(_calc_atr(bars, period=14), 0.0)

    def test_calc_atr_handles_missing_high_low_gracefully(self):
        # Bars without high/low (only close) → ATR returns 0.0
        bars = [SimpleNamespace(close=100.0, volume=1000) for _ in range(21)]
        atr = _calc_atr(bars, period=14)
        self.assertEqual(atr, 0.0)

    # ── Enhancements (Smoothed, Alpha, Volume) ────────────────────────────────

    def test_momentum_smoothed_lookback(self):
        prices = [100.0] * 120 + [105.0, 115.0]
        bars = [_bar(p, volume=1000) for p in prices[:-1]] + [_bar(prices[-1], volume=2000)]
        bars_resp = {"AAPL": bars}
        
        with (
            patch("strategies.momentum.ac.get_stock_bars", return_value=bars_resp),
            patch("strategies.momentum.rm.market_regime_ok", return_value=True),
            patch("strategies.momentum.rm.market_breadth_pct", return_value=0.6),
            patch("strategies.momentum.ac.get_portfolio_value", return_value=10000.0),
            patch("strategies.momentum.get_sector", return_value="Tech"),
        ):
            with patch.dict("strategies.momentum.SCFG", {"min_momentum_pct": 0.05, "enabled": True}):
                signals = MomentumStrategy().scan(["AAPL"])
            
        self.assertEqual(len(signals), 1)
        self.assertAlmostEqual(signals[0]["momentum_score"], 0.10, places=4)

    def test_momentum_alpha_ranking(self):
        spy_prices = [100.0] * 100 + [105.0, 105.0]
        a_prices = [100.0] * 100 + [110.0, 110.0]
        b_prices = [100.0] * 100 + [112.0, 112.0]
        
        bars_a = [_bar(p, volume=1000) for p in a_prices[:-1]] + [_bar(a_prices[-1], volume=2000)]
        bars_b = [_bar(p, volume=1000) for p in b_prices[:-1]] + [_bar(b_prices[-1], volume=2000)]
        
        bars_resp = {"AAPL": bars_a, "MSFT": bars_b}
        regime_bars = {"SPY": [_bar(p) for p in spy_prices]}

        with (
            patch("strategies.momentum.ac.get_stock_bars", return_value=bars_resp),
            patch("strategies.momentum.rm.market_regime_ok", return_value=True),
            patch("strategies.momentum.rm.market_breadth_pct", return_value=0.6),
            patch("strategies.momentum.ac.get_portfolio_value", return_value=10000.0),
            patch("strategies.momentum.get_sector", side_effect=lambda x: f"Sector_{x}"),
        ):
            with patch.dict("strategies.momentum.SCFG", {"top_n": 1, "enabled": True, "min_momentum_pct": 0.01}):
                signals = MomentumStrategy().scan(["AAPL", "MSFT"], regime_bars=regime_bars)
                
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["symbol"], "MSFT")

    def test_momentum_volume_confirmation_gate(self):
        prices = [100.0] * 100 + [110.0, 110.0]
        bars = [_bar(p, volume=1000) for p in prices[:-1]] + [_bar(prices[-1], volume=1100)]
        bars_resp = {"AAPL": bars}
        
        with (
            patch("strategies.momentum.ac.get_stock_bars", return_value=bars_resp),
            patch("strategies.momentum.rm.market_regime_ok", return_value=True),
            patch("strategies.momentum.rm.market_breadth_pct", return_value=0.6),
            patch("strategies.momentum.ac.get_portfolio_value", return_value=10000.0),
        ):
            signals = MomentumStrategy().scan(["AAPL"])
            
        self.assertEqual(len(signals), 0)

    # ── Original Logic Tests ──────────────────────────────────────────────────

    def test_scan_includes_atr_stop_price_in_signal(self):
        prices = list(range(90, 115)) 
        bars = [_bar(p, high=p * 1.02, low=p * 0.98, volume=1000) for p in prices[:-1]] + [_bar(prices[-1], volume=2000)]
        bars_resp = {"AAPL": bars}

        with (
            patch("strategies.momentum.ac.get_stock_bars", return_value=bars_resp),
            patch("strategies.momentum.rm.market_regime_ok", return_value=True),
            patch("strategies.momentum.rm.market_breadth_pct", return_value=0.6),
            patch("strategies.momentum.ac.get_portfolio_value", return_value=10000.0),
            patch("strategies.momentum.get_sector", return_value="Tech"),
        ):
            with patch.dict("strategies.momentum.SCFG", {"enabled": True, "min_momentum_pct": 0.01}):
                signals = MomentumStrategy().scan(["AAPL"])

        self.assertTrue(len(signals) > 0)
        for sig in signals:
            self.assertIn("atr_stop_price", sig)

    def test_scan_includes_atr_risk_qty_in_signal(self):
        prices = list(range(90, 115))
        bars = [_bar(p, high=p * 1.02, low=p * 0.98, volume=1000) for p in prices[:-1]] + [_bar(prices[-1], volume=2000)]
        bars_resp = {"AAPL": bars}

        with (
            patch("strategies.momentum.ac.get_stock_bars", return_value=bars_resp),
            patch("strategies.momentum.rm.market_regime_ok", return_value=True),
            patch("strategies.momentum.rm.market_breadth_pct", return_value=0.6),
            patch("strategies.momentum.ac.get_portfolio_value", return_value=10000.0),
            patch("strategies.momentum.get_sector", return_value="Tech"),
        ):
            with patch.dict("strategies.momentum.SCFG", {"enabled": True, "min_momentum_pct": 0.01}):
                signals = MomentumStrategy().scan(["AAPL"])

        self.assertTrue(len(signals) > 0)
        for sig in signals:
            self.assertIn("atr_risk_qty", sig)
            self.assertGreater(sig["atr_risk_qty"], 0)

    def test_scan_enforces_sector_neutrality(self):
        prices = [100.0] * 100 + [120.0, 120.0]
        bars_high = [_bar(p, volume=1000) for p in prices[:-1]] + [_bar(prices[-1], volume=2000)]
        
        bars_resp = {
            "AAPL": bars_high, 
            "NVDA": bars_high, 
            "JPM":  bars_high, 
        }

        # Mock get_sector to put AAPL/NVDA in same sector, JPM in other
        def mock_sector(symbol):
            if symbol in ["AAPL", "NVDA"]: return "Technology"
            return "Financials"

        with (
            patch("strategies.momentum.ac.get_stock_bars", return_value=bars_resp),
            patch("strategies.momentum.rm.market_regime_ok", return_value=True),
            patch("strategies.momentum.rm.market_breadth_pct", return_value=0.6),
            patch("strategies.momentum.ac.get_portfolio_value", return_value=10000.0),
            patch("strategies.momentum.get_sector", side_effect=mock_sector),
        ):
            with patch.dict("strategies.momentum.SCFG", {"max_positions_per_sector": 1, "top_n": 5, "enabled": True}):
                signals = MomentumStrategy().scan(["AAPL", "NVDA", "JPM"])

        symbols = [sig["symbol"] for sig in signals]
        self.assertEqual(len(symbols), 2)
        self.assertIn("JPM", symbols)
        self.assertTrue(("AAPL" in symbols) ^ ("NVDA" in symbols))

    def test_scan_skips_signal_when_atr_risk_qty_is_below_notional_minimum(self):
        # 120 bars at 1000.
        # ATR calculation logic:
        # high=1500, low=500 -> TR = 1000.
        bars = [_bar(1000.0, high=1500.0, low=500.0, volume=1000) for _ in range(120)] + [_bar(1000.0, volume=2000)]
        bars_resp = {"AAPL": bars}

        with (
            patch("strategies.momentum.ac.get_stock_bars", return_value=bars_resp),
            patch("strategies.momentum.rm.market_regime_ok", return_value=True),
            patch("strategies.momentum.rm.market_breadth_pct", return_value=0.6),
            patch("strategies.momentum.ac.get_portfolio_value", return_value=1000.0), 
            patch("strategies.momentum.get_sector", return_value="Tech"),
            patch("strategies.momentum.log.info") as log_info,
        ):
            # top_n=5, enabled=True, momentum 0% -> need to set low threshold
            with patch.dict("strategies.momentum.SCFG", {"risk_per_trade_pct": 0.01, "enabled": True, "min_momentum_pct": -1.0}):
                signals = MomentumStrategy().scan(["AAPL"])

        self.assertEqual(len(signals), 0)
        skip_log_found = any("is below min" in str(args[0]) for args, kwargs in log_info.call_args_list)
        self.assertTrue(skip_log_found)

    def test_scan_full_signals_in_green_regime(self):
        prices = [100.0] * 100 + [110.0, 110.0]
        bars = [_bar(p, volume=1000) for p in prices[:-1]] + [_bar(prices[-1], volume=2000)]
        bars_resp = {"AAPL": bars, "MSFT": bars, "GOOG": bars}

        with (
            patch("strategies.momentum.ac.get_stock_bars", return_value=bars_resp),
            patch("strategies.momentum.rm.market_regime_ok", return_value=True),
            patch("strategies.momentum.rm.market_breadth_pct", return_value=0.8), # Green
            patch("strategies.momentum.ac.get_portfolio_value", return_value=100000.0),
            patch("strategies.momentum.get_sector", side_effect=lambda x: f"Sector_{x}"),
        ):
            with patch.dict("strategies.momentum.SCFG", {"top_n": 3, "enabled": True, "min_momentum_pct": 0.01}):
                signals = MomentumStrategy().scan(["AAPL", "MSFT", "GOOG"])

        self.assertEqual(len(signals), 3)

    # ── Yellow regime mid-band fix ────────────────────────────────────────────

    def test_scan_yellow_regime_mid_band_caps_positions(self):
        # breadth=0.45 sits between red(<0.25) and green(>=0.50) — the 40–50% Yellow band.
        # Before fix: this band got full top_n (Green behaviour).
        # After fix: yellow_max_positions cap applies.
        prices = [100.0] * 100 + [110.0, 110.0]
        bars = [_bar(p, volume=1000) for p in prices[:-1]] + [_bar(prices[-1], volume=2000)]
        bars_resp = {"AAPL": bars, "MSFT": bars, "GOOG": bars}

        with (
            patch("strategies.momentum.ac.get_stock_bars", return_value=bars_resp),
            patch("strategies.momentum.rm.market_regime_ok", return_value=True),
            patch("strategies.momentum.rm.market_breadth_pct", return_value=0.45),
            patch("strategies.momentum.ac.get_portfolio_value", return_value=100000.0),
            patch("strategies.momentum.get_sector", side_effect=lambda x: f"Sector_{x}"),
        ):
            with patch.dict("strategies.momentum.SCFG", {
                "top_n": 3,
                "enabled": True,
                "min_momentum_pct": 0.01,
                "yellow_max_positions": 2,
                "breadth_green_threshold": 0.50,
                "breadth_red_threshold": 0.25,
            }):
                signals = MomentumStrategy().scan(["AAPL", "MSFT", "GOOG"])

        self.assertLessEqual(len(signals), 2, "Yellow 40-50% breadth must be capped at yellow_max_positions")

    def test_scan_volume_spike_ratio_is_config_driven(self):
        # volume_spike_ratio=2.0 in config; last bar volume=1500 (1.5x avg=1000)
        # 1.5 < 2.0 → skipped; if config is ignored and 1.2 used, it would pass
        prices = [100.0] * 100 + [110.0, 110.0]
        bars = [_bar(p, volume=1000) for p in prices[:-1]] + [_bar(prices[-1], volume=1500)]
        bars_resp = {"AAPL": bars}

        with (
            patch("strategies.momentum.ac.get_stock_bars", return_value=bars_resp),
            patch("strategies.momentum.rm.market_regime_ok", return_value=True),
            patch("strategies.momentum.rm.market_breadth_pct", return_value=0.6),
            patch("strategies.momentum.ac.get_portfolio_value", return_value=100000.0),
            patch("strategies.momentum.get_sector", return_value="Tech"),
        ):
            with patch.dict("strategies.momentum.SCFG", {
                "enabled": True,
                "min_momentum_pct": 0.01,
                "volume_spike_ratio": 2.0,
            }):
                signals = MomentumStrategy().scan(["AAPL"])

        self.assertEqual(len(signals), 0, "volume_spike_ratio must be read from config, not hardcoded")


if __name__ == "__main__":
    unittest.main()
