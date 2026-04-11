"""
HawksTrade - RSI Mean Reversion Strategy (Stocks)
===================================================
Buys when RSI drops below oversold_threshold (default 30).
Sells when RSI rises above overbought_threshold (default 60).

Strategy: Swing trade (NOT intraday).
"""

import logging
from typing import List, Dict
from pathlib import Path

import yaml
import pandas as pd
import numpy as np

from strategies.base_strategy import BaseStrategy
from core import alpaca_client as ac

BASE_DIR = Path(__file__).resolve().parent.parent
with open(BASE_DIR / "config" / "config.yaml") as f:
    CFG = yaml.safe_load(f)

SCFG = CFG["strategies"]["rsi_reversion"]
log  = logging.getLogger("strategy.rsi_reversion")


def _calc_rsi(closes: pd.Series, period: int = 14) -> float:
    """Compute RSI for a price series, return the latest value."""
    delta  = closes.diff()
    gain   = delta.where(delta > 0, 0.0)
    loss   = -delta.where(delta < 0, 0.0)
    avg_g  = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_l  = loss.ewm(com=period - 1, min_periods=period).mean()
    rs     = avg_g / avg_l
    rsi    = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])


class RSIReversionStrategy(BaseStrategy):

    name        = "rsi_reversion"
    asset_class = "stocks"

    def scan(self, universe: List[str], **kwargs) -> List[Dict]:
        if not SCFG["enabled"]:
            return []

        period  = SCFG["rsi_period"]
        oversold = SCFG["oversold_threshold"]
        sma_period = 50
        log.info(f"[RSI] Scanning {len(universe)} symbols (period={period}, oversold<{oversold}, trend=SMA{sma_period})...")

        try:
            bars_data = ac.get_stock_bars(universe, timeframe="1Day", limit=max(period, sma_period) + 10)
        except Exception as e:
            log.error(f"[RSI] Failed to fetch bars: {e}")
            return []

        signals = []

        for symbol in universe:
            try:
                bars = bars_data[symbol]
                if bars is None or len(bars) < max(period, sma_period) + 1:
                    continue

                closes = pd.Series([b.close for b in bars])
                rsi    = _calc_rsi(closes, period)
                sma50  = closes.rolling(window=sma_period).mean().iloc[-1]
                price  = float(bars[-1].close)

                # Trend Filter: Only buy if price is above 50-day SMA
                if rsi < oversold and price > sma50:
                    signals.append({
                        "symbol":      symbol,
                        "action":      "buy",
                        "strategy":    self.name,
                        "asset_class": self.asset_class,
                        "confidence":  round((oversold - rsi) / oversold, 3),
                        "reason":      f"RSI oversold ({rsi:.1f}) and Price > SMA50 ({price:.2f} > {sma50:.2f})",
                    })
                    log.info(f"[RSI] Signal: BUY {symbol} | RSI={rsi:.1f} | SMA50={sma50:.2f}")

            except Exception as e:
                log.warning(f"[RSI] Error for {symbol}: {e}")
                continue

        return signals

    def should_exit(self, symbol: str, entry_price: float) -> tuple:
        """Exit when RSI rises above overbought threshold."""
        overbought = SCFG["overbought_threshold"]
        period     = SCFG["rsi_period"]

        try:
            bars_data = ac.get_stock_bars([symbol], timeframe="1Day", limit=period + 10)
            bars      = bars_data[symbol]
            if bars is None or len(bars) < period + 1:
                return False, ""

            closes = pd.Series([b.close for b in bars])
            rsi    = _calc_rsi(closes, period)

            if rsi > overbought:
                return True, f"RSI overbought: {rsi:.1f} > {overbought}"

        except Exception as e:
            log.warning(f"[RSI] Exit check error for {symbol}: {e}")

        return False, ""
