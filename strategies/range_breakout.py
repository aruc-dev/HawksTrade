"""
HawksTrade - Range Breakout Strategy (Crypto)
==============================================
Enters long when price confirms a breakout above the configured
prior N-bar Donchian high, excluding the current bar. The default
profile requires a 20-day high breakout, 2.0x volume confirmation,
trend, volatility, RSI, and extension guards. Failed breakouts can
exit before the 14-day hold cap.

Works 24/7 on crypto pairs.
"""

from __future__ import annotations

import logging
from typing import List, Dict
import math

import pandas as pd
import numpy as np

from strategies.base_strategy import BaseStrategy
from strategies.atr_sizing import atr_stop_and_qty
from strategies.rsi_reversion import _calc_atr, _calc_rsi
from core import alpaca_client as ac
from core import risk_manager as rm
from core.config_loader import get_config

CFG = get_config()

SCFG = CFG["strategies"]["range_breakout"]
log  = logging.getLogger("strategy.range_breakout")


def _symbol_lookup_keys(symbol: str):
    """Return plausible crypto data keys for slashed and slashless symbols."""
    raw_symbol = str(symbol or "").strip().upper()
    pair_symbol = ac.to_crypto_pair_symbol(raw_symbol)
    normalized_symbol = ac.normalize_symbol(pair_symbol)
    return tuple(dict.fromkeys((pair_symbol, raw_symbol, normalized_symbol)))


def _bars_for_symbol(bars_data, symbol: str):
    """Return bars for slashed or slashless crypto symbols without raising on missing data."""
    for key in _symbol_lookup_keys(symbol):
        try:
            bars = bars_data[key]
        except (AttributeError, KeyError, TypeError):
            bars = None
        if bars is not None:
            return bars
    return None


def _bar_float(bar, field: str) -> float:
    if hasattr(bar, field):
        return float(getattr(bar, field))
    return float(bar[field])


def _bars_to_frame(bars) -> pd.DataFrame:
    """Convert SDK/dict bars to a numeric OHLCV frame."""
    df = pd.DataFrame({
        "high":   [_bar_float(b, "high") for b in bars],
        "low":    [_bar_float(b, "low") for b in bars],
        "close":  [_bar_float(b, "close") for b in bars],
        "volume": [_bar_float(b, "volume") for b in bars],
    })
    return df.replace([np.inf, -np.inf], np.nan)


def _has_tradeable_ohlcv(df: pd.DataFrame, required_bars: int) -> bool:
    window = df.tail(required_bars)
    if len(window) < required_bars or window.isna().any().any():
        return False
    if (window[["high", "low", "close"]] <= 0).any().any():
        return False
    if (window["high"] < window["low"]).any():
        return False
    if (window["close"] > window["high"]).any() or (window["close"] < window["low"]).any():
        return False
    if (window["volume"] < 0).any():
        return False
    return True


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    value = numerator / denominator
    return value if math.isfinite(value) else 0.0


class RangeBreakoutStrategy(BaseStrategy):

    name        = "range_breakout"
    asset_class = "crypto"

    def scan(self, universe: List[str], **kwargs) -> List[Dict]:
        if not SCFG["enabled"]:
            return []

        breakout_pct       = float(SCFG.get("breakout_pct", 0.008))
        breakout_lookback  = max(1, int(SCFG.get("breakout_lookback_days", 1)))
        max_extension_pct  = float(SCFG.get("max_breakout_extension_pct", 0.08))
        vol_mult           = float(SCFG.get("volume_multiplier", 1.8))
        timeframe          = SCFG.get("timeframe", "1Day")
        risk_pct           = float(SCFG.get("risk_per_trade_pct", 0.01))
        atr_period         = int(SCFG.get("atr_period", 14))
        atr_mult           = float(SCFG.get("atr_multiplier", 2.0))
        vol_filter_period  = int(SCFG.get("vol_filter_period", 10))
        min_range_ratio    = float(SCFG.get("min_range_ratio", 0.5))
        vol_avg_period     = int(SCFG.get("volume_avg_period", 20))
        trend_ema_period   = int(SCFG.get("trend_ema_period", 50))
        trend_slope_lookback = int(SCFG.get("trend_slope_lookback", 5))
        rsi_period         = int(SCFG.get("rsi_period", 14))
        rsi_entry_max      = float(SCFG.get("rsi_entry_max", 78))
        min_trade_value    = float(CFG["trading"].get("min_trade_value_usd", 100))

        log.info(f"[Breakout] Scanning {len(universe)} crypto pairs...")

        signals = []
        required_bars = max(
            trend_ema_period + trend_slope_lookback + 1,
            atr_period + 1,
            vol_avg_period + 1,
            vol_filter_period + 1,
            rsi_period + 1,
            breakout_lookback + 1,
            52,
        )

        try:
            limit = max(80, required_bars + 10)
            bars_data = ac.get_crypto_bars(universe, timeframe=timeframe, limit=limit)
        except Exception as e:
            log.error(f"[Breakout] Failed to fetch bars: {e}")
            return []

        regime_bars = kwargs.get("regime_bars")
        if not rm.crypto_regime_ok(
            bars_data=regime_bars,
            allow_warmup=bool(kwargs.get("allow_regime_warmup", False)),
        ):
            log.info("[Breakout] Crypto bear regime (BTC < EMA20), skipping scan.")
            return []

        portfolio_equity = None

        for symbol in universe:
            try:
                bars = _bars_for_symbol(bars_data, symbol)
                if bars is None or len(bars) < required_bars:
                    continue

                df = _bars_to_frame(bars)
                if not _has_tradeable_ohlcv(df, required_bars):
                    log.debug(f"[Breakout] {symbol} skipped: invalid or incomplete OHLCV window.")
                    continue

                breakout_high = float(df["high"].iloc[-(breakout_lookback + 1):-1].max())
                today_cls  = df["close"].iloc[-1]
                today_vol  = df["volume"].iloc[-1]
                avg_vol    = df["volume"].iloc[-(vol_avg_period + 1):-1].mean()
                trend_ema  = df["close"].ewm(span=trend_ema_period, adjust=False).mean()
                trend_now  = float(trend_ema.iloc[-1])
                trend_then = float(trend_ema.iloc[-(trend_slope_lookback + 1)])
                trend_ok   = today_cls > trend_now and trend_now >= trend_then

                ranges = df["high"] - df["low"]
                if vol_filter_period > 0 and min_range_ratio > 0:
                    avg_range = ranges.iloc[-(vol_filter_period + 1):-1].mean()
                    curr_range = df["high"].iloc[-1] - df["low"].iloc[-1]
                    is_volatile = avg_range > 0 and curr_range >= (avg_range * min_range_ratio)
                else:
                    is_volatile = True

                volume_ok = (
                    avg_vol > 0 and today_vol >= avg_vol * vol_mult
                ) if vol_mult > 0 else True

                breakout_level = breakout_high * (1 + breakout_pct)
                broke_out = today_cls >= breakout_level
                extension_pct = _safe_ratio(today_cls - breakout_level, breakout_level)
                extension_ok = max_extension_pct <= 0 or extension_pct <= max_extension_pct
                rsi_val = _calc_rsi(df["close"], rsi_period)
                rsi_ok = rsi_val <= rsi_entry_max
                vol_ratio = _safe_ratio(today_vol, avg_vol)
                trend_spread = _safe_ratio(today_cls - trend_now, trend_now)

                if broke_out and volume_ok and trend_ok and is_volatile and extension_ok and rsi_ok:
                    if portfolio_equity is None:
                        try:
                            portfolio_equity = ac.get_portfolio_value()
                        except Exception as e:
                            log.error(f"[Breakout] Could not fetch portfolio value for ATR-risk sizing; skipping signals: {e}")
                            return []

                    price = float(today_cls)
                    atr = _calc_atr(bars, atr_period)
                    sized = atr_stop_and_qty(
                        symbol=symbol,
                        price=price,
                        atr=atr,
                        atr_multiplier=atr_mult,
                        portfolio_equity=portfolio_equity,
                        risk_per_trade_pct=risk_pct,
                        min_trade_value=min_trade_value,
                        logger=log,
                        prefix="[Breakout]",
                    )
                    if sized is None:
                        continue
                    atr_stop, atr_risk_qty = sized

                    excess_pct = _safe_ratio(today_cls - breakout_high, breakout_high)
                    confidence = min(
                        1.0,
                        (0.45 * min(excess_pct / max(breakout_pct, 0.001), 2.0) / 2.0)
                        + (0.35 * min(vol_ratio / max(vol_mult, 1.0), 2.0) / 2.0)
                        + (0.20 * min(max(trend_spread, 0.0) / 0.05, 1.0)),
                    )
                    signals.append({
                        "symbol":         symbol,
                        "action":         "buy",
                        "strategy":       self.name,
                        "asset_class":    self.asset_class,
                        "confidence":     round(confidence, 3),
                        "atr_stop_price": atr_stop,
                        "atr_risk_qty":   atr_risk_qty,
                        "reason":         (
                            f"Breakout close {today_cls:.4f} above level {breakout_level:.4f} | "
                            f"{breakout_lookback}d high={breakout_high:.4f} | "
                            f"vol={vol_ratio:.1f}x avg | EMA{trend_ema_period} rising | "
                            f"RSI={rsi_val:.1f} | ATR Stop={atr_stop}"
                        ),
                    })
                    log.info(
                        f"[Breakout] Signal: BUY {symbol} | qty={atr_risk_qty} | stop={atr_stop} | "
                        f"close={today_cls:.4f} > level={breakout_level:.4f} "
                        f"vol={vol_ratio:.1f}x | EMA{trend_ema_period} rising | RSI={rsi_val:.1f}"
                    )

            except Exception as e:
                log.warning(f"[Breakout] Error for {symbol}: {e}")
                continue

        return sorted(signals, key=lambda sig: sig.get("confidence", 0), reverse=True)

    def should_exit(self, symbol: str, entry_price: float) -> tuple:
        """
        Exit failed breakouts before the hold-days cap when the completed daily
        close invalidates the breakout or the trend filter fails.
        """
        timeframe = SCFG.get("timeframe", "1Day")
        trend_ema_period = int(SCFG.get("trend_ema_period", 50))
        breakdown_exit_pct = float(SCFG.get("breakdown_exit_pct", 0.02))
        trend_exit_enabled = bool(SCFG.get("trend_exit_enabled", True))
        rsi_period = int(SCFG.get("rsi_period", 14))
        rsi_exit_max = float(SCFG.get("rsi_exit_max", 82))
        profit_floor_pct = float(SCFG.get("profit_floor_pct", 0.03))
        limit = max(trend_ema_period + 10, rsi_period + 5, 60)

        try:
            bars_data = ac.get_crypto_bars([symbol], timeframe=timeframe, limit=limit)
            bars = _bars_for_symbol(bars_data, symbol)
            if bars is None or len(bars) < max(trend_ema_period, rsi_period) + 2:
                return False, ""

            df = _bars_to_frame(bars)
            if not _has_tradeable_ohlcv(df, max(trend_ema_period, rsi_period) + 2):
                return False, ""

            close = float(df["close"].iloc[-1])
            if close <= entry_price * (1 - breakdown_exit_pct):
                return True, (
                    f"Range breakout failed: close {close:.4f} <= "
                    f"entry {entry_price:.4f} - {breakdown_exit_pct:.1%}"
                )

            trend_ema = df["close"].ewm(span=trend_ema_period, adjust=False).mean().iloc[-1]
            if trend_exit_enabled and close < float(trend_ema):
                return True, f"Range breakout trend failed: close {close:.4f} < EMA{trend_ema_period} {trend_ema:.4f}"

            rsi_val = _calc_rsi(df["close"], rsi_period)
            if close >= entry_price * (1 + profit_floor_pct) and rsi_val >= rsi_exit_max:
                return True, f"Range breakout exhaustion: RSI {rsi_val:.1f} >= {rsi_exit_max:.1f}"

        except Exception as e:
            log.warning(f"[Breakout] Exit check error for {symbol}: {e}")

        return False, ""
