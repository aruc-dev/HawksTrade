"""
Tests for screener/universe_builder.py and core/alpaca_client.get_all_tradable_assets
"""

import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np
from datetime import datetime, timezone

from screener.universe_builder import UniverseBuilder


def _make_bars_df(n=25, close=100.0, volume=1_000_000, atr_pct=0.025, seed=0):
    """
    Create a mock DataFrame of daily bars for testing.
    atr_pct controls the spread between high/low relative to close.
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range(end="2025-06-01", periods=n, freq="D", tz="UTC")
    spread = close * atr_pct
    data = {
        "close":  [close + rng.uniform(-0.5, 0.5) for _ in range(n)],
        "open":   [close + rng.uniform(-0.5, 0.5) for _ in range(n)],
        "high":   [close + spread + rng.uniform(0, 0.5) for _ in range(n)],
        "low":    [close - spread - rng.uniform(0, 0.5) for _ in range(n)],
        "volume": [volume + rng.integers(-10000, 10000) for _ in range(n)],
    }
    return pd.DataFrame(data, index=dates)


def _base_config():
    return {
        "screener": {
            "enabled": True,
            "min_adv_shares": 500_000,
            "min_adv_dollars": 5_000_000,
            "min_price": 5.0,
            "max_price": 2000.0,
            "min_atr_pct": 0.01,
            "max_atr_pct": 0.08,
            "max_universe": 100,
        },
        "stocks": {
            "scan_universe": ["SPY", "QQQ"],
        },
    }


class TestComputeScore(unittest.TestCase):
    """Tests for _compute_score (individual stock scoring)."""

    def test_compute_score_passes_good_stock(self):
        """ADV >500k, price $50-200, ATR 2-4% should pass."""
        cfg = _base_config()
        builder = UniverseBuilder(cfg)
        df = _make_bars_df(n=25, close=150.0, volume=800_000, atr_pct=0.025)
        score = builder._compute_score(df, "AAPL")
        self.assertIsNotNone(score)
        self.assertEqual(score["symbol"], "AAPL")
        self.assertGreater(score["adv_dollars"], 0)

    def test_compute_score_filters_penny_stock(self):
        """Price $2 should be filtered out (below min_price $5)."""
        cfg = _base_config()
        builder = UniverseBuilder(cfg)
        df = _make_bars_df(n=25, close=2.0, volume=800_000, atr_pct=0.03)
        score = builder._compute_score(df, "PENNY")
        self.assertIsNone(score)

    def test_compute_score_filters_low_volume(self):
        """ADV 100k shares should be filtered out (below 500k)."""
        cfg = _base_config()
        builder = UniverseBuilder(cfg)
        df = _make_bars_df(n=25, close=100.0, volume=100_000, atr_pct=0.025)
        score = builder._compute_score(df, "LOWVOL")
        self.assertIsNone(score)

    def test_compute_score_filters_low_atr(self):
        """ATR% = 0.3% (flat stock) should be filtered out (below 1%)."""
        cfg = _base_config()
        builder = UniverseBuilder(cfg)
        # Create a truly flat stock — high/low very close to close
        n = 25
        dates = pd.date_range(end="2025-06-01", periods=n, freq="D", tz="UTC")
        close_val = 100.0
        df = pd.DataFrame({
            "close":  [close_val] * n,
            "open":   [close_val] * n,
            "high":   [close_val + 0.05] * n,  # only $0.05 range = 0.05% ATR
            "low":    [close_val - 0.05] * n,
            "volume": [800_000.0] * n,
        }, index=dates)
        score = builder._compute_score(df, "FLAT")
        self.assertIsNone(score)

    def test_compute_score_filters_high_atr(self):
        """ATR% = 15% (meme stock) should be filtered out (above 8%)."""
        cfg = _base_config()
        builder = UniverseBuilder(cfg)
        df = _make_bars_df(n=25, close=100.0, volume=800_000, atr_pct=0.15)
        score = builder._compute_score(df, "MEME")
        self.assertIsNone(score)


class TestScreenFromBars(unittest.TestCase):
    """Tests for backtest-mode screening."""

    def test_screen_from_bars_returns_sorted_by_adv(self):
        """3 stocks with different ADV should be sorted highest first."""
        cfg = _base_config()
        builder = UniverseBuilder(cfg)
        as_of = datetime(2025, 6, 1, tzinfo=timezone.utc)

        bars_data = {
            "LOW":  _make_bars_df(n=25, close=50.0,  volume=600_000,   atr_pct=0.025),
            "MID":  _make_bars_df(n=25, close=100.0, volume=800_000,   atr_pct=0.025),
            "HIGH": _make_bars_df(n=25, close=200.0, volume=1_000_000, atr_pct=0.025),
        }

        builder.preload_historical_bars(bars_data)
        result = builder._screen_from_bars(bars_data, as_of)

        # All three should qualify
        self.assertTrue(len(result) >= 2)
        # HIGH has highest dollar volume (200 * 1M = $200M/day) — should be first
        self.assertEqual(result[0], "HIGH")

    def test_screen_from_bars_merges_legacy(self):
        """Even if no dynamic symbols qualify, legacy universe is always in result."""
        cfg = _base_config()
        builder = UniverseBuilder(cfg)
        as_of = datetime(2025, 6, 1, tzinfo=timezone.utc)

        # Provide bars that will all be filtered out (penny stocks)
        bars_data = {
            "JUNK1": _make_bars_df(n=25, close=1.0, volume=100, atr_pct=0.15),
            "JUNK2": _make_bars_df(n=25, close=2.0, volume=200, atr_pct=0.15),
        }
        builder.preload_historical_bars(bars_data)
        result = builder.get_universe(as_of_date=as_of)

        # Legacy symbols (SPY, QQQ) should always be present
        self.assertIn("SPY", result)
        self.assertIn("QQQ", result)


class TestUniverseCache(unittest.TestCase):
    """Tests for caching behaviour."""

    def test_universe_cache_hit(self):
        """Calling get_universe() twice for same date should use cache (bars processed once)."""
        cfg = _base_config()
        builder = UniverseBuilder(cfg)
        as_of = datetime(2025, 6, 1, tzinfo=timezone.utc)

        bars_data = {
            "AAPL": _make_bars_df(n=25, close=150.0, volume=800_000, atr_pct=0.025),
        }
        builder.preload_historical_bars(bars_data)

        # First call — computes
        result1 = builder.get_universe(as_of_date=as_of)
        # Second call — should hit cache
        result2 = builder.get_universe(as_of_date=as_of)

        self.assertEqual(result1, result2)
        # Cache should have exactly one entry
        self.assertEqual(builder.get_stats()["cached_dates"], 1)


class TestBacktestPointInTime(unittest.TestCase):
    """Tests for point-in-time accuracy."""

    def test_backtest_point_in_time(self):
        """
        Symbol with price crossing min_price threshold mid-series.
        Early date (price below $5) should exclude it; later date should include.
        """
        cfg = _base_config()
        builder = UniverseBuilder(cfg)

        # Create bars where price starts at $3 (below min) and rises to $50 (above min)
        n = 40
        dates = pd.date_range(end="2025-06-01", periods=n, freq="D", tz="UTC")
        prices = np.concatenate([
            np.full(20, 3.0),   # First 20 days: $3 (below min_price $5)
            np.full(20, 50.0),  # Last 20 days: $50 (above min_price $5)
        ])
        data = {
            "close":  prices,
            "open":   prices,
            "high":   prices * 1.025,
            "low":    prices * 0.975,
            "volume": np.full(n, 1_000_000.0),
        }
        bars_data = {"CROSSER": pd.DataFrame(data, index=dates)}
        builder.preload_historical_bars(bars_data)

        # Early date: price at $3, should NOT qualify
        early = datetime(2025, 5, 1, tzinfo=timezone.utc)
        result_early = builder._screen_from_bars(bars_data, early)
        self.assertNotIn("CROSSER", result_early)

        # Later date: price at $50, should qualify
        late = datetime(2025, 6, 1, tzinfo=timezone.utc)
        result_late = builder._screen_from_bars(bars_data, late)
        self.assertIn("CROSSER", result_late)


class TestGetAllTradableAssets(unittest.TestCase):
    """Tests for core/alpaca_client.get_all_tradable_assets."""

    @patch("core.alpaca_client.get_trading_client")
    def test_get_all_tradable_assets_mock(self, mock_get_client):
        """
        Mock get_all_assets() returning 5 assets (mix of tradable/non-tradable, OTC, long ticker).
        Assert only valid ones are returned.
        """
        mock_asset_active_tradable = MagicMock()
        mock_asset_active_tradable.symbol = "AAPL"
        mock_asset_active_tradable.tradable = True
        mock_asset_active_tradable.status = MagicMock(value="active")

        mock_asset_not_tradable = MagicMock()
        mock_asset_not_tradable.symbol = "DEAD"
        mock_asset_not_tradable.tradable = False
        mock_asset_not_tradable.status = MagicMock(value="active")

        mock_asset_otc = MagicMock()
        mock_asset_otc.symbol = "BRK.B"
        mock_asset_otc.tradable = True
        mock_asset_otc.status = MagicMock(value="active")

        mock_asset_long_ticker = MagicMock()
        mock_asset_long_ticker.symbol = "ABCDEF"
        mock_asset_long_ticker.tradable = True
        mock_asset_long_ticker.status = MagicMock(value="active")

        mock_asset_good2 = MagicMock()
        mock_asset_good2.symbol = "MSFT"
        mock_asset_good2.tradable = True
        mock_asset_good2.status = MagicMock(value="active")

        mock_client = MagicMock()
        mock_client.get_all_assets.return_value = [
            mock_asset_active_tradable,
            mock_asset_not_tradable,
            mock_asset_otc,
            mock_asset_long_ticker,
            mock_asset_good2,
        ]
        mock_get_client.return_value = mock_client

        from core.alpaca_client import get_all_tradable_assets
        result = get_all_tradable_assets()

        # Should include AAPL and MSFT; exclude DEAD (not tradable), BRK.B (has dot), ABCDEF (>5 chars)
        self.assertIn("AAPL", result)
        self.assertIn("MSFT", result)
        self.assertNotIn("DEAD", result)
        self.assertNotIn("BRK.B", result)
        self.assertNotIn("ABCDEF", result)
        self.assertEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main()
