"""
HawksTrade - Momentum Strategy (Adaptive v2.0)
===============================================
Phase 1: ATR-adjusted stop-loss (2×ATR below entry) and 1%-risk position sizing.
Phase 2: Sector-neutral ranking — max 1 position per GICS sector.
Phase 3: Market breadth tiered regime guard.
  - Green  (breadth >= 50%): full deployment.
  - Yellow (breadth < 40%): reduced deployment (yellow_max_positions cap).
  - Red    (breadth < 25% OR SPY < SMA50): no new entries.

Exits are handled by the scheduler: flat/losing trades exit after the minimum
hold, while profitable trades run with trailing protection.

Strategy: Swing trade (NOT intraday).
"""

import logging
from typing import List, Dict

import pandas as pd

from strategies.base_strategy import BaseStrategy
from core import alpaca_client as ac
from core import risk_manager as rm
from core.config_loader import get_config

CFG = get_config()

SCFG = CFG["strategies"]["momentum"]
log = logging.getLogger("strategy.momentum")

# Static GICS sector mapping — covers configured universe + extended backtest pool.
# Symbols not in this map are assigned a unique pseudo-sector so they never
# conflict with each other or with known sectors.
_SECTOR_MAP: Dict[str, str] = {
    # Technology
    "AAPL": "Technology", "MSFT": "Technology", "GOOGL": "Technology",
    "NVDA": "Technology", "META": "Technology", "AMD": "Technology",
    "ORCL": "Technology", "CRM": "Technology", "INTC": "Technology",
    "IBM": "Technology", "ARM": "Technology", "AVGO": "Technology",
    "TSM": "Technology", "SMCI": "Technology", "QCOM": "Technology",
    "TXN": "Technology", "AMAT": "Technology", "LRCX": "Technology",
    "MU": "Technology", "NOW": "Technology", "ADBE": "Technology",
    "SNOW": "Technology", "PANW": "Technology", "CRWD": "Technology",
    "ZS": "Technology", "NET": "Technology", "DDOG": "Technology",
    "MDB": "Technology", "ANET": "Technology", "MRVL": "Technology",
    "ASML": "Technology", "SAP": "Technology",
    "PLTR": "Technology", "AI": "Technology", "SOUN": "Technology",
    "IONQ": "Technology", "MSTR": "Technology",
    # Consumer Discretionary
    "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary",
    "NFLX": "Consumer Discretionary", "HD": "Consumer Discretionary",
    "TGT": "Consumer Discretionary", "NKE": "Consumer Discretionary",
    "SBUX": "Consumer Discretionary", "MCD": "Consumer Discretionary",
    "DIS": "Consumer Discretionary", "CMCSA": "Consumer Discretionary",
    "ABNB": "Consumer Discretionary", "DASH": "Consumer Discretionary",
    "RBLX": "Consumer Discretionary", "SHOP": "Consumer Discretionary",
    "SNAP": "Consumer Discretionary", "PINS": "Consumer Discretionary",
    "UBER": "Consumer Discretionary", "BABA": "Consumer Discretionary",
    "JD": "Consumer Discretionary", "PDD": "Consumer Discretionary",
    "TM": "Consumer Discretionary",
    # Financials
    "JPM": "Financials", "BAC": "Financials", "GS": "Financials",
    "MS": "Financials", "WFC": "Financials", "C": "Financials",
    "BLK": "Financials", "SCHW": "Financials", "AXP": "Financials",
    "V": "Financials", "MA": "Financials", "PYPL": "Financials",
    "SQ": "Financials", "COIN": "Financials", "HOOD": "Financials",
    "SOFI": "Financials",
    # Energy
    "XOM": "Energy", "CVX": "Energy", "COP": "Energy",
    "SLB": "Energy", "HAL": "Energy", "OXY": "Energy",
    # Health Care
    "JNJ": "Health Care", "UNH": "Health Care", "PFE": "Health Care",
    "ABBV": "Health Care", "MRK": "Health Care", "BMY": "Health Care",
    "GILD": "Health Care", "AMGN": "Health Care", "REGN": "Health Care",
    "MDT": "Health Care", "ISRG": "Health Care", "ELV": "Health Care",
    "LLY": "Health Care",
    # Industrials (incl. Defence)
    "CAT": "Industrials", "DE": "Industrials", "HON": "Industrials",
    "GE": "Industrials", "UPS": "Industrials", "FDX": "Industrials",
    "LMT": "Industrials", "RTX": "Industrials", "NOC": "Industrials",
    "GD": "Industrials", "BA": "Industrials",
    # Consumer Staples
    "PG": "Consumer Staples", "KO": "Consumer Staples",
    "PEP": "Consumer Staples", "WMT": "Consumer Staples",
    "COST": "Consumer Staples",
    # Communication Services
    "T": "Communication Services", "VZ": "Communication Services",
    "TMUS": "Communication Services", "SPOT": "Communication Services",
    # Real Estate
    "AMT": "Real Estate", "PLD": "Real Estate", "CCI": "Real Estate",
    # Utilities
    "NEE": "Utilities", "DUK": "Utilities", "SO": "Utilities",
    # ETFs (treated as unique sectors so they never block each other)
    "SPY": "ETF_SPY", "QQQ": "ETF_QQQ", "ARKK": "ETF_ARKK",
}


def _get_sector(symbol: str) -> str:
    """Return GICS sector for symbol; unknown symbols get a unique pseudo-sector."""
    return _SECTOR_MAP.get(symbol, f"Unknown_{symbol}")


def _calc_atr(bars, period: int = 14) -> float:
    """Compute ATR via EWM-smoothed True Range over the most recent bars."""
    if len(bars) < 2:
        return 0.0
    trs = []
    for i in range(1, len(bars)):
        high = float(getattr(bars[i], "high", 0) or 0)
        low = float(getattr(bars[i], "low", 0) or 0)
        prev_close = float(getattr(bars[i - 1], "close", 0) or 0)
        if high <= 0 or low <= 0 or prev_close <= 0:
            continue
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if not trs:
        return 0.0
    return float(pd.Series(trs).ewm(span=period, adjust=False).mean().iloc[-1])


def _sector_filtered_top_n(scores: list, top_n: int, max_per_sector: int) -> list:
    """
    Return up to top_n candidates from a pre-sorted (desc momentum) scores list
    while enforcing max_per_sector per GICS sector.
    """
    selected: list = []
    sector_counts: Dict[str, int] = {}
    for candidate in scores:
        sector = _get_sector(candidate["symbol"])
        if sector_counts.get(sector, 0) < max_per_sector:
            selected.append(candidate)
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
        if len(selected) >= top_n:
            break
    return selected


class MomentumStrategy(BaseStrategy):

    name        = "momentum"
    asset_class = "stocks"

    @staticmethod
    def _missing_symbol_error(exc: Exception) -> bool:
        message = str(exc)
        return isinstance(exc, KeyError) or message.startswith("'No key ") or message.startswith("No key ")

    def _get_symbol_bars(self, bars_data, symbol: str):
        try:
            return bars_data[symbol]
        except Exception as exc:
            if self._missing_symbol_error(exc):
                return None
            raise

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
        spy_bull = rm.market_regime_ok(bars_data=regime_bars)

        breadth = rm.market_breadth_pct(universe, bars_data=bars_data)

        green_thresh  = float(SCFG.get("breadth_green_threshold", 0.50))
        yellow_thresh = float(SCFG.get("breadth_yellow_threshold", 0.40))
        red_thresh    = float(SCFG.get("breadth_red_threshold", 0.25))
        yellow_max    = int(SCFG.get("yellow_max_positions", 3))

        if not spy_bull or breadth < red_thresh:
            log.info(
                f"[Momentum] Red regime — SPY_bull={spy_bull} breadth={breadth:.1%} "
                f"(red<{red_thresh:.0%}). No new entries."
            )
            return []

        if breadth < yellow_thresh:
            regime_tier = "Yellow"
            effective_top_n = min(int(SCFG["top_n"]), yellow_max)
        else:
            regime_tier = "Green" if breadth >= green_thresh else "Yellow"
            effective_top_n = int(SCFG["top_n"])

        log.info(
            f"[Momentum] Regime={regime_tier} breadth={breadth:.1%} "
            f"top_n={effective_top_n}"
        )

        # --- Score candidates ---
        scores = []
        atr_period = int(SCFG.get("atr_period", 14))
        atr_mult   = float(SCFG.get("atr_multiplier", 2.0))

        for symbol in universe:
            try:
                bars = self._load_symbol_bars(bars_data, symbol)
                if bars is None or len(bars) < 6:
                    continue

                price_now    = float(bars[-1].close)
                price_5d_ago = float(bars[-6].close)
                if price_5d_ago <= 0:
                    continue
                momentum = (price_now - price_5d_ago) / price_5d_ago

                if momentum < SCFG["min_momentum_pct"]:
                    continue

                # Phase 1: ATR stop price
                atr = _calc_atr(bars, period=atr_period) if len(bars) >= atr_period + 1 else 0.0
                atr_stop = round(price_now - atr_mult * atr, 4) if atr > 0 else None

                scores.append({
                    "symbol":        symbol,
                    "momentum":      momentum,
                    "price":         price_now,
                    "atr":           atr,
                    "atr_stop":      atr_stop,
                })

            except Exception as e:
                log.warning(f"[Momentum] Error processing {symbol}: {e}")
                continue

        if not scores:
            return []

        # --- Phase 2: Sector-neutral ranking ---
        scores.sort(key=lambda x: x["momentum"], reverse=True)
        max_per_sector = int(SCFG.get("max_positions_per_sector", 1))
        top = _sector_filtered_top_n(scores, effective_top_n, max_per_sector)

        # --- Phase 1: ATR-based risk sizing ---
        risk_pct = float(SCFG.get("risk_per_trade_pct", 0.01))
        min_trade_value = float(CFG["trading"].get("min_trade_value_usd", 100))
        try:
            portfolio_equity = ac.get_portfolio_value()
        except Exception:
            portfolio_equity = 0.0

        signals = []
        for s in top:
            price     = s["price"]
            atr_stop  = s["atr_stop"]

            # Compute ATR-risk quantity when stop is valid and below entry
            atr_risk_qty = None
            if atr_stop is not None and atr_stop < price and portfolio_equity > 0:
                risk_dollars = portfolio_equity * risk_pct
                risk_per_share = price - atr_stop
                if risk_per_share > 0:
                    atr_risk_qty = round(risk_dollars / risk_per_share, 6)

                    # Notional minimum check: prevent micro-trades
                    if atr_risk_qty * price < min_trade_value:
                        log.info(
                            f"[Momentum] {s['symbol']} ATR-risk quantity {atr_risk_qty} "
                            f"(${atr_risk_qty * price:.2f}) is below min ${min_trade_value}. "
                            "Skipping signal."
                        )
                        continue

            sig: Dict = {
                "symbol":     s["symbol"],
                "action":     "buy",
                "strategy":   self.name,
                "asset_class": self.asset_class,
                "confidence": min(s["momentum"] / 0.10, 1.0),
                "reason":     f"5-day momentum: {s['momentum']:.2%}",
            }
            if atr_stop is not None:
                sig["atr_stop_price"] = atr_stop
            if atr_risk_qty is not None:
                sig["atr_risk_qty"] = atr_risk_qty

            log.info(
                f"[Momentum] Signal: BUY {s['symbol']} | momentum={s['momentum']:.2%} "
                f"| sector={_get_sector(s['symbol'])} "
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
