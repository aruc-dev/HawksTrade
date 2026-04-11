"""
HawksTrade - Momentum Strategy (Stocks)
========================================
Ranks all stocks in the universe by 5-day price momentum.
Buys the top N if momentum > min_momentum_pct.
Exits after hold_days trading days.

Strategy: Swing trade (NOT intraday).
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict

import yaml
import pandas as pd
from pathlib import Path

from strategies.base_strategy import BaseStrategy
from core import alpaca_client as ac

BASE_DIR = Path(__file__).resolve().parent.parent
with open(BASE_DIR / "config" / "config.yaml") as f:
    CFG = yaml.safe_load(f)

SCFG = CFG["strategies"]["momentum"]
log = logging.getLogger("strategy.momentum")


class MomentumStrategy(BaseStrategy):

    name        = "momentum"
    asset_class = "stocks"

    def scan(self, universe: List[str], **kwargs) -> List[Dict]:
        if not SCFG["enabled"]:
            return []

        log.info(f"[Momentum] Scanning {len(universe)} symbols...")
        scores = []

        try:
            bars_data = ac.get_stock_bars(universe, timeframe="1Day", limit=10)
        except Exception as e:
            log.error(f"[Momentum] Failed to fetch bars: {e}")
            return []

        for symbol in universe:
            try:
                bars = bars_data[symbol]
                if bars is None or len(bars) < 6:
                    continue

                df = pd.DataFrame([{
                    "close": b.close,
                    "volume": b.volume,
                    "timestamp": b.timestamp,
                } for b in bars])

                price_5d_ago = df["close"].iloc[-6]
                price_now    = df["close"].iloc[-1]
                momentum     = (price_now - price_5d_ago) / price_5d_ago

                if momentum >= SCFG["min_momentum_pct"]:
                    scores.append({
                        "symbol":     symbol,
                        "momentum":   momentum,
                        "price":      price_now,
                    })

            except Exception as e:
                log.warning(f"[Momentum] Error processing {symbol}: {e}")
                continue

        # Rank by momentum, take top N
        scores.sort(key=lambda x: x["momentum"], reverse=True)
        top = scores[:SCFG["top_n"]]

        signals = []
        for s in top:
            signals.append({
                "symbol":     s["symbol"],
                "action":     "buy",
                "strategy":   self.name,
                "asset_class": self.asset_class,
                "confidence": min(s["momentum"] / 0.10, 1.0),  # normalise to [0,1]
                "reason":     f"5-day momentum: {s['momentum']:.2%}",
            })
            log.info(f"[Momentum] Signal: BUY {s['symbol']} | momentum={s['momentum']:.2%}")

        return signals

    def should_exit(self, symbol: str, entry_price: float) -> tuple:
        """
        Momentum exits on take-profit / stop-loss (handled by risk_manager).
        Strategy-level exit: after hold_days (checked by scheduler via trade log).
        """
        return False, ""
