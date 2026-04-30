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
import math
from datetime import datetime, time, timezone
from typing import List, Dict
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from strategies.base_strategy import BaseStrategy
from strategies.rsi_reversion import _calc_atr
from core import alpaca_client as ac
from core import risk_manager as rm
from core.config_loader import get_config

CFG = get_config()

SCFG           = CFG["strategies"]["gap_up"]
INTRADAY_ON    = CFG["intraday"]["enabled"]
ET             = ZoneInfo("America/New_York")
log            = logging.getLogger("strategy.gap_up")


def _bar_float(bar, field: str) -> float:
    if hasattr(bar, field):
        return float(getattr(bar, field))
    return float(bar[field])


def _bars_to_frame(bars) -> pd.DataFrame:
    """Convert SDK/dict bars to a numeric OHLCV frame."""
    df = pd.DataFrame({
        "open":   [_bar_float(b, "open") for b in bars],
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
    if (window[["open", "high", "low", "close"]] <= 0).any().any():
        return False
    if (window["high"] < window["low"]).any():
        return False
    if (window["open"] > window["high"]).any() or (window["open"] < window["low"]).any():
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


def _bar_timestamp(bar):
    if hasattr(bar, "timestamp"):
        return getattr(bar, "timestamp")
    if isinstance(bar, dict):
        return bar.get("timestamp")
    return None


def _parse_bar_timestamp(value):
    if isinstance(value, datetime):
        ts = value
    elif isinstance(value, str):
        try:
            ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _as_et(value: datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(ET)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ET)


def _minutes_since_open(now_et: datetime) -> float:
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    return (now_et - market_open).total_seconds() / 60


def _entry_elapsed_minutes(current_time: datetime | None, entry_window_minutes: int) -> float | None:
    """Return elapsed regular-session minutes, or None when a live scan is outside the window."""
    now_et = _as_et(current_time)
    minutes_since_open = _minutes_since_open(now_et)
    if current_time is None and not 0 <= minutes_since_open <= entry_window_minutes:
        return None
    if not 0 <= minutes_since_open <= entry_window_minutes:
        # Backtests pass a daily timestamp; evaluate as if the scan ran at the
        # configured opening window instead of rejecting every historical date.
        minutes_since_open = float(entry_window_minutes)
    return max(1.0, min(float(minutes_since_open), float(entry_window_minutes)))


def _bars_for_symbol(bars_data, symbol: str):
    try:
        return bars_data[symbol]
    except (AttributeError, KeyError, TypeError):
        return None


def _completed_daily_history(bars, current_time: datetime | None):
    """Drop the current regular-session daily bar when timestamps make it identifiable."""
    if not bars:
        return bars
    session_date = _as_et(current_time).date()
    completed = []
    saw_timestamps = False
    for bar in bars:
        ts = _parse_bar_timestamp(_bar_timestamp(bar))
        if ts is None:
            completed.append(bar)
            continue
        saw_timestamps = True
        if ts.astimezone(ET).date() < session_date:
            completed.append(bar)
    return completed if saw_timestamps else bars


def _session_opening_metrics(bars, current_time: datetime | None) -> dict | None:
    if not bars:
        return None

    now_et = _as_et(current_time)
    session_date = now_et.date()
    session_start = datetime.combine(session_date, time(9, 30), tzinfo=ET)
    usable = []
    fallback = []

    for bar in bars:
        ts = _parse_bar_timestamp(_bar_timestamp(bar))
        if ts is None:
            fallback.append((None, bar))
            continue
        ts_et = ts.astimezone(ET)
        if session_start <= ts_et <= now_et:
            usable.append((ts_et, bar))

    if not usable and fallback:
        usable = fallback
    if not usable:
        return None

    usable.sort(key=lambda item: item[0] or datetime.min.replace(tzinfo=ET))
    ordered_bars = [bar for _, bar in usable]
    try:
        session_open = _bar_float(ordered_bars[0], "open")
        latest_price = _bar_float(ordered_bars[-1], "close")
        opening_volume = sum(_bar_float(bar, "volume") for bar in ordered_bars)
        opening_high = max(_bar_float(bar, "high") for bar in ordered_bars)
        opening_low = min(_bar_float(bar, "low") for bar in ordered_bars)
    except (TypeError, ValueError, KeyError):
        return None

    values = [session_open, latest_price, opening_volume, opening_high, opening_low]
    if any(not math.isfinite(v) for v in values):
        return None
    if session_open <= 0 or latest_price <= 0 or opening_volume < 0:
        return None
    if opening_high < opening_low:
        return None
    if latest_price > opening_high or latest_price < opening_low:
        return None

    return {
        "session_open": session_open,
        "latest_price": latest_price,
        "opening_volume": opening_volume,
    }


class GapUpStrategy(BaseStrategy):

    name        = "gap_up"
    asset_class = "stocks"

    def scan(self, universe: List[str], current_time: datetime = None, **kwargs) -> List[Dict]:
        if not SCFG["enabled"]:
            return []

        entry_window_minutes = int(SCFG.get("entry_window_minutes", 45))
        elapsed_minutes = _entry_elapsed_minutes(current_time, entry_window_minutes)
        if elapsed_minutes is None:
            log.debug("[GapUp] Outside entry window, skipping.")
            return []

        min_gap     = float(SCFG.get("min_gap_pct", 0.03))
        max_gap     = float(SCFG.get("max_gap_pct", 0.15))
        vol_mult    = float(SCFG.get("volume_multiplier", 1.5))
        vol_avg_period = int(SCFG.get("volume_avg_period", 20))
        risk_pct    = float(SCFG.get("risk_per_trade_pct", 0.01))
        atr_period  = int(SCFG.get("atr_period", 14))
        atr_mult    = float(SCFG.get("atr_multiplier", 2.0))
        sma_long    = int(SCFG.get("trend_sma_period", 200))
        require_true_gap = bool(SCFG.get("require_true_gap", True))
        history_timeframe = SCFG.get("timeframe", "1Day")
        opening_timeframe = SCFG.get("opening_timeframe", "1Min")
        session_minutes = max(1.0, float(SCFG.get("session_minutes", 390)))
        max_open_extension_pct = float(SCFG.get("max_open_extension_pct", 0.03))
        max_open_fade_pct = float(SCFG.get("max_open_fade_pct", 0.005))
        max_signals = int(SCFG.get("max_signals", 0) or 0)
        min_trade_value = float(CFG["trading"].get("min_trade_value_usd", 100))
        max_position_pct = float(CFG["trading"].get("max_position_pct", 0.05))

        log.info(
            f"[GapUp] Scanning {len(universe)} symbols "
            f"(gap={min_gap:.1%}-{max_gap:.1%}, trend=SMA{sma_long})..."
        )

        try:
            # Daily bars are completed history only; the current session comes
            # from minute bars so live scans do not trade yesterday's gap.
            required_bars = max(sma_long, atr_period + 1, vol_avg_period)
            limit = max(required_bars + 10, sma_long + 10)
            bars_data = ac.get_stock_bars(universe, timeframe=history_timeframe, limit=limit)
            opening_limit = max(entry_window_minutes + 5, 10)
            opening_bars_data = ac.get_stock_bars(
                universe,
                timeframe=opening_timeframe,
                limit=opening_limit,
            )
        except Exception as e:
            log.error(f"[GapUp] Failed to fetch bars: {e}")
            return []

        regime_bars = kwargs.get("regime_bars")
        if not rm.market_regime_ok(
            bars_data=regime_bars,
            allow_warmup=bool(kwargs.get("allow_regime_warmup", False)),
        ):
            log.info("[GapUp] Bear regime (SPY < SMA50), skipping scan.")
            return []

        signals = []
        portfolio_value = None

        for symbol in universe:
            try:
                bars = _completed_daily_history(_bars_for_symbol(bars_data, symbol), current_time)
                if bars is None or len(bars) < required_bars:
                    continue

                df = _bars_to_frame(bars)
                if not _has_tradeable_ohlcv(df, required_bars):
                    log.debug(f"[GapUp] {symbol} skipped: invalid or incomplete OHLCV window.")
                    continue

                opening_metrics = _session_opening_metrics(
                    _bars_for_symbol(opening_bars_data, symbol),
                    current_time,
                )
                if opening_metrics is None:
                    log.debug(f"[GapUp] {symbol} skipped: no current-session opening bars.")
                    continue

                prev_open    = df["open"].iloc[-1]
                prev_close   = df["close"].iloc[-1]
                prev_high    = df["high"].iloc[-1]
                today_open   = opening_metrics["session_open"]
                current_px   = opening_metrics["latest_price"]
                opening_vol  = opening_metrics["opening_volume"]
                avg_vol      = df["volume"].iloc[-vol_avg_period:].mean()
                trend_sma    = df["close"].iloc[-sma_long:].mean()
                true_gap     = today_open > prev_high

                gap_pct = _safe_ratio(today_open - prev_close, prev_close)
                expected_opening_vol = avg_vol * min(elapsed_minutes, session_minutes) / session_minutes
                vol_ratio = _safe_ratio(opening_vol, expected_opening_vol)
                trend_spread = _safe_ratio(today_open - trend_sma, trend_sma)
                open_extension_pct = _safe_ratio(current_px - today_open, today_open)

                if min_gap <= gap_pct <= max_gap:
                    if (avg_vol > 0 and
                        vol_ratio >= vol_mult and
                        today_open > trend_sma and
                        prev_close > prev_open and
                        open_extension_pct <= max_open_extension_pct and
                        open_extension_pct >= -max_open_fade_pct and
                        (true_gap or not require_true_gap)):

                        price = float(current_px)
                        # Size from completed bars only; the current day's full range is unknown at entry.
                        atr = _calc_atr(bars, atr_period)
                        atr_stop = round(price - atr_mult * atr, 4)
                        risk_per_share = price - atr_stop

                        if risk_per_share > 0:
                            if portfolio_value is None:
                                try:
                                    portfolio_value = ac.get_portfolio_value()
                                except Exception as e:
                                    log.error(
                                        "[GapUp] Could not fetch portfolio value for ATR-risk sizing; "
                                        f"skipping signals: {e}"
                                    )
                                    return []
                            risk_amount = portfolio_value * risk_pct
                            qty = risk_amount / risk_per_share
                            max_qty = (portfolio_value * max_position_pct) / price
                            qty = round(min(qty, max_qty), 6)

                            if qty * price >= min_trade_value:
                                confidence = min(
                                    1.0,
                                    (0.45 * min(gap_pct / max(min_gap, 0.001), 2.0) / 2.0)
                                    + (0.35 * min(vol_ratio / max(vol_mult, 1.0), 2.0) / 2.0)
                                    + (0.15 * min(max(trend_spread, 0.0) / 0.05, 1.0))
                                    + (0.05 if true_gap else 0.0),
                                )
                                signals.append({
                                    "symbol":      symbol,
                                    "action":      "buy",
                                    "strategy":    self.name,
                                    "asset_class": self.asset_class,
                                    "confidence":  round(confidence, 3),
                                    "entry_price":  price,
                                    "atr_stop_price": atr_stop,
                                    "atr_risk_qty": qty,
                                    "reason":      (
                                        f"Gap-up {gap_pct:.2%} | opening vol pace={vol_ratio:.1f}x | "
                                        f"Trend UP | Prev Day Green"
                                        + (" | True Gap" if true_gap else "")
                                    ),
                                })
                                log.info(
                                    f"[GapUp] Candidate: BUY {symbol} | qty={qty} | stop={atr_stop} | "
                                    f"gap={gap_pct:.2%} | opening vol pace={vol_ratio:.1f}x | "
                                    f"SMA{sma_long}={trend_sma:.2f}"
                                )

            except Exception as e:
                log.warning(f"[GapUp] Error for {symbol}: {e}")
                continue

        ranked = sorted(signals, key=lambda sig: sig.get("confidence", 0), reverse=True)
        return ranked[:max_signals] if max_signals > 0 else ranked

    def should_exit(self, symbol: str, entry_price: float) -> tuple:
        """
        Exit failed gap continuations before the hold-days cap when the
        completed daily close breaks the configured failure threshold or loses
        the long-term trend filter.
        """
        timeframe = SCFG.get("timeframe", "1Day")
        trend_sma_period = int(SCFG.get("trend_sma_period", 200))
        breakdown_exit_pct = float(SCFG.get("breakdown_exit_pct", 0.0))
        trend_exit_enabled = bool(SCFG.get("trend_exit_enabled", False))
        limit = max(trend_sma_period + 2, 60)

        try:
            bars_data = ac.get_stock_bars([symbol], timeframe=timeframe, limit=limit)
            bars = bars_data[symbol] if bars_data else None
            if bars is None or len(bars) < trend_sma_period + 1:
                return False, ""

            df = _bars_to_frame(bars)
            if not _has_tradeable_ohlcv(df, trend_sma_period + 1):
                return False, ""

            close = float(df["close"].iloc[-1])
            if breakdown_exit_pct > 0 and close <= entry_price * (1 - breakdown_exit_pct):
                return True, (
                    f"Gap-up failed: close {close:.4f} <= "
                    f"entry {entry_price:.4f} - {breakdown_exit_pct:.1%}"
                )

            trend_sma = float(df["close"].iloc[-trend_sma_period:].mean())
            if trend_exit_enabled and close < trend_sma:
                return True, f"Gap-up trend failed: close {close:.4f} < SMA{trend_sma_period} {trend_sma:.4f}"

        except Exception as e:
            log.warning(f"[GapUp] Exit check error for {symbol}: {e}")

        return False, ""
