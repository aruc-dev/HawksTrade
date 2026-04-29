"""
HawksTrade - Momentum Strategy (Adaptive v2.0)
===============================================
Phase 1: ATR-adjusted stop-loss (2×ATR below entry) and 1%-risk position sizing.
Phase 2: Sector-neutral ranking — max 1 position per GICS sector.
Phase 3: Market breadth tiered regime guard.
  - Green  (breadth >= 50%): full deployment.
  - Yellow (breadth 25–50%): reduced deployment (yellow_max_positions cap).
  - Red    (breadth < 25% OR SPY < SMA50): no new entries.

Exits are handled by the scheduler: flat/losing trades exit after the minimum
hold, while profitable trades run with trailing protection.

Strategy: Swing trade (NOT intraday).
"""

import logging
from typing import List, Dict

import pandas as pd

from strategies.base_strategy import BaseStrategy
from strategies.atr_sizing import atr_stop_and_qty
from core import alpaca_client as ac
from core import risk_manager as rm
from core.config_loader import get_config
from core.sector_lookup import get_sector

CFG = get_config()

SCFG = CFG["strategies"]["momentum"]
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

                # 3. Volume Confirmation (Recommendation 3)
                # Current bar must be a volume spike (> volume_spike_ratio × 20-day avg)
                vol_spike_ratio = float(SCFG.get("volume_spike_ratio", 1.2))
                volumes = pd.Series([
                    float(b.volume) if hasattr(b, "volume") else float(b["volume"])
                    for b in bars
                ])
                avg_vol_20 = volumes.iloc[-21:-1].mean()
                # Fix BUG-007: add safety guard for volume access
                curr_vol = _bar_value(bars[-1], "volume")
                if avg_vol_20 > 0 and curr_vol <= vol_spike_ratio * avg_vol_20:
                    log.debug(f"[Momentum] {symbol} skipped: volume confirmation failed ({curr_vol:.0f} <= {vol_spike_ratio}x {avg_vol_20:.0f})")
                    continue

                # Phase 1: ATR input for stop and risk sizing
                atr = _calc_atr(bars, period=atr_period) if len(bars) >= atr_period + 1 else 0.0

                scores.append({
                    "symbol":        symbol,
                    "momentum":      momentum,
                    "alpha":         alpha,
                    "price":         price_now,
                    "atr":           atr,
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
                "reason":     f"Alpha momentum: {s['alpha']:.1%} (Absolute {s['momentum']:.1%})",
            }
            sig["atr_stop_price"] = atr_stop
            sig["atr_risk_qty"] = atr_risk_qty

            log.info(
                f"[Momentum] Signal: BUY {s['symbol']} | Alpha={s['alpha']:.1%} "
                f"| Momentum={s['momentum']:.1%} | sector={get_sector(s['symbol'])} "
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
