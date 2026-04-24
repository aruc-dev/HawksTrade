"""
HawksTrade - Gap-Up Strategy (Stocks)
=======================================
Identifies stocks that gap up >3% at the open on above-average volume.
Entry is swing-oriented: hold for hold_days, NOT intraday exit by default.

NOTE: Intraday exit is controlled by config intraday.enabled.
      When intraday is disabled, gap-up entries are held as swing trades.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Dict
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
import pandas as pd

from strategies.base_strategy import BaseStrategy
from core import alpaca_client as ac
from core import risk_manager as rm

BASE_DIR = Path(__file__).resolve().parent.parent
with open(BASE_DIR / "config" / "config.yaml") as f:
    CFG = yaml.safe_load(f)

SCFG           = CFG["strategies"]["gap_up"]
INTRADAY_ON    = CFG["intraday"]["enabled"]
ET             = ZoneInfo("America/New_York")
log            = logging.getLogger("strategy.gap_up")


def _within_entry_window() -> bool:
    """True if current time is within the entry window after market open (9:30 ET)."""
    now = datetime.now(ET)
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    minutes_since_open = (now - market_open).total_seconds() / 60
    return 0 <= minutes_since_open <= SCFG["entry_window_minutes"]


class GapUpStrategy(BaseStrategy):

    name        = "gap_up"
    asset_class = "stocks"

    def scan(self, universe: List[str], current_time: datetime = None, **kwargs) -> List[Dict]:
        if not SCFG["enabled"]:
            return []

        # If current_time is provided (backtesting), we assume it's at market open or within window
        if current_time is None and not _within_entry_window():
            log.debug("[GapUp] Outside entry window, skipping.")
            return []

        min_gap     = SCFG["min_gap_pct"]
        vol_mult    = SCFG["volume_multiplier"]
        max_gap     = 0.15 # 15% cap to avoid buying "exhaustion" gaps
        sma_long    = 200

        log.info(f"[GapUp] Scanning {len(universe)} symbols (min_gap={min_gap:.1%}, trend=SMA{sma_long})...")

        try:
            # Need 200 days for trend + 20 days for avg vol
            bars_data = ac.get_stock_bars(universe, timeframe="1Day", limit=sma_long + 10)
        except Exception as e:
            log.error(f"[GapUp] Failed to fetch bars: {e}")
            return []

        regime_bars = kwargs.get("regime_bars")
        if not rm.market_regime_ok(bars_data=regime_bars):
            log.info("[GapUp] Bear regime (SPY < SMA50), skipping scan.")
            return []

        signals = []

        for symbol in universe:
            try:
                bars = bars_data[symbol]
                if bars is None or len(bars) < sma_long + 1:
                    continue

                df = pd.DataFrame([{
                    "open":   float(b.open),
                    "high":   float(b.high),
                    "low":    float(b.low),
                    "close":  float(b.close),
                    "volume": float(b.volume),
                } for b in bars])

                prev_open    = df["open"].iloc[-2]
                prev_close   = df["close"].iloc[-2]
                prev_high    = df["high"].iloc[-2]
                today_open   = df["open"].iloc[-1]
                today_vol    = df["volume"].iloc[-1]
                avg_vol_20   = df["volume"].iloc[-21:-1].mean()
                sma200       = df["close"].rolling(window=sma_long).mean().iloc[-1]

                gap_pct = (today_open - prev_close) / prev_close

                # Refined Logic: 
                # 1. Gap within range (3% to 15%)
                # 2. High Volume (2x avg)
                # 3. Long-term Uptrend (Price > SMA200)
                # 4. Momentum Confirmation (Prev Day was Green: Close > Open)
                # 5. True Gap (Open is above Prev High)
                if min_gap <= gap_pct <= max_gap:
                    if (today_vol >= avg_vol_20 * vol_mult and
                        today_open > sma200 and
                        prev_close > prev_open):
                        # true gap boosts confidence
                        is_true_gap = today_open > prev_high
                        confidence = round(min(gap_pct / 0.08, 1.0) * (1.1 if is_true_gap else 1.0), 3)
                        signals.append({
                            "symbol":      symbol,
                            "action":      "buy",
                            "strategy":    self.name,
                            "asset_class": self.asset_class,
                            "confidence":  confidence,
                            "reason":      (
                                f"Gap-up {gap_pct:.2%} | vol={today_vol/avg_vol_20:.1f}x | "
                                f"Trend UP | Prev Day Green"
                                + (" | True Gap" if is_true_gap else "")
                            ),
                        })
                        log.info(
                            f"[GapUp] Signal: BUY {symbol} | gap={gap_pct:.2%} | "
                            f"vol={today_vol/avg_vol_20:.1f}x | SMA200={sma200:.2f}"
                        )

            except Exception as e:
                log.warning(f"[GapUp] Error for {symbol}: {e}")
                continue

        return signals

    def should_exit(self, symbol: str, entry_price: float) -> tuple:
        """
        If intraday mode is OFF (default): exit after hold_days only via trade log age.
        If intraday mode is ON: exit at end of day (handled by scheduler).
        Strategy itself doesn't force an intraday exit here.
        """
        return False, ""
