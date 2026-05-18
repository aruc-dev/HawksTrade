import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from core import minute_cache


class MinuteCacheTests(unittest.TestCase):
    def _bars(self):
        return [
            SimpleNamespace(
                timestamp=datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc),
                open=100,
                high=101,
                low=99,
                close=100.5,
                volume=1000,
            ),
            SimpleNamespace(
                timestamp=datetime(2026, 1, 5, 14, 31, tzinfo=timezone.utc),
                open=100.5,
                high=102,
                low=100,
                close=101.5,
                volume=1200,
            ),
        ]

    def test_get_minute_bars_fetches_month_once_and_filters_window(self):
        calls = []

        def fetcher(symbol, start, end):
            calls.append((symbol, start, end))
            return pd.DataFrame(
                {
                    "timestamp": [bar.timestamp for bar in self._bars()],
                    "open": [bar.open for bar in self._bars()],
                    "high": [bar.high for bar in self._bars()],
                    "low": [bar.low for bar in self._bars()],
                    "close": [bar.close for bar in self._bars()],
                    "volume": [bar.volume for bar in self._bars()],
                }
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            first = minute_cache.get_minute_bars(
                "AAPL",
                datetime(2026, 1, 5, 14, 31, tzinfo=timezone.utc),
                datetime(2026, 1, 5, 14, 31, tzinfo=timezone.utc),
                cache_dir=Path(tmpdir),
                fetcher=fetcher,
            )
            second = minute_cache.get_minute_bars(
                "AAPL",
                datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc),
                datetime(2026, 1, 5, 14, 31, tzinfo=timezone.utc),
                cache_dir=Path(tmpdir),
                fetcher=fetcher,
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(first), 1)
        self.assertEqual(float(first.iloc[0]["close"]), 101.5)
        self.assertEqual(len(second), 2)

    def test_cache_path_sanitizes_crypto_like_symbols(self):
        path = minute_cache.cache_path("BTC/USD", datetime(2026, 1, 1, tzinfo=timezone.utc), "/tmp/cache")

        self.assertEqual(path, Path("/tmp/cache/BTCUSD/202601.parquet"))

    def test_get_minute_bars_rejects_reversed_window(self):
        with self.assertRaisesRegex(ValueError, "start must be before end"):
            minute_cache.get_minute_bars(
                "AAPL",
                datetime(2026, 1, 5, 14, 31, tzinfo=timezone.utc),
                datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc),
            )

    def test_fetch_stock_minute_bars_uses_supplied_source(self):
        calls = []

        def source(symbols, timeframe="1Day", limit=60, start=None, end=None):
            calls.append({
                "symbols": symbols,
                "timeframe": timeframe,
                "limit": limit,
                "start": start,
                "end": end,
            })
            return {"AAPL": self._bars()}

        start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        end = datetime(2026, 1, 5, 14, 31, tzinfo=timezone.utc)
        frame = minute_cache.fetch_stock_minute_bars("AAPL", start, end, source=source)

        self.assertEqual(len(frame), 2)
        self.assertEqual(calls[0]["symbols"], ["AAPL"])
        self.assertEqual(calls[0]["timeframe"], "1Min")
        self.assertEqual(calls[0]["start"], start)
        self.assertEqual(calls[0]["end"], end)


if __name__ == "__main__":
    unittest.main()
