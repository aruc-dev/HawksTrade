"""
HawksTrade - Relative Strength Strategy
=======================================

Medium-term stock momentum sleeve that ranks candidates by excess 20-day return
versus SPY. This complements the existing short-window momentum strategy by
requiring both absolute strength and benchmark-relative leadership.

Exits are handled by the scheduler through the configured hold/trailing policy.
"""

import logging
import math
from typing import Dict, List, Optional

import pandas as pd

from core import alpaca_client as ac
from core import risk_manager as rm
from core.config_loader import get_config
from core.sector_lookup import get_sector
from strategies.atr_sizing import atr_stop_and_qty
from strategies.base_strategy import BaseStrategy
from strategies.momentum import (
    _bar_value,
    _breadth_coverage_pct,
    _calc_atr,
    _filter_bars_as_of,
    _filter_bars_data_as_of,
    _regular_session_progress,
    _sector_filtered_top_n,
    _session_volume_from_bars,
)

CFG = get_config()
SCFG = CFG["strategies"]["relative_strength"]
log = logging.getLogger("strategy.relative_strength")


def _safe_float(value, default: float = 0.0) -> float:
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(converted):
        return default
    return converted


def _closes_from_bars(bars) -> pd.Series:
    return pd.Series([_bar_value(bar, "close") for bar in bars], dtype="float64")


def _lookback_return(closes: pd.Series, lookback_days: int) -> Optional[float]:
    if len(closes) < lookback_days + 1:
        return None
    start = _safe_float(closes.iloc[-lookback_days - 1], default=0.0)
    end = _safe_float(closes.iloc[-1], default=0.0)
    if start <= 0 or end <= 0:
        return None
    return (end / start) - 1.0


def _sma(closes: pd.Series, days: int) -> Optional[float]:
    if days <= 0 or len(closes) < days:
        return None
    value = _safe_float(closes.iloc[-days:].mean(), default=0.0)
    return value if value > 0 else None


def _load_benchmark_bars(strategy, regime_bars, benchmark: str, current_time, limit: int):
    bars = None
    if regime_bars is not None:
        bars = strategy._get_symbol_bars(regime_bars, benchmark)
    if bars is None:
        raw = ac.get_stock_bars([benchmark], timeframe="1Day", limit=limit)
        bars = strategy._get_symbol_bars(raw, benchmark)
    return _filter_bars_as_of(bars, current_time)


class RelativeStrengthStrategy(BaseStrategy):

    name = "relative_strength"
    asset_class = "stocks"

    def _load_symbol_bars(self, bars_data, symbol: str, limit: int):
        bars = self._get_symbol_bars(bars_data, symbol)
        if bars is not None:
            return bars
        try:
            fallback = ac.get_stock_bars([symbol], timeframe="1Day", limit=limit)
        except Exception as exc:
            log.debug("[RelativeStrength] Fallback bars fetch failed for %s: %s", symbol, exc)
            return None
        bars = self._get_symbol_bars(fallback, symbol)
        if bars is None:
            log.debug("[RelativeStrength] Bars still missing for %s after fallback fetch.", symbol)
        return bars

    def scan(self, universe: List[str], **kwargs) -> List[Dict]:
        if not SCFG["enabled"]:
            return []

        log.info("[RelativeStrength] Scanning %s symbols...", len(universe))

        lookback_days = int(SCFG.get("lookback_days", 20))
        atr_period = int(SCFG.get("atr_period", 14))
        volume_avg_period = int(SCFG.get("volume_avg_period", 20))
        trend_sma_days = int(SCFG.get("trend_sma_days", 50))
        bars_limit = max(60, lookback_days + 5, atr_period + 2, volume_avg_period + 1, trend_sma_days + 1)

        try:
            bars_data = ac.get_stock_bars(universe, timeframe="1Day", limit=bars_limit)
        except Exception as e:
            log.error("[RelativeStrength] Failed to fetch bars: %s", e)
            return []

        current_time = kwargs.get("current_time")
        bars_data = _filter_bars_data_as_of(self, bars_data, universe, current_time)

        regime_bars = kwargs.get("regime_bars")
        if regime_bars is not None:
            regime_symbols = list(regime_bars.keys()) if isinstance(regime_bars, dict) else ["SPY", "QQQ"]
            regime_bars = _filter_bars_data_as_of(self, regime_bars, regime_symbols, current_time)

        spy_bull = rm.market_regime_ok(
            bars_data=regime_bars,
            allow_warmup=bool(kwargs.get("allow_regime_warmup", False)),
        )
        breadth = rm.market_breadth_pct(universe, bars_data=bars_data)
        green_thresh = float(SCFG.get("breadth_green_threshold", 0.50))
        red_thresh = float(SCFG.get("breadth_red_threshold", 0.25))
        yellow_max = int(SCFG.get("yellow_max_positions", 1))
        min_breadth_coverage = float(SCFG.get("min_breadth_coverage_pct", 0.0))

        breadth_coverage = _breadth_coverage_pct(bars_data, universe)
        if breadth_coverage < min_breadth_coverage:
            log.warning(
                "[RelativeStrength] Breadth coverage too low: %.1f%% < required %.1f%%. No new entries.",
                breadth_coverage * 100.0,
                min_breadth_coverage * 100.0,
            )
            return []

        if not spy_bull or breadth < red_thresh:
            log.info(
                "[RelativeStrength] Red regime - SPY_bull=%s breadth=%.1f%%. No new entries.",
                spy_bull,
                breadth * 100.0,
            )
            return []

        if breadth >= green_thresh:
            regime_tier = "Green"
            effective_top_n = int(SCFG["top_n"])
        else:
            regime_tier = "Yellow"
            effective_top_n = min(int(SCFG["top_n"]), yellow_max)

        log.info(
            "[RelativeStrength] Regime=%s breadth=%.1f%% top_n=%s",
            regime_tier,
            breadth * 100.0,
            effective_top_n,
        )

        benchmark = str(SCFG.get("benchmark_symbol", "SPY")).strip().upper() or "SPY"
        try:
            benchmark_bars = _load_benchmark_bars(self, regime_bars, benchmark, current_time, bars_limit)
            benchmark_closes = _closes_from_bars(benchmark_bars or [])
            benchmark_return = _lookback_return(benchmark_closes, lookback_days)
        except Exception as e:
            log.warning("[RelativeStrength] Failed to calculate %s benchmark return: %s", benchmark, e)
            return []
        if benchmark_return is None:
            log.warning("[RelativeStrength] Insufficient %s history for benchmark return.", benchmark)
            return []

        min_rs_pct = float(SCFG.get("min_rs_pct", 0.0))
        min_abs_return_pct = float(SCFG.get("min_abs_return_pct", 0.0))
        recent_lookback = int(SCFG.get("recent_lookback_days", 3))
        max_recent_return_pct = SCFG.get("max_recent_return_pct")
        max_trend_extension_pct = SCFG.get("max_trend_extension_pct")
        require_price_above_sma = bool(SCFG.get("require_price_above_sma", True))
        atr_mult = float(SCFG.get("atr_multiplier", 1.2))
        max_stop_loss_pct = SCFG.get("max_stop_loss_pct")
        if max_stop_loss_pct in ("", None):
            max_stop_loss_pct = None
        risk_pct = float(SCFG.get("risk_per_trade_pct", 0.01))
        min_trade_value = float(CFG["trading"].get("min_trade_value_usd", 100))
        volume_mode = str(SCFG.get("volume_confirmation_mode", "daily")).strip().lower()
        if volume_mode not in {"daily", "pace"}:
            log.warning(
                "[RelativeStrength] Unknown volume_confirmation_mode=%s; using daily fallback.",
                volume_mode,
            )
            volume_mode = "daily"
        volume_spike_ratio = float(SCFG.get("volume_spike_ratio", 1.2))
        volume_pace_ratio = float(SCFG.get("volume_pace_ratio", volume_spike_ratio))
        session_minutes = max(1.0, float(SCFG.get("session_minutes", 390)))
        volume_pace_timeframe = str(SCFG.get("volume_pace_timeframe", "1Min"))
        elapsed_minutes, in_regular_session = _regular_session_progress(current_time, session_minutes)
        intraday_bars_data = None

        if volume_mode == "pace" and in_regular_session:
            try:
                intraday_limit = max(10, min(int(math.ceil(elapsed_minutes)) + 5, int(session_minutes) + 5))
                intraday_bars_data = ac.get_stock_bars(
                    universe,
                    timeframe=volume_pace_timeframe,
                    limit=intraday_limit,
                )
            except Exception as e:
                log.warning(
                    "[RelativeStrength] Failed to fetch intraday bars for volume pace; "
                    "falling back to daily volume ratio: %s",
                    e,
                )

        scores = []
        for symbol in universe:
            try:
                bars = self._load_symbol_bars(bars_data, symbol, bars_limit)
                bars = _filter_bars_as_of(bars, current_time)
                if bars is None or len(bars) < bars_limit - 10:
                    continue

                closes = _closes_from_bars(bars)
                price_now = _safe_float(closes.iloc[-1], default=0.0)
                if price_now <= 0:
                    continue

                abs_return = _lookback_return(closes, lookback_days)
                if abs_return is None:
                    continue
                rs_return = abs_return - benchmark_return
                if abs_return < min_abs_return_pct:
                    log.debug(
                        "[RelativeStrength] %s skipped: abs %.1f%% < %.1f%%.",
                        symbol,
                        abs_return * 100.0,
                        min_abs_return_pct * 100.0,
                    )
                    continue
                if rs_return < min_rs_pct:
                    log.debug(
                        "[RelativeStrength] %s skipped: RS %.1f%% < %.1f%%.",
                        symbol,
                        rs_return * 100.0,
                        min_rs_pct * 100.0,
                    )
                    continue

                if recent_lookback > 0 and max_recent_return_pct not in ("", None):
                    recent_return = _lookback_return(closes, recent_lookback)
                    if recent_return is not None and recent_return > float(max_recent_return_pct):
                        log.debug(
                            "[RelativeStrength] %s skipped: recent %.1f%% > max %.1f%%.",
                            symbol,
                            recent_return * 100.0,
                            float(max_recent_return_pct) * 100.0,
                        )
                        continue

                trend_sma = _sma(closes, trend_sma_days)
                if require_price_above_sma and trend_sma is not None and price_now < trend_sma:
                    log.debug(
                        "[RelativeStrength] %s skipped: price %.2f below SMA%d %.2f.",
                        symbol,
                        price_now,
                        trend_sma_days,
                        trend_sma,
                    )
                    continue
                if trend_sma is not None and max_trend_extension_pct not in ("", None):
                    trend_extension = (price_now / trend_sma) - 1.0
                    if trend_extension > float(max_trend_extension_pct):
                        log.debug(
                            "[RelativeStrength] %s skipped: trend extension %.1f%% > %.1f%%.",
                            symbol,
                            trend_extension * 100.0,
                            float(max_trend_extension_pct) * 100.0,
                        )
                        continue

                volumes = pd.Series([_bar_value(bar, "volume") for bar in bars], dtype="float64")
                if len(volumes) < volume_avg_period + 1:
                    continue
                avg_vol = volumes.iloc[-volume_avg_period - 1:-1].mean()
                if avg_vol <= 0:
                    continue
                curr_vol = _bar_value(bars[-1], "volume")
                daily_volume_ratio = curr_vol / avg_vol
                volume_ratio = daily_volume_ratio
                volume_required = volume_spike_ratio
                volume_basis = "daily"

                if volume_mode == "pace" and intraday_bars_data is not None:
                    intraday_bars = self._get_symbol_bars(intraday_bars_data, symbol)
                    session_volume = _session_volume_from_bars(intraday_bars, current_time)
                    expected_volume = avg_vol * elapsed_minutes / session_minutes
                    if session_volume is not None and expected_volume > 0:
                        volume_ratio = session_volume / expected_volume
                        volume_required = volume_pace_ratio
                        volume_basis = "pace"

                if volume_ratio < volume_required:
                    log.debug(
                        "[RelativeStrength] %s skipped: volume %s %.2fx < %.2fx.",
                        symbol,
                        volume_basis,
                        volume_ratio,
                        volume_required,
                    )
                    continue

                atr = _calc_atr(bars, period=atr_period) if len(bars) >= atr_period + 1 else 0.0
                scores.append({
                    "symbol": symbol,
                    "price": price_now,
                    "atr": atr,
                    "abs_return": abs_return,
                    "benchmark_return": benchmark_return,
                    "rs_return": rs_return,
                    "volume_ratio": volume_ratio,
                    "volume_basis": volume_basis,
                })
            except Exception as e:
                log.warning("[RelativeStrength] Error processing %s: %s", symbol, e)
                continue

        if not scores:
            return []

        scores.sort(key=lambda item: (item["rs_return"], item["abs_return"]), reverse=True)
        max_per_sector = int(SCFG.get("max_positions_per_sector", 1))
        top = _sector_filtered_top_n(
            scores,
            effective_top_n,
            max_per_sector,
            existing_symbols=kwargs.get("existing_symbols"),
        )

        try:
            portfolio_equity = ac.get_portfolio_value()
        except Exception as e:
            log.error(
                "[RelativeStrength] Could not fetch portfolio value for ATR-risk sizing; skipping signals: %s",
                e,
            )
            return []

        signals = []
        for candidate in top:
            sized = atr_stop_and_qty(
                symbol=candidate["symbol"],
                price=candidate["price"],
                atr=candidate["atr"],
                atr_multiplier=atr_mult,
                portfolio_equity=portfolio_equity,
                risk_per_trade_pct=risk_pct,
                min_trade_value=min_trade_value,
                logger=log,
                prefix="[RelativeStrength]",
                max_stop_loss_pct=max_stop_loss_pct,
            )
            if sized is None:
                continue
            atr_stop, atr_risk_qty = sized

            signal: Dict = {
                "symbol": candidate["symbol"],
                "action": "buy",
                "strategy": self.name,
                "asset_class": self.asset_class,
                "confidence": round(min(max(candidate["rs_return"] / 0.10, 0.0), 1.0), 3),
                "relative_strength_score": round(candidate["rs_return"], 4),
                "momentum_score": round(candidate["abs_return"], 4),
                "benchmark_score": round(candidate["benchmark_return"], 4),
                "reason": (
                    f"20d RS vs {benchmark}: {candidate['rs_return']:.1%} "
                    f"(absolute {candidate['abs_return']:.1%}, benchmark {candidate['benchmark_return']:.1%}) | "
                    f"Volume {candidate['volume_basis']}: {candidate['volume_ratio']:.1f}x"
                ),
                "atr_stop_price": atr_stop,
                "atr_risk_qty": atr_risk_qty,
            }

            log.info(
                "[RelativeStrength] Signal: BUY %s | RS=%.1f%% | Absolute=%.1f%% | %s=%.1f%% | "
                "sector=%s | volume_%s=%.2fx | atr_stop=%s | risk_qty=%s",
                candidate["symbol"],
                candidate["rs_return"] * 100.0,
                candidate["abs_return"] * 100.0,
                benchmark,
                candidate["benchmark_return"] * 100.0,
                get_sector(candidate["symbol"]),
                candidate["volume_basis"],
                candidate["volume_ratio"],
                atr_stop,
                atr_risk_qty,
            )
            signals.append(signal)

        return signals

    def should_exit(self, symbol: str, entry_price: float) -> tuple:
        return False, ""
