"""
HawksTrade - EMA Crossover Strategy (Crypto)
=============================================
Enters when the fast EMA (9) crosses above the slow EMA (21).
Exits when the fast EMA crosses back below the slow EMA.

Works 24/7 on crypto pairs.
"""

from __future__ import annotations

import logging
from typing import List, Dict
from pathlib import Path

import pandas as pd

from strategies.base_strategy import BaseStrategy
from strategies.rsi_reversion import _calc_rsi
from core import alpaca_client as ac
from core import risk_manager as rm
from core.config_loader import get_config

BASE_DIR = Path(__file__).resolve().parent.parent
CFG = get_config()

SCFG = CFG["strategies"]["ma_crossover"]
log  = logging.getLogger("strategy.ma_crossover")


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _calc_atr(bars, period: int = 14) -> float:
    """
    Compute ATR(period) from a bar list.
    True Range = max(high-low, |high-prev_close|, |low-prev_close|).
    Returns ATR as an absolute price value.
    """
    highs  = pd.Series([float(b.high)  if hasattr(b, "high")  else float(b["high"])  for b in bars])
    lows   = pd.Series([float(b.low)   if hasattr(b, "low")   else float(b["low"])   for b in bars])
    closes = pd.Series([float(b.close) if hasattr(b, "close") else float(b["close"]) for b in bars])

    prev_close = closes.shift(1)
    tr = pd.concat([
        highs - lows,
        (highs - prev_close).abs(),
        (lows  - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(span=period, min_periods=period).mean()
    return float(atr.iloc[-1])


def _detect_crossover(fast: pd.Series, slow: pd.Series) -> str:
    """
    Returns 'bullish' if fast just crossed above slow,
            'bearish' if fast just crossed below slow,
            'none'    otherwise.
    """
    if len(fast) < 2:
        return "none"

    prev_diff = fast.iloc[-2] - slow.iloc[-2]
    curr_diff = fast.iloc[-1] - slow.iloc[-1]

    if prev_diff < 0 and curr_diff > 0:
        return "bullish"
    if prev_diff > 0 and curr_diff < 0:
        return "bearish"
    return "none"


def _bars_for_symbol(bars_data, symbol: str):
    """Return bars for slashed or slashless crypto symbols without raising on missing data."""
    lookup_symbol = ac.to_crypto_pair_symbol(symbol)
    for key in dict.fromkeys((lookup_symbol, symbol)):
        try:
            bars = bars_data[key]
        except (AttributeError, KeyError, TypeError):
            bars = None
        if bars is not None:
            return bars
    return None


class MACrossoverStrategy(BaseStrategy):

    name        = "ma_crossover"
    asset_class = "crypto"

    def scan(self, universe: List[str], **kwargs) -> List[Dict]:
        if not SCFG["enabled"]:
            return []

        fast_span = SCFG["fast_ema"]
        slow_span = SCFG["slow_ema"]
        timeframe = SCFG["timeframe"]
        atr_period = SCFG.get("atr_period", 14)
        atr_mult   = SCFG.get("atr_multiplier", 2.0)

        log.info(f"[MACross] Scanning {len(universe)} crypto pairs "
                 f"(EMA {fast_span}/{slow_span}, {timeframe})...")

        signals = []

        try:
            bars_data = ac.get_crypto_bars(universe, timeframe=timeframe, limit=max(slow_span, atr_period) + 20)
        except Exception as e:
            log.error(f"[MACross] Failed to fetch bars: {e}")
            return []

        regime_bars = kwargs.get("regime_bars")
        if not rm.crypto_regime_ok(bars_data=regime_bars):
            log.info("[MACross] Crypto bear regime (BTC < EMA20), skipping scan.")
            return []

        for symbol in universe:
            try:
                bars = _bars_for_symbol(bars_data, symbol)
                if bars is None or len(bars) < max(slow_span, atr_period) + 5:
                    continue

                closes  = pd.Series([b.close for b in bars])
                fast    = _ema(closes, fast_span)
                slow    = _ema(closes, slow_span)
                cross   = _detect_crossover(fast, slow)

                # Slope Filter: Ensure slow EMA is actually trending up over last 4 periods
                is_trending_up = slow.iloc[-1] > slow.iloc[-5]

                # Volatility Filter: Ensure we aren't in a completely dead market
                # Current range must be at least 0.5x the 10-day average range
                highs  = pd.Series([b.high for b in bars])
                lows   = pd.Series([b.low for b in bars])
                ranges = highs - lows
                avg_range = ranges.iloc[-11:-1].mean()
                curr_range = highs.iloc[-1] - lows.iloc[-1]
                is_volatile = curr_range >= (avg_range * 0.5)

                fast_v  = float(fast.iloc[-1])
                slow_v  = float(slow.iloc[-1])
                price   = float(closes.iloc[-1])

                rsi_val = _calc_rsi(closes, 14)

                if cross == "bullish" and is_trending_up and is_volatile and 35 <= rsi_val <= 70:
                    atr = _calc_atr(bars, atr_period)
                    atr_stop = round(price - atr_mult * atr, 4)

                    signals.append({
                        "symbol":      symbol,
                        "action":      "buy",
                        "strategy":    self.name,
                        "asset_class": self.asset_class,
                        "confidence":  round(min(abs(fast_v - slow_v) / slow_v * 10, 1.0), 3),
                        "atr_stop_price": atr_stop,
                        "reason":      f"BULLISH {fast_span}/{slow_span} EMA Crossover | Slope UP | Vol Confirm | RSI={rsi_val:.1f} | ATR Stop={atr_stop}",
                    })
                    log.info(f"[MACross] BULLISH crossover on {symbol} | Trend UP | Vol Confirm | RSI={rsi_val:.1f} | ATR Stop={atr_stop}")

            except Exception as e:
                log.warning(f"[MACross] Error for {symbol}: {e}")
                continue

        return signals

    def should_exit(self, symbol: str, entry_price: float) -> tuple:
        """Exit when fast EMA crosses below slow EMA."""
        fast_span = SCFG["fast_ema"]
        slow_span = SCFG["slow_ema"]
        timeframe = SCFG["timeframe"]

        try:
            bars_data = ac.get_crypto_bars([symbol], timeframe=timeframe, limit=slow_span + 10)
            bars      = _bars_for_symbol(bars_data, symbol)
            if bars is None or len(bars) < slow_span + 2:
                return False, ""

            closes = pd.Series([b.close for b in bars])
            fast   = _ema(closes, fast_span)
            slow   = _ema(closes, slow_span)
            cross  = _detect_crossover(fast, slow)

            if cross == "bearish":
                return True, f"EMA {fast_span} crossed below EMA {slow_span}"

        except Exception as e:
            log.warning(f"[MACross] Exit check error for {symbol}: {e}")

        return False, ""
