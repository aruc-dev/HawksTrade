"""
HawksTrade - RSI Mean Reversion Strategy (Stocks) — Conservative
=================================================================
Entry: RSI(14) < 30 (deeply oversold), price at/below lower Bollinger Band
       (20-period, 2σ), volume ≥ 1.5× 20-day average, 1-bar price recovery,
       stock within 15% of SMA200.
Exit:  RSI(14) > 70 (overbought) OR hold_days cap (10 business days).

Regime filters (both must pass):
  1. Crash filter  — skip if SPY >20% below its 252-day peak.
  2. VIX proxy     — skip if SPY realised HV(20) > its 200-day MA × 1.2,
                     indicating an elevated-volatility regime. Falls back
                     gracefully when SPY history is insufficient (backtest
                     warmup uses regime_bars which only contains 60 bars;
                     filter returns False / allow in that case).

Strategy: Swing trade (NOT intraday). Hard exit enforced by hold_days.
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

    Blocks entries only during genuine market crashes, not routine corrections.
    Backtest warmup (< 20 bars) returns False (allow). Live errors return True
    (fail closed).
    """
    try:
        if bars_data is not None:
            spy_bars = bars_data.get("SPY")
            if spy_bars is None or len(spy_bars) < 20:
                return False  # backtest warmup — allow trading
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
    Returns True if SPY realised volatility HV(hv_period) exceeds its
    ma_period-day moving average × multiplier — an elevated-volatility regime.

    Uses SPY daily returns as a VIX proxy. Backtest regime_bars typically
    contain only 60 bars, which is insufficient for a 200-day MA; in that
    case the filter returns False (allow trading) so backtest warmup is
    unaffected. Live mode fetches its own SPY history and fails closed.
    """
    required = hv_period + ma_period
    try:
        if bars_data is not None:
            spy_bars = bars_data.get("SPY")
            if spy_bars is None or len(spy_bars) < required:
                return False  # insufficient history — allow (backtest warmup)
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
            return False  # insufficient history — allow

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

        period     = SCFG["rsi_period"]
        oversold   = SCFG["oversold_threshold"]
        bb_period  = SCFG.get("bb_period", 20)
        bb_std     = SCFG.get("bb_std", 2.0)
        vix_mult   = SCFG.get("vix_multiplier", 1.2)

        log.info(
            f"[RSI] Scanning {len(universe)} symbols "
            f"(RSI<{oversold}, lower-BB, vol>1.5×, 1-bar recovery, SMA200±15%, crash+VIX filter)..."
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

                # Volume filter: today ≥ 1.5× 20-day average
                avg_vol_20 = pd.Series([b.volume for b in bars]).iloc[-21:-1].mean()
                today_vol  = float(bars[-1].volume)
                vol_ratio  = today_vol / avg_vol_20 if avg_vol_20 > 0 else 0.0

                # SMA200 trend filter: not more than 15% below 200-day MA
                not_broken_down = price > sma200 * 0.85

                if not (rsi < oversold and not_broken_down and vol_ratio >= 1.5):
                    continue

                # Bollinger Band filter: price must be in the lower 20% of the
                # BB range (%B < 0.2). Using %B rather than a strict lower-band
                # breach because during volatile selloffs the band widens faster
                # than price falls — "near the lower band" is the correct signal.
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

                # 1-bar recovery: last close must exceed the prior close
                if len(bars) >= 2:
                    c_prev = float(bars[-2].close) if hasattr(bars[-2], "close") else float(bars[-2]["close"])
                    c_last = float(bars[-1].close) if hasattr(bars[-1], "close") else float(bars[-1]["close"])
                    recovering = c_last > c_prev
                else:
                    recovering = False

                if not recovering:
                    log.debug(f"[RSI] {symbol} skipped — no 1-bar recovery (last close not above prior)")
                    continue

                lower_band = _bollinger_lower(closes, bb_period, bb_std)
                signals.append({
                    "symbol":      symbol,
                    "action":      "buy",
                    "strategy":    self.name,
                    "asset_class": self.asset_class,
                    "confidence":  round((oversold - rsi) / oversold, 3),
                    "reason": (
                        f"RSI={rsi:.1f}<{oversold}, %B={pct_b:.2%} (lower-BB={lower_band:.2f}), "
                        f"vol={vol_ratio:.1f}×, SMA200={sma200:.2f}, 1-bar recovery"
                    ),
                })
                log.info(
                    f"[RSI] Signal: BUY {symbol} | RSI={rsi:.1f} | "
                    f"%B={pct_b:.2%} lower-BB={lower_band:.2f} | vol={vol_ratio:.1f}× | SMA200={sma200:.2f}"
                )

            except Exception as e:
                log.warning(f"[RSI] Error for {symbol}: {e}")
                continue

        return signals

    def should_exit(self, symbol: str, entry_price: float) -> tuple:
        """Exit when RSI rises above the overbought threshold (conservative: 70)."""
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
