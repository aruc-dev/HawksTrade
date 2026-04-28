"""
HawksTrade - Range Breakout Strategy (Crypto)
==============================================
Enters long when price breaks above the prior day's high
with volume confirmation. Holds for hold_days days.

Works 24/7 on crypto pairs.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import List, Dict
from pathlib import Path

import pandas as pd
import numpy as np

from strategies.base_strategy import BaseStrategy
from core import alpaca_client as ac
from core import risk_manager as rm
from core.config_loader import get_config

BASE_DIR = Path(__file__).resolve().parent.parent
CFG = get_config()

SCFG = CFG["strategies"]["range_breakout"]
log  = logging.getLogger("strategy.range_breakout")


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


class RangeBreakoutStrategy(BaseStrategy):

    name        = "range_breakout"
    asset_class = "crypto"

    def scan(self, universe: List[str], **kwargs) -> List[Dict]:
        if not SCFG["enabled"]:
            return []

        breakout_pct = SCFG["breakout_pct"]
        vol_mult     = SCFG["volume_multiplier"]
        timeframe    = SCFG["timeframe"]
        risk_pct     = float(SCFG.get("risk_per_trade_pct", 0.01))
        atr_period   = int(SCFG.get("atr_period", 14))
        atr_mult     = float(SCFG.get("atr_multiplier", 2.0))
        vol_filter_period = int(SCFG.get("vol_filter_period", 10))
        min_trade_value = float(CFG["trading"].get("min_trade_value_usd", 100))

        log.info(f"[Breakout] Scanning {len(universe)} crypto pairs...")

        signals = []

        try:
            # Need 50 bars for trend filter + max(atr_period, vol_filter_period, 20)
            limit = max(60, atr_period + 10, vol_filter_period + 10)
            bars_data = ac.get_crypto_bars(universe, timeframe=timeframe, limit=limit)
        except Exception as e:
            log.error(f"[Breakout] Failed to fetch bars: {e}")
            return []

        regime_bars = kwargs.get("regime_bars")
        if not rm.crypto_regime_ok(bars_data=regime_bars):
            log.info("[Breakout] Crypto bear regime (BTC < EMA20), skipping scan.")
            return []

        for symbol in universe:
            try:
                bars = _bars_for_symbol(bars_data, symbol)
                if bars is None or len(bars) < max(52, atr_period + 1, vol_filter_period + 1):
                    continue

                # Use risk_manager helper for safe attribute access
                from core.risk_manager import _get_closes
                
                df = pd.DataFrame({
                    "high":   [float(b.high) if hasattr(b, "high") else float(b["high"]) for b in bars],
                    "low":    [float(b.low) if hasattr(b, "low") else float(b["low"]) for b in bars],
                    "close":  _get_closes(bars),
                    "volume": [float(b.volume) if hasattr(b, "volume") else float(b["volume"]) for b in bars],
                })

                prev_high  = df["high"].iloc[-2]
                today_cls  = df["close"].iloc[-1]
                today_vol  = df["volume"].iloc[-1]
                avg_vol    = df["volume"].iloc[-21:-1].mean()
                ema50      = df["close"].ewm(span=50, adjust=False).mean().iloc[-1]

                # Volatility Filter
                ranges = df["high"] - df["low"]
                # Fix: Use configurable vol_filter_period
                if vol_filter_period > 0:
                    avg_range = ranges.iloc[-(vol_filter_period + 1):-1].mean()
                    curr_range = df["high"].iloc[-1] - df["low"].iloc[-1]
                    is_volatile = curr_range >= (avg_range * 0.5)
                else:
                    is_volatile = True

                breakout_level = prev_high * (1 + breakout_pct)

                # Condition: Price breakout, High Volume, long-term Uptrend, and Volatility
                if today_cls >= breakout_level and today_vol >= avg_vol * vol_mult and today_cls > ema50 and is_volatile:
                    from strategies.rsi_reversion import _calc_atr
                    
                    price = float(today_cls)
                    atr = _calc_atr(bars, atr_period)
                    atr_stop = round(price - atr_mult * atr, 4)

                    portfolio_value = ac.get_portfolio_value()
                    risk_amount = portfolio_value * risk_pct
                    risk_per_share = price - atr_stop

                    if risk_per_share > 0:
                        qty = math.floor(risk_amount / risk_per_share)
                        # Cap at 5% portfolio
                        max_qty = math.floor((portfolio_value * 0.05) / price)
                        qty = min(qty, max_qty)

                        if qty * price >= min_trade_value:
                            excess_pct = (today_cls - prev_high) / prev_high
                            signals.append({
                                "symbol":      symbol,
                                "action":      "buy",
                                "strategy":    self.name,
                                "asset_class": self.asset_class,
                                "confidence":  round(min(excess_pct / 0.02, 1.0), 3),
                                "atr_stop":    atr_stop,
                                "atr_risk_qty": qty,
                                "reason":      (
                                    f"Breakout above prior high {prev_high:.4f} | "
                                    f"vol={today_vol/avg_vol:.1f}x avg | Trend UP | Vol Confirm"
                                ),
                            })
                            log.info(
                                f"[Breakout] Signal: BUY {symbol} | qty={qty} | stop={atr_stop} | "
                                f"close={today_cls:.4f} > prev_high={prev_high:.4f} "
                                f"vol={today_vol/avg_vol:.1f}x | Trend UP | Vol Confirm"
                            )

            except Exception as e:
                log.warning(f"[Breakout] Error for {symbol}: {e}")
                continue

        return signals

    def should_exit(self, symbol: str, entry_price: float) -> tuple:
        """
        Range breakout exits via risk_manager stop/take-profit
        or after hold_days (checked by scheduler via trade log age).
        """
        return False, ""
