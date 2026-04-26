"""
HawksTrade - Momentum Strategy (Stocks)
========================================
Ranks all stocks in the universe by 5-day price momentum.
Buys the top N if momentum > min_momentum_pct.
Exits are handled by the scheduler: flat/losing trades exit after
the minimum hold, while profitable trades can run with trailing protection.

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
from core import risk_manager as rm
from core.config_loader import get_config_path

BASE_DIR = Path(__file__).resolve().parent.parent
with open(get_config_path()) as f:
    CFG = yaml.safe_load(f)

SCFG = CFG["strategies"]["momentum"]
log = logging.getLogger("strategy.momentum")


class MomentumStrategy(BaseStrategy):

    name        = "momentum"
    asset_class = "stocks"

    @staticmethod
    def _missing_symbol_error(exc: Exception) -> bool:
        message = str(exc)
        return isinstance(exc, KeyError) or message.startswith("'No key ") or message.startswith("No key ")

    def _get_symbol_bars(self, bars_data, symbol: str):
        try:
            return bars_data[symbol]
        except Exception as exc:
            if self._missing_symbol_error(exc):
                return None
            raise

    def _load_symbol_bars(self, bars_data, symbol: str):
        bars = self._get_symbol_bars(bars_data, symbol)
        if bars is not None:
            return bars
        try:
            fallback = ac.get_stock_bars([symbol], timeframe="1Day", limit=10)
        except Exception as exc:
            log.debug(f"[Momentum] Fallback bars fetch failed for {symbol}: {exc}")
            return None
        bars = self._get_symbol_bars(fallback, symbol)
        if bars is None:
            log.debug(f"[Momentum] Bars still missing for {symbol} after fallback fetch.")
        return bars

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

        regime_bars = kwargs.get("regime_bars")
        if not rm.market_regime_ok(bars_data=regime_bars):
            log.info("[Momentum] Bear regime (SPY < SMA50), skipping scan.")
            return []

        for symbol in universe:
            try:
                bars = self._load_symbol_bars(bars_data, symbol)
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
        Strategy-level hold/trailing exits are checked by the scheduler.
        """
        return False, ""
