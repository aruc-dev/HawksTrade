"""
HawksTrade - EMA Crossover Strategy (Crypto)
=============================================
Enters when the fast EMA (9) crosses above the slow EMA (21).
Exits when the fast EMA crosses back below the slow EMA.

Works 24/7 on crypto pairs.
"""

import logging
from typing import List, Dict
from pathlib import Path

import yaml
import pandas as pd

from strategies.base_strategy import BaseStrategy
from core import alpaca_client as ac

BASE_DIR = Path(__file__).resolve().parent.parent
with open(BASE_DIR / "config" / "config.yaml") as f:
    CFG = yaml.safe_load(f)

SCFG = CFG["strategies"]["ma_crossover"]
log  = logging.getLogger("strategy.ma_crossover")


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


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


class MACrossoverStrategy(BaseStrategy):

    name        = "ma_crossover"
    asset_class = "crypto"

    def scan(self, universe: List[str], **kwargs) -> List[Dict]:
        if not SCFG["enabled"]:
            return []

        fast_span = SCFG["fast_ema"]
        slow_span = SCFG["slow_ema"]
        timeframe = SCFG["timeframe"]

        log.info(f"[MACross] Scanning {len(universe)} crypto pairs "
                 f"(EMA {fast_span}/{slow_span}, {timeframe})...")

        signals = []

        try:
            bars_data = ac.get_crypto_bars(universe, timeframe=timeframe, limit=slow_span + 10)
        except Exception as e:
            log.error(f"[MACross] Failed to fetch bars: {e}")
            return []

        for symbol in universe:
            try:
                bars = bars_data[symbol]
                if bars is None or len(bars) < slow_span + 2:
                    continue

                closes  = pd.Series([b.close for b in bars])
                fast    = _ema(closes, fast_span)
                slow    = _ema(closes, slow_span)
                cross   = _detect_crossover(fast, slow)

                price   = float(closes.iloc[-1])
                fast_v  = float(fast.iloc[-1])
                slow_v  = float(slow.iloc[-1])

                if cross == "bullish":
                    signals.append({
                        "symbol":      symbol,
                        "action":      "buy",
                        "strategy":    self.name,
                        "asset_class": self.asset_class,
                        "confidence":  round(min(abs(fast_v - slow_v) / slow_v * 10, 1.0), 3),
                        "reason":      f"EMA {fast_span} crossed above EMA {slow_span}",
                    })
                    log.info(f"[MACross] BULLISH crossover on {symbol} | "
                             f"fast={fast_v:.4f} slow={slow_v:.4f}")

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
            bars      = bars_data[symbol]
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
