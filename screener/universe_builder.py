import json
import logging
from pathlib import Path

import pandas as pd
import numpy as np
from datetime import datetime, timezone
from typing import List, Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent

log = logging.getLogger("screener")

class UniverseBuilder:
    """
    Builds a dynamic, point-in-time accurate stock universe for each trading day.

    In live mode: fetches current asset list + recent bars to score candidates.
    In backtest mode: uses historical bar data keyed to the simulation date.
    """

    def __init__(self, config: dict, alpaca_client=None):
        self.cfg = config
        self.ac = alpaca_client
        self._asset_cache: Optional[List[str]] = None       # broad symbol list (session-level)
        self._universe_cache: Dict[str, List[str]] = {}     # date -> qualified symbols
        self._historical_bars: Optional[Dict[str, object]] = None  # symbol -> bars df (backtest)

        # Screening parameters (can be overridden from config)
        screener_cfg = config.get("screener", {})
        self.min_adv_shares   = screener_cfg.get("min_adv_shares",   500_000)
        self.min_adv_dollars  = screener_cfg.get("min_adv_dollars",  5_000_000)
        self.min_price        = screener_cfg.get("min_price",        5.0)
        self.max_price        = screener_cfg.get("max_price",        2000.0)
        self.min_atr_pct      = screener_cfg.get("min_atr_pct",      0.01)
        self.max_atr_pct      = screener_cfg.get("max_atr_pct",      0.08)
        self.max_universe     = screener_cfg.get("max_universe",     100)
        self.trend_sma_days   = screener_cfg.get("trend_sma_days",   50)
        self.min_trend_sma_ratio = screener_cfg.get("min_trend_sma_ratio", 0.0)
        self.max_trend_sma_ratio = screener_cfg.get("max_trend_sma_ratio", 999.0)
        self.min_20d_return_pct  = screener_cfg.get("min_20d_return_pct",  None)
        self.max_20d_return_pct  = screener_cfg.get("max_20d_return_pct",  None)
        self.target_atr_pct   = screener_cfg.get("target_atr_pct",    0.03)
        self.live_batch_retries = int(screener_cfg.get("live_batch_retries", 1))
        self.lookback_days    = max(25, int(self.trend_sma_days) + 5)
        self.legacy_universe  = config.get("stocks", {}).get("scan_universe", [])

    def preload_historical_bars(self, bars_data: Dict[str, object]):
        """
        Called by the backtest simulator to inject pre-fetched historical bars.
        Allows point-in-time screening without additional API calls.
        """
        self._historical_bars = bars_data

    def get_universe(self, as_of_date: Optional[datetime] = None) -> List[str]:
        """
        Returns a qualified list of stock symbols for the given date.
        Uses cache — each date only screened once per session.
        """
        if as_of_date is None:
            as_of_date = datetime.now(timezone.utc)

        date_key = as_of_date.strftime("%Y-%m-%d")
        if date_key in self._universe_cache:
            return self._universe_cache[date_key]

        if self._historical_bars is not None:
            # Backtest mode: derive universe from injected historical bars
            qualified = self._screen_from_bars(self._historical_bars, as_of_date)
        else:
            # Live mode: use pre-computed universe file if available, else fetch from Alpaca
            precomputed = self._load_precomputed_universe(date_key)
            qualified = precomputed if precomputed is not None else self._screen_live()

        # Always merge legacy universe (so existing 20 symbols are never dropped)
        result = list(dict.fromkeys(qualified + self.legacy_universe))  # dedup, preserve order

        self._universe_cache[date_key] = result
        log.info(f"[Screener] {date_key}: {len(result)} symbols qualified "
                 f"({len(qualified)} dynamic + {len(self.legacy_universe)} legacy, "
                 f"deduped to {len(result)})")
        return result

    def _load_precomputed_universe(self, date_key: str) -> Optional[List[str]]:
        """Load a pre-computed universe file written by run_screener.py, if available."""
        universe_path = BASE_DIR / "data" / f"universe_{date_key}.json"
        if not universe_path.exists():
            return None
        try:
            with open(universe_path, "r") as f:
                data = json.load(f)
            symbols = data.get("symbols")
            if isinstance(symbols, list) and symbols:
                log.info(
                    f"[Screener] Loaded pre-computed universe for {date_key}: "
                    f"{len(symbols)} symbols from {universe_path.name}"
                )
                return symbols
        except Exception as e:
            log.warning(f"[Screener] Could not read pre-computed universe at {universe_path}: {e}")
        return None

    def _screen_from_bars(self, bars_data: Dict, as_of_date: datetime) -> List[str]:
        """Screen using pre-fetched historical bars (backtest mode)."""
        scores = []

        for symbol, bars in bars_data.items():
            # Skip crypto (handled separately) and non-string symbols
            if "/" in symbol or bars is None:
                continue

            try:
                # Get bars up to as_of_date
                if hasattr(bars, 'index'):  # DataFrame
                    df = bars
                    mask = df.index <= as_of_date if df.index.tz is not None else df.index <= as_of_date.replace(tzinfo=None)
                    df = df[mask].tail(self.lookback_days)
                else:
                    # List of bar objects
                    filtered = [b for b in bars if (b.timestamp if hasattr(b, 'timestamp') else b['timestamp']) <= as_of_date]
                    if not filtered:
                        continue
                    df = pd.DataFrame([{
                        'close': float(b.close if hasattr(b, 'close') else b['close']),
                        'volume': float(b.volume if hasattr(b, 'volume') else b['volume']),
                        'high': float(b.high if hasattr(b, 'high') else b['high']),
                        'low': float(b.low if hasattr(b, 'low') else b['low']),
                    } for b in filtered[-self.lookback_days:]])

                if len(df) < 15:
                    continue

                score = self._compute_score(df, symbol)
                if score is not None:
                    scores.append(score)

            except Exception as e:
                log.debug(f"[Screener] Skipping {symbol}: {e}")
                continue

        # Sort by quality score, cap at max_universe.
        # Liquidity is still part of the score, but not the only selector.
        scores.sort(key=lambda x: x['score'], reverse=True)
        return [s['symbol'] for s in scores[:self.max_universe]]

    def _screen_live(self) -> List[str]:
        """Screen using live Alpaca data."""
        if self._asset_cache is None:
            try:
                self._asset_cache = self.ac.get_all_tradable_assets()
                log.info(f"[Screener] Loaded {len(self._asset_cache)} tradable assets")
            except Exception as e:
                log.warning(f"[Screener] Could not fetch asset list: {e}. Using legacy universe.")
                return self.legacy_universe

        # Batch-fetch 30-day bars for candidates (process in batches of 200)
        scores = []
        candidates = self._asset_cache

        for i in range(0, len(candidates), 200):
            batch = candidates[i:i+200]
            try:
                bars_batch = self._fetch_live_batch(batch, i)
                for symbol in batch:
                    score = self._score_live_symbol_bars(bars_batch, symbol)
                    if score is not None:
                        scores.append(score)
            except Exception as e:
                log.warning(f"[Screener] Batch {i}-{i+200} error: {e}. Falling back to per-symbol fetch.")
                for symbol in batch:
                    score = self._screen_live_symbol(symbol)
                    if score is not None:
                        scores.append(score)

        scores.sort(key=lambda x: x['score'], reverse=True)
        return [s['symbol'] for s in scores[:self.max_universe]]

    def _fetch_live_batch(self, batch: List[str], start_index: int):
        """Fetch one live batch, retrying transient failures before fallback."""
        attempts = max(1, self.live_batch_retries + 1)
        for attempt in range(1, attempts + 1):
            try:
                return self.ac.get_stock_bars(batch, timeframe="1Day", limit=self.lookback_days)
            except Exception as e:
                if attempt >= attempts:
                    raise
                log.info(
                    f"[Screener] Batch {start_index}-{start_index + len(batch)} "
                    f"attempt {attempt} error: {e}. Retrying."
                )

    def _screen_live_symbol(self, symbol: str) -> Optional[Dict]:
        """Fetch and score one symbol after a batch request fails."""
        try:
            bars_batch = self.ac.get_stock_bars([symbol], timeframe="1Day", limit=self.lookback_days)
            return self._score_live_symbol_bars(bars_batch, symbol)
        except Exception as e:
            log.debug(f"[Screener] Skipping {symbol} after fallback fetch failed: {e}")
            return None

    def _score_live_symbol_bars(self, bars_batch, symbol: str) -> Optional[Dict]:
        """Extract one symbol from an Alpaca bars response and score it."""
        if not bars_batch:
            return None
        try:
            bars = bars_batch[symbol]
        except Exception as e:
            log.debug(f"[Screener] Missing bars for {symbol}: {e}")
            return None
        if bars is None or len(bars) < 15:
            return None
        df = pd.DataFrame([{
            'close': float(b.close), 'volume': float(b.volume),
            'high': float(b.high), 'low': float(b.low)
        } for b in bars[-self.lookback_days:]])
        return self._compute_score(df, symbol)

    def _compute_score(self, df: pd.DataFrame, symbol: str) -> Optional[Dict]:
        """
        Apply screening filters and return score dict or None if filtered out.
        """
        try:
            close   = df['close'].iloc[-1]
            volume  = df['volume']
            closes  = df['close']

            # Price filter
            if not (self.min_price <= close <= self.max_price):
                return None

            # ADV (shares) filter — 20-day average
            adv_shares  = volume.tail(20).mean()
            if adv_shares < self.min_adv_shares:
                return None

            # Dollar volume filter
            adv_dollars = (df['close'] * df['volume']).tail(20).mean()
            if adv_dollars < self.min_adv_dollars:
                return None

            # ATR % filter — 14-day ATR as % of close
            highs  = df['high'].values
            lows   = df['low'].values
            close_values = df['close'].values
            tr = np.maximum(
                highs[1:] - lows[1:],
                np.maximum(
                    np.abs(highs[1:] - close_values[:-1]),
                    np.abs(lows[1:]  - close_values[:-1])
                )
            )
            atr14 = tr[-14:].mean() if len(tr) >= 14 else tr.mean()
            atr_pct = atr14 / close
            if not (self.min_atr_pct <= atr_pct <= self.max_atr_pct):
                return None

            if self.trend_sma_days and len(closes) >= self.trend_sma_days:
                trend_sma = closes.tail(self.trend_sma_days).mean()
                if trend_sma <= 0:
                    return None
                trend_ratio = close / trend_sma
                if not (self.min_trend_sma_ratio <= trend_ratio <= self.max_trend_sma_ratio):
                    return None
            else:
                trend_ratio = 1.0

            if len(closes) >= 21:
                return_20d = (close / closes.iloc[-21]) - 1
                if self.min_20d_return_pct is not None and return_20d < self.min_20d_return_pct:
                    return None
                if self.max_20d_return_pct is not None and return_20d > self.max_20d_return_pct:
                    return None
            else:
                return_20d = 0.0

            atr_quality = max(0.0, 1.0 - abs(atr_pct - self.target_atr_pct) / max(self.target_atr_pct, 1e-9))
            liquidity_score = np.log10(max(adv_dollars, 1.0))
            trend_score = max(return_20d, 0.0) * 10.0
            score = liquidity_score + trend_score + atr_quality

            return {
                'symbol':      symbol,
                'close':       close,
                'adv_shares':  adv_shares,
                'adv_dollars': adv_dollars,
                'atr_pct':     atr_pct,
                'trend_ratio': trend_ratio,
                'return_20d':  return_20d,
                'score':       score,
            }
        except Exception:
            return None

    def get_stats(self) -> Dict:
        """Return summary stats about the current screener cache."""
        return {
            'cached_dates': len(self._universe_cache),
            'asset_pool_size': len(self._asset_cache) if self._asset_cache else 0,
            'dates': list(self._universe_cache.keys()),
        }
