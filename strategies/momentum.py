"""
HawksTrade - Momentum Strategy (Adaptive v2.1)
===============================================
Phase 1: ATR-adjusted stop extension (1.2×ATR below entry) and 1%-risk position sizing.
Phase 2: Sector-neutral ranking — max 1 position per GICS sector.
Phase 3: Breadth data coverage guard.
Phase 4: Market breadth tiered regime guard.
  - Green  (breadth >= 50%): full deployment.
  - Yellow (breadth 25–50%): reduced deployment (yellow_max_positions cap).
  - Red    (breadth < 25% OR SPY < SMA50): no new entries.

Exits are handled by the scheduler: flat/losing trades exit after the minimum
hold, while profitable trades run with trailing protection.

Strategy: Swing trade (NOT intraday).
"""

import logging
import math
from datetime import datetime, time, timezone
from typing import List, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

from strategies.base_strategy import BaseStrategy
from strategies.atr_sizing import atr_stop_and_qty
from core import alpaca_client as ac
from core import risk_manager as rm
from core.config_loader import get_config
from core.sector_lookup import get_sector

CFG = get_config()

SCFG = CFG["strategies"]["momentum"]
ET = ZoneInfo("America/New_York")
log = logging.getLogger("strategy.momentum")


def _bar_value(bar, field: str, default: float = 0.0) -> float:
    if isinstance(bar, dict):
        value = bar.get(field, default)
    else:
        value = getattr(bar, field, default)
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return float(default)


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
    elif hasattr(value, "to_pydatetime"):
        try:
            ts = value.to_pydatetime()
        except (TypeError, ValueError):
            return None
    else:
        try:
            ts = pd.to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if pd.isna(ts):
            return None
        if hasattr(ts, "to_pydatetime"):
            ts = ts.to_pydatetime()
        elif not isinstance(ts, datetime):
            return None
    if pd.isna(ts):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _as_et(value=None) -> datetime:
    if value is None:
        return datetime.now(ET)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ET)


def _regular_session_progress(current_time, session_minutes: float) -> Tuple[float, bool]:
    now_et = _as_et(current_time)
    session_start = datetime.combine(now_et.date(), time(9, 30), tzinfo=ET)
    elapsed = (now_et - session_start).total_seconds() / 60.0
    in_session = 0 <= elapsed <= session_minutes
    return max(1.0, min(float(elapsed), float(session_minutes))), in_session


def _session_volume_from_bars(bars, current_time) -> Optional[float]:
    if not bars:
        return None

    now_et = _as_et(current_time)
    session_start = datetime.combine(now_et.date(), time(9, 30), tzinfo=ET)
    total = 0.0
    saw_timestamp = False

    for bar in bars:
        volume = _bar_value(bar, "volume")
        if volume <= 0:
            continue
        ts = _parse_bar_timestamp(_bar_timestamp(bar))
        if ts is None:
            continue
        saw_timestamp = True
        ts_et = ts.astimezone(ET)
        if session_start <= ts_et <= now_et:
            total += volume

    if not saw_timestamp:
        return None
    return total if total > 0 else None


def _calc_atr(bars, period: int = 14) -> float:
    """Compute ATR via EWM-smoothed True Range over the most recent bars."""
    if len(bars) < 2:
        return 0.0
    trs = []
    for i in range(1, len(bars)):
        high = _bar_value(bars[i], "high")
        low = _bar_value(bars[i], "low")
        prev_close = _bar_value(bars[i - 1], "close")
        if high <= 0 or low <= 0 or prev_close <= 0:
            continue
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if not trs:
        return 0.0
    return float(pd.Series(trs).ewm(span=period, adjust=False).mean().iloc[-1])


def _breadth_coverage_pct(bars_data, universe: list, min_bars: int = 51) -> float:
    """Return the fraction of the scan universe with enough valid bars for breadth."""
    if not universe:
        return 0.0
    eligible = 0
    for symbol in universe:
        try:
            bars = bars_data[symbol]
        except Exception:
            bars = None
        if bars is None or len(bars) < min_bars:
            continue
        closes = [_bar_value(bar, "close") for bar in bars[-min_bars:]]
        if all(value > 0 and math.isfinite(value) for value in closes):
            eligible += 1
    return eligible / len(universe)


def _initial_sector_counts(existing_symbols=None) -> Dict[str, int]:
    """Count sectors already represented by open or pending stock positions."""
    sector_counts: Dict[str, int] = {}
    for symbol in existing_symbols or []:
        if not str(symbol or "").strip():
            continue
        sector = get_sector(str(symbol))
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
    return sector_counts


def _sector_filtered_top_n(
    scores: list,
    top_n: int,
    max_per_sector: int,
    existing_symbols=None,
) -> list:
    """
    Return up to top_n candidates from a pre-sorted (desc momentum) scores list
    while enforcing max_per_sector per GICS sector across existing and new
    momentum candidates.
    """
    selected: list = []
    sector_counts = _initial_sector_counts(existing_symbols)
    for candidate in scores:
        sector = get_sector(candidate["symbol"])
        if sector_counts.get(sector, 0) < max_per_sector:
            selected.append(candidate)
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
        if len(selected) >= top_n:
            break
    return selected


class MomentumStrategy(BaseStrategy):

    name        = "momentum"
    asset_class = "stocks"

    def _load_symbol_bars(self, bars_data, symbol: str):
        bars = self._get_symbol_bars(bars_data, symbol)
        if bars is not None:
            return bars
        try:
            fallback = ac.get_stock_bars([symbol], timeframe="1Day", limit=25)
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

        # Fetch bars: need max(SMA50=50, ATR14=14, momentum=6) + buffer → 60 bars
        try:
            bars_data = ac.get_stock_bars(universe, timeframe="1Day", limit=60)
        except Exception as e:
            log.error(f"[Momentum] Failed to fetch bars: {e}")
            return []

        # --- Phase 3: Market Breadth Tiered Regime Guard ---
        regime_bars = kwargs.get("regime_bars")
        spy_bull = rm.market_regime_ok(
            bars_data=regime_bars,
            allow_warmup=bool(kwargs.get("allow_regime_warmup", False)),
        )

        breadth = rm.market_breadth_pct(universe, bars_data=bars_data)

        green_thresh  = float(SCFG.get("breadth_green_threshold", 0.50))
        red_thresh    = float(SCFG.get("breadth_red_threshold", 0.25))
        yellow_max    = int(SCFG.get("yellow_max_positions", 3))
        min_breadth_coverage = float(SCFG.get("min_breadth_coverage_pct", 0.0))

        breadth_coverage = _breadth_coverage_pct(bars_data, universe)
        if breadth_coverage < min_breadth_coverage:
            log.warning(
                f"[Momentum] Breadth coverage too low: {breadth_coverage:.1%} "
                f"< required {min_breadth_coverage:.1%}. No new entries."
            )
            return []

        if not spy_bull or breadth < red_thresh:
            log.info(
                f"[Momentum] Red regime — SPY_bull={spy_bull} breadth={breadth:.1%} "
                f"(red<{red_thresh:.0%}). No new entries."
            )
            return []

        if breadth >= green_thresh:
            regime_tier = "Green"
            effective_top_n = int(SCFG["top_n"])
        else:
            # Yellow: breadth is between red_thresh and green_thresh (25–50%)
            regime_tier = "Yellow"
            effective_top_n = min(int(SCFG["top_n"]), yellow_max)

        log.info(
            f"[Momentum] Regime={regime_tier} breadth={breadth:.1%} "
            f"top_n={effective_top_n}"
        )

        # --- Calculate SPY Momentum for Alpha (Recommendation 2) ---
        spy_momentum = 0.0
        try:
            s_bars = None
            if regime_bars and "SPY" in regime_bars:
                s_bars = regime_bars["SPY"]
            else:
                # Fallback fetch for SPY if not in regime_bars
                raw_spy = ac.get_stock_bars(["SPY"], timeframe="1Day", limit=25)
                s_bars = raw_spy.get("SPY")

            if s_bars and len(s_bars) >= 8:
                s_closes = pd.Series([
                    float(b.close) if hasattr(b, "close") else float(b["close"])
                    for b in s_bars
                ])
                s_avg_now = s_closes.iloc[-2:].mean()
                s_avg_then = s_closes.iloc[-8:-5].mean()
                if s_avg_then > 0:
                    spy_momentum = (s_avg_now - s_avg_then) / s_avg_then
                    log.debug(f"[Momentum] SPY 5d-smoothed momentum: {spy_momentum:.2%}")
        except Exception as e:
            log.warning(f"[Momentum] Failed to calculate SPY momentum for Alpha: {e}")

        # --- Score candidates ---
        scores = []
        atr_period = int(SCFG.get("atr_period", 14))
        atr_mult   = float(SCFG.get("atr_multiplier", 2.0))
        min_alpha_pct = float(SCFG.get("min_alpha_pct", 0.0))
        volume_mode = str(SCFG.get("volume_confirmation_mode", "daily")).strip().lower()
        if volume_mode not in {"daily", "pace"}:
            log.warning("[Momentum] Unknown volume_confirmation_mode=%s; using daily fallback.", volume_mode)
            volume_mode = "daily"
        volume_spike_ratio = float(SCFG.get("volume_spike_ratio", 1.2))
        volume_pace_ratio = float(SCFG.get("volume_pace_ratio", volume_spike_ratio))
        session_minutes = max(1.0, float(SCFG.get("session_minutes", 390)))
        volume_pace_timeframe = str(SCFG.get("volume_pace_timeframe", "1Min"))
        current_time = kwargs.get("current_time")
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
                    "[Momentum] Failed to fetch intraday bars for volume pace; "
                    "falling back to daily volume ratio: %s",
                    e,
                )

        for symbol in universe:
            try:
                bars = self._load_symbol_bars(bars_data, symbol)
                if bars is None or len(bars) < 21: # Need 21 for avg volume
                    continue

                # 1. Smoothed Lookback (Recommendation 1)
                closes = pd.Series([float(b.close) if hasattr(b, "close") else float(b["close"]) for b in bars])
                if len(closes) < 8:
                    continue
                avg_now = closes.iloc[-2:].mean()
                avg_then = closes.iloc[-8:-5].mean()
                if avg_then <= 0:
                    continue
                
                momentum = (avg_now - avg_then) / avg_then
                price_now = float(closes.iloc[-1])

                # 2. Alpha (Recommendation 2)
                alpha = momentum - spy_momentum

                if momentum < SCFG["min_momentum_pct"]:
                    continue
                if alpha < min_alpha_pct:
                    log.debug(
                        f"[Momentum] {symbol} skipped: alpha {alpha:.1%} < {min_alpha_pct:.1%}"
                    )
                    continue

                # 3. Volume Confirmation (Recommendation 3)
                volumes = pd.Series([
                    float(b.volume) if hasattr(b, "volume") else float(b["volume"])
                    for b in bars
                ])
                avg_vol_20 = volumes.iloc[-21:-1].mean()
                if avg_vol_20 <= 0:
                    continue
                curr_vol = _bar_value(bars[-1], "volume")
                daily_volume_ratio = curr_vol / avg_vol_20
                volume_ratio = daily_volume_ratio
                volume_required = volume_spike_ratio
                volume_basis = "daily"

                if volume_mode == "pace" and intraday_bars_data is not None:
                    intraday_bars = self._get_symbol_bars(intraday_bars_data, symbol)
                    session_volume = _session_volume_from_bars(intraday_bars, current_time)
                    expected_volume = avg_vol_20 * elapsed_minutes / session_minutes
                    if session_volume is not None and expected_volume > 0:
                        volume_ratio = session_volume / expected_volume
                        volume_required = volume_pace_ratio
                        volume_basis = "pace"

                if volume_ratio < volume_required:
                    log.debug(
                        f"[Momentum] {symbol} skipped: volume {volume_basis} confirmation "
                        f"failed ({volume_ratio:.2f}x < {volume_required:.2f}x)"
                    )
                    continue

                # Phase 1: ATR input for stop and risk sizing
                atr = _calc_atr(bars, period=atr_period) if len(bars) >= atr_period + 1 else 0.0

                scores.append({
                    "symbol":        symbol,
                    "momentum":      momentum,
                    "alpha":         alpha,
                    "price":         price_now,
                    "atr":           atr,
                    "volume_ratio":  volume_ratio,
                    "volume_basis":  volume_basis,
                })

            except Exception as e:
                log.warning(f"[Momentum] Error processing {symbol}: {e}")
                continue

        if not scores:
            return []

        # --- Phase 2: Sector-neutral ranking ---
        scores.sort(key=lambda x: x["alpha"], reverse=True)
        max_per_sector = int(SCFG.get("max_positions_per_sector", 1))
        top = _sector_filtered_top_n(
            scores,
            effective_top_n,
            max_per_sector,
            existing_symbols=kwargs.get("existing_symbols"),
        )

        # --- Phase 1: ATR-based risk sizing ---
        risk_pct = float(SCFG.get("risk_per_trade_pct", 0.01))
        min_trade_value = float(CFG["trading"].get("min_trade_value_usd", 100))
        try:
            portfolio_equity = ac.get_portfolio_value()
        except Exception as e:
            log.error(f"[Momentum] Could not fetch portfolio value for ATR-risk sizing; skipping signals: {e}")
            return []

        signals = []
        for s in top:
            price     = s["price"]
            sized = atr_stop_and_qty(
                symbol=s["symbol"],
                price=price,
                atr=s["atr"],
                atr_multiplier=atr_mult,
                portfolio_equity=portfolio_equity,
                risk_per_trade_pct=risk_pct,
                min_trade_value=min_trade_value,
                logger=log,
                prefix="[Momentum]",
            )
            if sized is None:
                continue
            atr_stop, atr_risk_qty = sized

            sig: Dict = {
                "symbol":     s["symbol"],
                "action":     "buy",
                "strategy":   self.name,
                "asset_class": self.asset_class,
                "confidence": round(min(s["momentum"] / 0.10, 1.0), 3),
                "momentum_score": round(s["momentum"], 4),
                "alpha_score":    round(s["alpha"], 4),
                "reason":     (
                    f"Alpha momentum: {s['alpha']:.1%} (Absolute {s['momentum']:.1%}) | "
                    f"Volume {s['volume_basis']}: {s['volume_ratio']:.1f}x"
                ),
            }
            sig["atr_stop_price"] = atr_stop
            sig["atr_risk_qty"] = atr_risk_qty

            log.info(
                f"[Momentum] Signal: BUY {s['symbol']} | Alpha={s['alpha']:.1%} "
                f"| Momentum={s['momentum']:.1%} | sector={get_sector(s['symbol'])} "
                f"| volume_{s['volume_basis']}={s['volume_ratio']:.2f}x "
                f"| atr_stop={atr_stop} | risk_qty={atr_risk_qty}"
            )
            signals.append(sig)

        return signals

    def should_exit(self, symbol: str, entry_price: float) -> tuple:
        """
        Momentum exits on take-profit / stop-loss (handled by risk_manager).
        Strategy-level hold/trailing exits are checked by the scheduler.
        """
        return False, ""
