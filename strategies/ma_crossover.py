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

import pandas as pd

from strategies.base_strategy import BaseStrategy
from strategies.rsi_reversion import _calc_rsi, _calc_atr
from core import alpaca_client as ac
from core import risk_manager as rm
from core.config_loader import get_config

CFG = get_config()
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

        fast_span        = SCFG["fast_ema"]
        slow_span        = SCFG["slow_ema"]
        timeframe        = SCFG["timeframe"]
        atr_period       = SCFG.get("atr_period", 14)
        atr_mult         = SCFG.get("atr_multiplier", 2.0)
        rsi_min          = int(SCFG.get("rsi_entry_min", 35))
        rsi_max          = int(SCFG.get("rsi_entry_max", 70))
        risk_pct         = float(SCFG.get("risk_per_trade_pct", 0.01))
        vol_spike_ratio  = float(SCFG.get("volume_spike_ratio", 1.2))
        vol_avg_period   = int(SCFG.get("volume_avg_period", 20))
        vol_filter_period = int(SCFG.get("vol_filter_period", 10))
        min_trade_value  = float(CFG["trading"].get("min_trade_value_usd", 100))

        log.info(f"[MACross] Scanning {len(universe)} crypto pairs "
                 f"(EMA {fast_span}/{slow_span}, {timeframe})...")

        signals = []

        try:
            bars_data = ac.get_crypto_bars(universe, timeframe=timeframe, limit=max(slow_span, atr_period) + 100)
        except Exception as e:
            log.error(f"[MACross] Failed to fetch bars: {e}")
            return []

        regime_bars = kwargs.get("regime_bars")
        if not rm.crypto_regime_ok(bars_data=regime_bars):
            log.info("[MACross] Crypto bear regime (BTC < EMA20), skipping scan.")
            return []

        try:
            portfolio_equity = ac.get_portfolio_value()
        except Exception:
            portfolio_equity = 0.0

        for symbol in universe:
            try:
                bars = _bars_for_symbol(bars_data, symbol)
                if bars is None or len(bars) < max(slow_span, atr_period, vol_avg_period, vol_filter_period) + 5:
                    continue

                closes = pd.Series([
                    float(b.close) if hasattr(b, "close") else float(b["close"])
                    for b in bars
                ])
                fast    = _ema(closes, fast_span)
                slow    = _ema(closes, slow_span)
                cross   = _detect_crossover(fast, slow)

                # Slope Filter: Ensure slow EMA is actually trending up over last 4 periods
                # Fix BUG-004: Added length check for iloc[-5]
                is_trending_up = (len(slow) >= 5) and (slow.iloc[-1] > slow.iloc[-5])

                # Volatility Filter: Ensure we aren't in a completely dead market
                # Current range must be at least 0.5x the N-day average range
                highs  = pd.Series([
                    float(b.high) if hasattr(b, "high") else float(b["high"])
                    for b in bars
                ])
                lows   = pd.Series([
                    float(b.low) if hasattr(b, "low") else float(b["low"])
                    for b in bars
                ])
                ranges = highs - lows
                
                # Fix BUG-013: Use configurable vol_filter_period
                avg_range = ranges.iloc[-(vol_filter_period + 1):-1].mean()
                curr_range = highs.iloc[-1] - lows.iloc[-1]
                is_volatile = curr_range >= (avg_range * 0.5)

                # Fix BUG-014: Volume spike confirmation
                volumes = pd.Series([
                    float(b.volume) if hasattr(b, "volume") else float(b["volume"])
                    for b in bars
                ])
                avg_vol = volumes.iloc[-(vol_avg_period + 1):-1].mean()
                curr_vol = volumes.iloc[-1]
                volume_ok = (avg_vol > 0 and curr_vol >= vol_spike_ratio * avg_vol) if vol_spike_ratio > 0 else True

                fast_v  = float(fast.iloc[-1])
                slow_v  = float(slow.iloc[-1])
                price   = float(closes.iloc[-1])

                rsi_val = _calc_rsi(closes, 14)

                if cross == "bullish" and is_trending_up and is_volatile and volume_ok and rsi_min <= rsi_val <= rsi_max:
                    atr = _calc_atr(bars, atr_period)
                    atr_stop = round(price - atr_mult * atr, 4)

                    # Guard against zero slow EMA (theoretical edge case on micro-cap assets)
                    ema_divergence = abs(fast_v - slow_v) / slow_v if slow_v != 0 else 0.0

                    atr_risk_qty = None
                    if atr > 0 and atr_stop < price and portfolio_equity > 0:
                        risk_dollars   = portfolio_equity * risk_pct
                        risk_per_share = price - atr_stop
                        if risk_per_share > 0:
                            atr_risk_qty = round(risk_dollars / risk_per_share, 6)
                            if atr_risk_qty * price < min_trade_value:
                                log.info(
                                    f"[MACross] {symbol} ATR-risk quantity {atr_risk_qty} "
                                    f"(${atr_risk_qty * price:.2f}) is below min ${min_trade_value}. "
                                    "Skipping signal."
                                )
                                continue

                    sig = {
                        "symbol":         symbol,
                        "action":         "buy",
                        "strategy":       self.name,
                        "asset_class":    self.asset_class,
                        "confidence":     round(min(ema_divergence * 10, 1.0), 3),
                        "atr_stop_price": atr_stop,
                        "reason":         f"BULLISH {fast_span}/{slow_span} EMA Crossover | Slope UP | Vol Confirm | RSI={rsi_val:.1f} | ATR Stop={atr_stop}",
                    }
                    if atr_risk_qty is not None:
                        sig["atr_risk_qty"] = atr_risk_qty

                    signals.append(sig)
                    log.info(
                        f"[MACross] BULLISH crossover on {symbol} | Trend UP | Vol Confirm | "
                        f"RSI={rsi_val:.1f} | ATR Stop={atr_stop} | risk_qty={atr_risk_qty}"
                    )

            except Exception as e:
                log.warning(f"[MACross] Error for {symbol}: {e}")
                continue

        return signals

    def should_exit(self, symbol: str, entry_price: float) -> tuple:
        """Exit when fast EMA crosses below slow EMA or RSI is overbought."""
        fast_span = SCFG["fast_ema"]
        slow_span = SCFG["slow_ema"]
        timeframe = SCFG["timeframe"]
        rsi_exit_max = int(SCFG.get("rsi_exit_max", 70))

        try:
            bars_data = ac.get_crypto_bars([symbol], timeframe=timeframe, limit=slow_span + 20)
            bars      = _bars_for_symbol(bars_data, symbol)
            if bars is None or len(bars) < max(slow_span, 15) + 2:
                return False, ""

            closes = pd.Series([
                float(b.close) if hasattr(b, "close") else float(b["close"])
                for b in bars
            ])
            fast   = _ema(closes, fast_span)
            slow   = _ema(closes, slow_span)
            cross  = _detect_crossover(fast, slow)

            if cross == "bearish":
                return True, f"EMA {fast_span} crossed below EMA {slow_span}"

            # Fix BUG-005: RSI overbought exit
            rsi_val = _calc_rsi(closes, 14)
            if rsi_val > rsi_exit_max:
                return True, f"RSI overbought: {rsi_val:.1f} > {rsi_exit_max}"

        except Exception as e:
            log.warning(f"[MACross] Exit check error for {symbol}: {e}")

        return False, ""
