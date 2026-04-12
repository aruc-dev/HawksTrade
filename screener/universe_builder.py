import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional

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
        self._historical_bars: Dict[str, object] = {}       # symbol -> bars df (backtest)

        # Screening parameters (can be overridden from config)
        screener_cfg = config.get("screener", {})
        self.min_adv_shares   = screener_cfg.get("min_adv_shares",   500_000)
        self.min_adv_dollars  = screener_cfg.get("min_adv_dollars",  5_000_000)
        self.min_price        = screener_cfg.get("min_price",        5.0)
        self.max_price        = screener_cfg.get("max_price",        2000.0)
        self.min_atr_pct      = screener_cfg.get("min_atr_pct",      0.01)
        self.max_atr_pct      = screener_cfg.get("max_atr_pct",      0.08)
        self.max_universe     = screener_cfg.get("max_universe",     100)
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

        if self._historical_bars:
            # Backtest mode: derive universe from injected historical bars
            qualified = self._screen_from_bars(self._historical_bars, as_of_date)
        else:
            # Live mode: fetch from Alpaca
            qualified = self._screen_live(as_of_date)

        # Always merge legacy universe (so existing 20 symbols are never dropped)
        result = list(dict.fromkeys(qualified + self.legacy_universe))  # dedup, preserve order

        self._universe_cache[date_key] = result
        log.info(f"[Screener] {date_key}: {len(result)} symbols qualified "
                 f"({len(qualified)} dynamic + {len(self.legacy_universe)} legacy, "
                 f"deduped to {len(result)})")
        return result

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
                    df = df[mask].tail(25)
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
                    } for b in filtered[-25:]])

                if len(df) < 15:
                    continue

                score = self._compute_score(df, symbol)
                if score is not None:
                    scores.append(score)

            except Exception as e:
                log.debug(f"[Screener] Skipping {symbol}: {e}")
                continue

        # Sort by dollar volume descending, cap at max_universe
        scores.sort(key=lambda x: x['adv_dollars'], reverse=True)
        return [s['symbol'] for s in scores[:self.max_universe]]

    def _screen_live(self, as_of_date: datetime) -> List[str]:
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
                bars_batch = self.ac.get_stock_bars(batch, timeframe="1Day", limit=25)
                for symbol in batch:
                    bars = bars_batch[symbol] if bars_batch else None
                    if bars is None or len(bars) < 15:
                        continue
                    df = pd.DataFrame([{
                        'close': float(b.close), 'volume': float(b.volume),
                        'high': float(b.high), 'low': float(b.low)
                    } for b in bars[-25:]])
                    score = self._compute_score(df, symbol)
                    if score is not None:
                        scores.append(score)
            except Exception as e:
                log.warning(f"[Screener] Batch {i}-{i+200} error: {e}")
                continue

        scores.sort(key=lambda x: x['adv_dollars'], reverse=True)
        return [s['symbol'] for s in scores[:self.max_universe]]

    def _compute_score(self, df: pd.DataFrame, symbol: str) -> Optional[Dict]:
        """
        Apply screening filters and return score dict or None if filtered out.
        """
        try:
            close   = df['close'].iloc[-1]
            volume  = df['volume']

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
            closes = df['close'].values
            tr = np.maximum(
                highs[1:] - lows[1:],
                np.maximum(
                    np.abs(highs[1:] - closes[:-1]),
                    np.abs(lows[1:]  - closes[:-1])
                )
            )
            atr14 = tr[-14:].mean() if len(tr) >= 14 else tr.mean()
            atr_pct = atr14 / close
            if not (self.min_atr_pct <= atr_pct <= self.max_atr_pct):
                return None

            return {
                'symbol':      symbol,
                'close':       close,
                'adv_shares':  adv_shares,
                'adv_dollars': adv_dollars,
                'atr_pct':     atr_pct,
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
