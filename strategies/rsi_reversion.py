"""
HawksTrade - RSI Mean Reversion Strategy (Stocks) — Conservative
=================================================================
Entry: RSI(14) < 30 (deeply oversold), %B < 20% (near lower Bollinger Band,
       20-period 2σ), volume ≥ 1.5× 20-day average, 1-bar price recovery,
       stock within 15% of SMA200.

Stop:  2 × ATR(14) below entry — volatility-adjusted, gives the trade room
       to breathe during capitulation. Global 3.5% stop remains as absolute
       floor; whichever is further below entry governs.

Exit:  FIRST of:
         (a) Price reaches the 20-day SMA  — mean reversion target achieved.
         (b) RSI(14) > 50                  — momentum neutralised, edge gone.
         (c) 10-day hold_days cap          — time exit.

Regime filters (both must pass):
  1. Crash filter  — skip if SPY >20% below its 252-day peak.
  2. VIX proxy     — skip if SPY realised HV(20) > its 200-day MA × 1.2.
                     Falls back gracefully; backtest regime_bars (60 bars)
                     are too short for this filter → always passes through.

ATR stop price is stored in each signal dict as "atr_stop_price" so the
backtest and live risk-check layers can use it instead of the global fixed
percentage stop.
"""

from __future__ import annotations

import logging
from typing import List, Dict
from pathlib import Path

import pandas as pd
import numpy as np

from strategies.base_strategy import BaseStrategy
from core import alpaca_client as ac
from core.config_loader import get_config

BASE_DIR = Path(__file__).resolve().parent.parent
CFG = get_config()

SCFG = CFG["strategies"]["rsi_reversion"]
log  = logging.getLogger("strategy.rsi_reversion")


def _in_severe_crash(bars_data=None) -> bool:
    """
    Returns True if SPY is more than 20% below its 252-day peak.
    Backtest warmup (< 20 bars) returns False. Live errors return True (fail closed).
    """
    try:
        if bars_data is not None:
            spy_bars = bars_data.get("SPY")
            if spy_bars is None or len(spy_bars) < 20:
                return False
            closes = pd.Series([
                float(b.close) if hasattr(b, "close") else float(b["close"])
                for b in spy_bars
            ])
        else:
            raw = ac.get_stock_bars(["SPY"], timeframe="1Day", limit=255)
            spy_bars = raw["SPY"]
            if spy_bars is None or len(spy_bars) < 20:
                log.warning("[RSI] Insufficient SPY bars for crash check — blocking (fail closed).")
                return True
            closes = pd.Series([b.close for b in spy_bars])

        peak = closes.rolling(min(252, len(closes))).max().iloc[-1]
        current = float(closes.iloc[-1])
        drawdown = 1.0 - (current / peak)
        in_crash = drawdown > 0.20
        log.debug(
            f"[RSI] Crash filter: SPY={current:.2f} peak={peak:.2f} "
            f"drawdown={drawdown:.1%} crash={in_crash}"
        )
        return in_crash
    except Exception as e:
        log.warning(f"[RSI] Crash filter error: {e} — blocking (fail closed).")
        return True


def _in_high_volatility_regime(
    bars_data=None,
    hv_period: int = 20,
    ma_period: int = 200,
    multiplier: float = 1.2,
) -> bool:
    """
    Returns True if SPY realised HV(hv_period) exceeds its ma_period-day MA × multiplier.
    Backtest regime_bars (60 bars) are insufficient for a 200-day MA → returns False.
    Live mode fetches SPY history directly; errors return True (fail closed).
    """
    required = hv_period + ma_period
    try:
        if bars_data is not None:
            spy_bars = bars_data.get("SPY")
            if spy_bars is None or len(spy_bars) < required:
                return False
            closes = pd.Series([
                float(b.close) if hasattr(b, "close") else float(b["close"])
                for b in spy_bars
            ])
        else:
            raw = ac.get_stock_bars(["SPY"], timeframe="1Day", limit=required + 10)
            spy_bars = raw["SPY"]
            if spy_bars is None or len(spy_bars) < required:
                log.warning("[RSI] Insufficient SPY bars for VIX filter — blocking (fail closed).")
                return True
            closes = pd.Series([b.close for b in spy_bars])

        returns   = closes.pct_change().dropna()
        hv_series = returns.rolling(hv_period).std() * np.sqrt(252)
        hv_now    = float(hv_series.iloc[-1])
        hv_ma     = float(hv_series.rolling(ma_period).mean().iloc[-1])

        if np.isnan(hv_now) or np.isnan(hv_ma) or hv_ma == 0:
            return False

        threshold   = hv_ma * multiplier
        in_high_vol = hv_now > threshold
        log.debug(
            f"[RSI] VIX filter: HV20={hv_now:.1%} HV_MA={hv_ma:.1%} "
            f"threshold={threshold:.1%} high_vol={in_high_vol}"
        )
        return in_high_vol
    except Exception as e:
        log.warning(f"[RSI] VIX filter error: {e} — blocking (fail closed).")
        return True


def _calc_rsi(closes: pd.Series, period: int = 14) -> float:
    """Compute RSI for a price series, return the latest value."""
    delta  = closes.diff()
    gain   = delta.where(delta > 0, 0.0)
    loss   = -delta.where(delta < 0, 0.0)
    avg_g  = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_l  = loss.ewm(com=period - 1, min_periods=period).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        rs  = avg_g / avg_l
        rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])


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


def _bollinger_lower(closes: pd.Series, period: int = 20, n_std: float = 2.0) -> float:
    """Return the lower Bollinger Band value for the latest bar."""
    sma = closes.rolling(period).mean().iloc[-1]
    std = closes.rolling(period).std().iloc[-1]
    return float(sma - n_std * std)


def _bollinger_pct_b(closes: pd.Series, period: int = 20, n_std: float = 2.0) -> float:
    """
    Return %B = (price - lower) / (upper - lower).
    %B = 0  → price at lower band
    %B = 1  → price at upper band
    %B < 0  → price below lower band
    Returns 0.5 when bandwidth is zero (flat market).
    """
    sma = closes.rolling(period).mean().iloc[-1]
    std = closes.rolling(period).std().iloc[-1]
    lower = sma - n_std * std
    upper = sma + n_std * std
    bandwidth = upper - lower
    if bandwidth == 0:
        return 0.5
    return float((float(closes.iloc[-1]) - lower) / bandwidth)


class RSIReversionStrategy(BaseStrategy):

    name        = "rsi_reversion"
    asset_class = "stocks"

    def scan(self, universe: List[str], **kwargs) -> List[Dict]:
        if not SCFG["enabled"]:
            return []

        period       = SCFG["rsi_period"]
        oversold     = SCFG["oversold_threshold"]
        bb_period    = SCFG.get("bb_period", 20)
        bb_std       = SCFG.get("bb_std", 2.0)
        vix_mult     = SCFG.get("vix_multiplier", 1.2)
        atr_period   = SCFG.get("atr_period", 14)
        atr_mult     = SCFG.get("atr_multiplier", 2.0)

        log.info(
            f"[RSI] Scanning {len(universe)} symbols "
            f"(RSI<{oversold}, %B<20%, vol>1.5×, 1-bar recovery, SMA200±15%, "
            f"2×ATR stop, exit@SMA20 or RSI>50)..."
        )

        try:
            bars_data = ac.get_stock_bars(universe, timeframe="1Day", limit=210)
        except Exception as e:
            log.error(f"[RSI] Failed to fetch bars: {e}")
            return []

        regime_bars = kwargs.get("regime_bars")

        if _in_severe_crash(bars_data=regime_bars):
            log.info("[RSI] Severe crash (SPY >20% below 252d peak) — skipping scan.")
            return []

        if _in_high_volatility_regime(bars_data=regime_bars, multiplier=vix_mult):
            log.info("[RSI] Elevated volatility regime (HV20 > HV_MA×1.2) — skipping scan.")
            return []

        signals = []

        for symbol in universe:
            try:
                bars = bars_data[symbol]
                if bars is None or len(bars) < 201:
                    continue

                closes = pd.Series([b.close for b in bars])
                rsi    = _calc_rsi(closes, period)
                sma200 = closes.rolling(window=200).mean().iloc[-1]
                price  = float(bars[-1].close)

                avg_vol_20 = pd.Series([b.volume for b in bars]).iloc[-21:-1].mean()
                today_vol  = float(bars[-1].volume)
                vol_ratio  = today_vol / avg_vol_20 if avg_vol_20 > 0 else 0.0

                not_broken_down = price > sma200 * 0.85

                if not (rsi < oversold and not_broken_down and vol_ratio >= 1.5):
                    continue

                if len(closes) >= bb_period:
                    pct_b = _bollinger_pct_b(closes, bb_period, bb_std)
                    near_lower_bb = pct_b < 0.20
                else:
                    near_lower_bb = False

                if not near_lower_bb:
                    log.debug(
                        f"[RSI] {symbol} skipped — %B={pct_b:.2%} not in lower quintile (need <20%)"
                    )
                    continue

                if len(bars) >= 2:
                    c_prev = float(bars[-2].close) if hasattr(bars[-2], "close") else float(bars[-2]["close"])
                    c_last = float(bars[-1].close) if hasattr(bars[-1], "close") else float(bars[-1]["close"])
                    recovering = c_last > c_prev
                else:
                    recovering = False

                if not recovering:
                    log.debug(f"[RSI] {symbol} skipped — no 1-bar recovery (last close not above prior)")
                    continue

                # ATR-based stop: gives the trade room to breathe in high-vol conditions
                atr           = _calc_atr(bars, atr_period)
                atr_stop      = round(price - atr_mult * atr, 4)
                lower_band    = _bollinger_lower(closes, bb_period, bb_std)

                signals.append({
                    "symbol":         symbol,
                    "action":         "buy",
                    "strategy":       self.name,
                    "asset_class":    self.asset_class,
                    "confidence":     round((oversold - rsi) / oversold, 3),
                    "atr_stop_price": atr_stop,
                    "reason": (
                        f"RSI={rsi:.1f}<{oversold}, %B={pct_b:.2%} (lower-BB={lower_band:.2f}), "
                        f"vol={vol_ratio:.1f}×, ATR={atr:.2f} stop@{atr_stop:.2f}"
                    ),
                })
                log.info(
                    f"[RSI] Signal: BUY {symbol} | RSI={rsi:.1f} | "
                    f"%B={pct_b:.2%} | vol={vol_ratio:.1f}× | ATR={atr:.2f} | stop@{atr_stop:.2f}"
                )

            except Exception as e:
                log.warning(f"[RSI] Error for {symbol}: {e}")
                continue

        return signals

    def should_exit(self, symbol: str, entry_price: float) -> tuple:
        """
        Exit when the mean-reversion edge is gone:
          (a) price reaches the 20-day SMA  — target achieved
          (b) RSI(14) > 50                  — momentum neutral, edge evaporated
        The 10-day hold cap is enforced externally by the hold_days mechanism.
        """
        period    = SCFG["rsi_period"]
        bb_period = SCFG.get("bb_period", 20)

        limit = max(period + 10, bb_period + 5)
        try:
            bars_data = ac.get_stock_bars([symbol], timeframe="1Day", limit=limit)
            bars      = bars_data[symbol]
            if bars is None or len(bars) < period + 1:
                return False, ""

            closes    = pd.Series([b.close for b in bars])
            price     = float(closes.iloc[-1])
            rsi       = _calc_rsi(closes, period)
            sma20     = float(closes.rolling(bb_period).mean().iloc[-1])

            if price >= sma20:
                return True, f"Mean target reached: {price:.2f} >= SMA20={sma20:.2f}"

            if rsi > 50:
                return True, f"RSI neutral: {rsi:.1f} > 50 — edge evaporated"

        except Exception as e:
            log.warning(f"[RSI] Exit check error for {symbol}: {e}")

        return False, ""
