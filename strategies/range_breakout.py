"""
HawksTrade - Range Breakout Strategy (Crypto)
==============================================
Enters long when price breaks above the prior day's high
with volume confirmation. Holds for hold_days days.

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

SCFG = CFG["strategies"]["range_breakout"]
log  = logging.getLogger("strategy.range_breakout")


class RangeBreakoutStrategy(BaseStrategy):

    name        = "range_breakout"
    asset_class = "crypto"

    def scan(self, universe: List[str]) -> List[Dict]:
        if not SCFG["enabled"]:
            return []

        breakout_pct = SCFG["breakout_pct"]
        vol_mult     = SCFG["volume_multiplier"]
        timeframe    = SCFG["timeframe"]

        log.info(f"[Breakout] Scanning {len(universe)} crypto pairs...")

        signals = []

        try:
            bars_data = ac.get_crypto_bars(universe, timeframe=timeframe, limit=25)
        except Exception as e:
            log.error(f"[Breakout] Failed to fetch bars: {e}")
            return []

        for symbol in universe:
            try:
                bars = bars_data[symbol]
                if bars is None or len(bars) < 22:
                    continue

                df = pd.DataFrame([{
                    "high":   b.high,
                    "low":    b.low,
                    "close":  b.close,
                    "volume": b.volume,
                } for b in bars])

                prev_high  = df["high"].iloc[-2]
                today_cls  = df["close"].iloc[-1]
                today_vol  = df["volume"].iloc[-1]
                avg_vol    = df["volume"].iloc[-21:-1].mean()

                breakout_level = prev_high * (1 + breakout_pct)

                if today_cls >= breakout_level and today_vol >= avg_vol * vol_mult:
                    excess_pct = (today_cls - prev_high) / prev_high
                    signals.append({
                        "symbol":      symbol,
                        "action":      "buy",
                        "strategy":    self.name,
                        "asset_class": self.asset_class,
                        "confidence":  round(min(excess_pct / 0.02, 1.0), 3),
                        "reason":      (
                            f"Breakout above prior high {prev_high:.4f} | "
                            f"close={today_cls:.4f} | vol={today_vol/avg_vol:.1f}x avg"
                        ),
                    })
                    log.info(
                        f"[Breakout] Signal: BUY {symbol} | "
                        f"close={today_cls:.4f} > prev_high={prev_high:.4f} "
                        f"vol={today_vol/avg_vol:.1f}x"
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
