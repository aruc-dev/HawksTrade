import unittest
from types import SimpleNamespace
from unittest.mock import patch

from strategies.momentum import (
    MomentumStrategy,
    _calc_atr,
    _get_sector,
    _sector_filtered_top_n,
)


def _bar(close, high=None, low=None, volume=1000):
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
        # AAPL (Technology) + JPM (Financials) — different sectors so both pass filter
        batch_bars = {"AAPL": [_bar(price) for price in (100, 101, 102, 103, 104, 105, 110)]}
        jpm_bars   = {"JPM": [_bar(price) for price in (200, 201, 202, 203, 204, 205, 226)]}

        def _get_stock_bars(symbols, timeframe="1Day", limit=60):
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
            patch("strategies.momentum.log.warning") as warning,
        ):
            signals = MomentumStrategy().scan(["AAPL", "JPM"])

        self.assertEqual({signal["symbol"] for signal in signals}, {"AAPL", "JPM"})
        warning.assert_not_called()

    def test_scan_skips_missing_symbol_without_warning_when_fallback_is_also_missing(self):
        # AAPL (Technology) + JPM (Financials): JPM has no data anywhere
        batch_bars = {"AAPL": [_bar(price) for price in (100, 101, 102, 103, 104, 105, 110)]}

        def _get_stock_bars(symbols, timeframe="1Day", limit=60):
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
            patch("strategies.momentum.log.warning") as warning,
        ):
            signals = MomentumStrategy().scan(["AAPL", "JPM"])

        self.assertEqual([signal["symbol"] for signal in signals], ["AAPL"])
        warning.assert_not_called()

    # ── Phase 1: ATR helpers ──────────────────────────────────────────────────

    def test_calc_atr_positive_for_normal_bars(self):
        bars = [_bar(close=100.0, high=105.0, low=95.0) for _ in range(20)]
        atr = _calc_atr(bars, period=14)
        self.assertGreater(atr, 0)

    def test_calc_atr_wider_bars_give_larger_atr(self):
        wide   = [_bar(100, high=110, low=90)  for _ in range(20)]
        narrow = [_bar(100, high=101, low=99)  for _ in range(20)]
        self.assertGreater(_calc_atr(wide, 14), _calc_atr(narrow, 14))

    def test_calc_atr_returns_zero_for_insufficient_bars(self):
        bars = [_bar(100) for _ in range(1)]
        self.assertEqual(_calc_atr(bars, period=14), 0.0)

    def test_calc_atr_handles_missing_high_low_gracefully(self):
        # Bars without high/low (only close) → ATR returns 0.0
        bars = [SimpleNamespace(close=100.0, volume=1000) for _ in range(20)]
        atr = _calc_atr(bars, period=14)
        self.assertEqual(atr, 0.0)

    def test_scan_includes_atr_stop_price_in_signal(self):
        # 20 bars with high/low so ATR can be computed
        prices = list(range(90, 110)) + [115]
        bars = [_bar(p, high=p * 1.02, low=p * 0.98) for p in prices]
        bars_resp = {"AAPL": bars, "JPM": bars}

        with (
            patch("strategies.momentum.ac.get_stock_bars", return_value=bars_resp),
            patch("strategies.momentum.rm.market_regime_ok", return_value=True),
            patch("strategies.momentum.rm.market_breadth_pct", return_value=0.6),
            patch("strategies.momentum.ac.get_portfolio_value", return_value=10000.0),
        ):
            signals = MomentumStrategy().scan(["AAPL", "JPM"])

        self.assertTrue(len(signals) > 0)
        for sig in signals:
            self.assertIn("atr_stop_price", sig)
            self.assertLess(sig["atr_stop_price"], sig.get("price_now", 115))

    def test_scan_includes_atr_risk_qty_in_signal(self):
        prices = list(range(90, 110)) + [115]
        bars = [_bar(p, high=p * 1.02, low=p * 0.98) for p in prices]
        bars_resp = {"AAPL": bars}

        with (
            patch("strategies.momentum.ac.get_stock_bars", return_value=bars_resp),
            patch("strategies.momentum.rm.market_regime_ok", return_value=True),
            patch("strategies.momentum.rm.market_breadth_pct", return_value=0.6),
            patch("strategies.momentum.ac.get_portfolio_value", return_value=10000.0),
        ):
            signals = MomentumStrategy().scan(["AAPL"])

        self.assertTrue(len(signals) > 0)
        for sig in signals:
            self.assertIn("atr_risk_qty", sig)
            # 1% of $10k = $100 risk → qty = $100 / risk_per_share > 0
            self.assertGreater(sig["atr_risk_qty"], 0)

    def test_atr_stop_is_below_entry_price(self):
        # Stop must be below entry price by definition
        prices = list(range(90, 110)) + [115]
        bars = [_bar(p, high=p * 1.02, low=p * 0.98) for p in prices]
        bars_resp = {"AAPL": bars}

        with (
            patch("strategies.momentum.ac.get_stock_bars", return_value=bars_resp),
            patch("strategies.momentum.rm.market_regime_ok", return_value=True),
            patch("strategies.momentum.rm.market_breadth_pct", return_value=0.6),
            patch("strategies.momentum.ac.get_portfolio_value", return_value=10000.0),
        ):
            signals = MomentumStrategy().scan(["AAPL"])

        for sig in signals:
            if "atr_stop_price" in sig:
                current_price = float(bars[-1].close)
                self.assertLess(sig["atr_stop_price"], current_price)

    # ── Phase 2: Sector-neutral helpers ──────────────────────────────────────

    def test_get_sector_known_symbol(self):
        self.assertEqual(_get_sector("AAPL"), "Technology")
        self.assertEqual(_get_sector("JPM"), "Financials")
        self.assertEqual(_get_sector("XOM"), "Energy")

    def test_get_sector_unknown_returns_unique_pseudo_sector(self):
        sector = _get_sector("UNKNOWN_XYZ")
        self.assertIn("UNKNOWN_XYZ", sector)

    def test_sector_filter_blocks_second_tech_stock(self):
        scores = [
            {"symbol": "NVDA", "momentum": 0.20},
            {"symbol": "AAPL", "momentum": 0.15},  # also Technology
            {"symbol": "JPM",  "momentum": 0.10},
        ]
        result = _sector_filtered_top_n(scores, top_n=3, max_per_sector=1)
        symbols = [s["symbol"] for s in result]
        self.assertIn("NVDA", symbols)
        self.assertNotIn("AAPL", symbols)  # same sector as NVDA, blocked
        self.assertIn("JPM", symbols)

    def test_sector_filter_respects_top_n(self):
        scores = [
            {"symbol": "NVDA",  "momentum": 0.20},
            {"symbol": "JPM",   "momentum": 0.18},
            {"symbol": "XOM",   "momentum": 0.15},
            {"symbol": "ABBV",  "momentum": 0.12},
        ]
        result = _sector_filtered_top_n(scores, top_n=2, max_per_sector=1)
        self.assertEqual(len(result), 2)

    def test_sector_filter_allows_multiple_different_sectors(self):
        scores = [
            {"symbol": "NVDA",  "momentum": 0.20},  # Technology
            {"symbol": "JPM",   "momentum": 0.18},  # Financials
            {"symbol": "XOM",   "momentum": 0.15},  # Energy
        ]
        result = _sector_filtered_top_n(scores, top_n=3, max_per_sector=1)
        self.assertEqual(len(result), 3)

    def test_scan_enforces_sector_neutrality(self):
        # Ranks: NVDA > AAPL (both Tech) > JPM (Financials) — AAPL blocked
        prices_hi  = list(range(100, 120)) + [140]  # ~18% gain
        prices_mid = list(range(100, 120)) + [130]  # ~14% gain
        prices_lo  = list(range(100, 120)) + [120]  # ~5% gain  < min_momentum, filtered
        bars_resp = {
            "NVDA": [_bar(p) for p in prices_hi],
            "AAPL": [_bar(p) for p in prices_mid],
            "JPM":  [_bar(p) for p in prices_lo],
        }

        with (
            patch("strategies.momentum.ac.get_stock_bars", return_value=bars_resp),
            patch("strategies.momentum.rm.market_regime_ok", return_value=True),
            patch("strategies.momentum.rm.market_breadth_pct", return_value=0.6),
            patch("strategies.momentum.ac.get_portfolio_value", return_value=10000.0),
        ):
            signals = MomentumStrategy().scan(["NVDA", "AAPL", "JPM"])

        symbols = [s["symbol"] for s in signals]
        # NVDA top Tech: selected; AAPL also Tech: blocked; JPM momentum < 6%: filtered
        self.assertIn("NVDA", symbols)
        self.assertNotIn("AAPL", symbols)

    # ── Phase 3: Breadth regime guard ────────────────────────────────────────

    def test_scan_returns_empty_on_red_regime_low_breadth(self):
        # Breadth < 25% with SPY bull → Red
        bars_resp = {"AAPL": [_bar(p) for p in range(100, 107)]}
        with (
            patch("strategies.momentum.ac.get_stock_bars", return_value=bars_resp),
            patch("strategies.momentum.rm.market_regime_ok", return_value=True),
            patch("strategies.momentum.rm.market_breadth_pct", return_value=0.20),
            patch("strategies.momentum.ac.get_portfolio_value", return_value=10000.0),
        ):
            signals = MomentumStrategy().scan(["AAPL"])
        self.assertEqual(signals, [])

    def test_scan_returns_empty_on_red_regime_spy_bear(self):
        # SPY < SMA50 regardless of breadth → Red
        bars_resp = {"AAPL": [_bar(p) for p in range(100, 107)]}
        with (
            patch("strategies.momentum.ac.get_stock_bars", return_value=bars_resp),
            patch("strategies.momentum.rm.market_regime_ok", return_value=False),
            patch("strategies.momentum.rm.market_breadth_pct", return_value=0.60),
            patch("strategies.momentum.ac.get_portfolio_value", return_value=10000.0),
        ):
            signals = MomentumStrategy().scan(["AAPL"])
        self.assertEqual(signals, [])

    def test_scan_limits_signals_in_yellow_regime(self):
        # Yellow regime (breadth 30% < 40%) caps to yellow_max_positions
        prices = list(range(90, 110)) + [125]
        bars_resp = {sym: [_bar(p) for p in prices] for sym in ["NVDA", "JPM", "XOM", "ABBV"]}

        with (
            patch("strategies.momentum.ac.get_stock_bars", return_value=bars_resp),
            patch("strategies.momentum.rm.market_regime_ok", return_value=True),
            patch("strategies.momentum.rm.market_breadth_pct", return_value=0.30),  # Yellow
            patch("strategies.momentum.ac.get_portfolio_value", return_value=10000.0),
        ):
            signals = MomentumStrategy().scan(["NVDA", "JPM", "XOM", "ABBV"])

        # yellow_max_positions=3, top_n=3, so max 3 signals returned
        self.assertLessEqual(len(signals), 3)

    def test_scan_full_signals_in_green_regime(self):
        prices = list(range(90, 110)) + [125]
        bars_resp = {sym: [_bar(p) for p in prices] for sym in ["NVDA", "JPM", "XOM"]}

        with (
            patch("strategies.momentum.ac.get_stock_bars", return_value=bars_resp),
            patch("strategies.momentum.rm.market_regime_ok", return_value=True),
            patch("strategies.momentum.rm.market_breadth_pct", return_value=0.60),  # Green
            patch("strategies.momentum.ac.get_portfolio_value", return_value=10000.0),
        ):
            signals = MomentumStrategy().scan(["NVDA", "JPM", "XOM"])

        self.assertEqual(len(signals), 3)

    def test_scan_skips_signal_when_atr_risk_qty_is_below_notional_minimum(self):
        prices = list(range(100, 120)) + [125]
        bars = [_bar(p, high=p + 10, low=p - 10) for p in prices]
        bars_resp = {"AAPL": bars}

        mock_cfg = {
            "strategies": {
                "momentum": {
                    "enabled": True, "top_n": 3, "min_momentum_pct": 0.06,
                    "risk_per_trade_pct": 0.01, "atr_period": 14, "atr_multiplier": 2.0,
                    "max_positions_per_sector": 1, "breadth_green_threshold": 0.50,
                    "breadth_yellow_threshold": 0.40, "breadth_red_threshold": 0.25,
                    "yellow_max_positions": 3
                }
            },
            "trading": {
                "min_trade_value_usd": 1000.0
            }
        }

        with (
            patch("strategies.momentum.ac.get_stock_bars", return_value=bars_resp),
            patch("strategies.momentum.rm.market_regime_ok", return_value=True),
            patch("strategies.momentum.rm.market_breadth_pct", return_value=0.6),
            patch("strategies.momentum.ac.get_portfolio_value", return_value=10000.0),
            patch("strategies.momentum.CFG", mock_cfg),
            patch("strategies.momentum.log.info") as mock_log_info
        ):
            signals = MomentumStrategy().scan(["AAPL"])

        self.assertEqual(len(signals), 0)
        skip_log_found = any("below min $1000.0" in str(call.args[0]) for call in mock_log_info.call_args_list)
        self.assertTrue(skip_log_found)

    def test_scan_keeps_signal_when_atr_risk_qty_is_above_notional_minimum(self):
        prices = list(range(100, 120)) + [125]
        bars = [_bar(p, high=p + 1, low=p - 1) for p in prices]
        bars_resp = {"AAPL": bars}

        mock_cfg = {
            "strategies": {
                "momentum": {
                    "enabled": True, "top_n": 3, "min_momentum_pct": 0.06,
                    "risk_per_trade_pct": 0.01, "atr_period": 14, "atr_multiplier": 2.0,
                    "max_positions_per_sector": 1, "breadth_green_threshold": 0.50,
                    "breadth_yellow_threshold": 0.40, "breadth_red_threshold": 0.25,
                    "yellow_max_positions": 3
                }
            },
            "trading": {
                "min_trade_value_usd": 10.0
            }
        }

        with (
            patch("strategies.momentum.ac.get_stock_bars", return_value=bars_resp),
            patch("strategies.momentum.rm.market_regime_ok", return_value=True),
            patch("strategies.momentum.rm.market_breadth_pct", return_value=0.6),
            patch("strategies.momentum.ac.get_portfolio_value", return_value=10000.0),
            patch("strategies.momentum.CFG", mock_cfg),
        ):
            signals = MomentumStrategy().scan(["AAPL"])

        self.assertEqual(len(signals), 1)


if __name__ == "__main__":
    unittest.main()
