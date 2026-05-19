"""
HawksTrade - Crypto RSI Mean Reversion Strategy
===============================================
Buys short-lived crypto pullbacks when daily RSI and Bollinger %B both show
oversold pressure, then exits on mean reversion, RSI recovery, stop, or a short
time cap. This sleeve is deliberately separate from the stock RSI strategy
because crypto trades 24/7 and should not inherit SPY/VIX regime filters.
"""

from __future__ import annotations

import logging
from typing import Dict, List

import pandas as pd

from core import alpaca_client as ac
from core.config_loader import get_config
from core import risk_manager as rm
from strategies.base_strategy import BaseStrategy
from strategies.rsi_reversion import _bollinger_pct_b, _calc_rsi

CFG = get_config()
SCFG = CFG["strategies"]["crypto_rsi_reversion"]
log = logging.getLogger("strategy.crypto_rsi_reversion")


def _symbol_lookup_keys(symbol: str):
    raw_symbol = str(symbol or "").strip().upper()
    pair_symbol = ac.to_crypto_pair_symbol(raw_symbol)
    normalized_symbol = ac.normalize_symbol(pair_symbol)
    return tuple(dict.fromkeys((pair_symbol, raw_symbol, normalized_symbol)))


def _symbol_bars(bars_data, symbol: str):
    """Return bars for slashed or slashless crypto symbols without raising."""
    for key in _symbol_lookup_keys(symbol):
        try:
            bars = bars_data.get(key) if hasattr(bars_data, "get") else bars_data[key]
        except (AttributeError, KeyError, TypeError):
            bars = None
        if bars is not None and len(bars) > 0:
            return bars
    return None


def _series_from_bars(bars, field: str) -> pd.Series:
    return pd.Series([
        float(getattr(bar, field)) if hasattr(bar, field) else float(bar[field])
        for bar in bars
    ])


class CryptoRSIReversionStrategy(BaseStrategy):
    name = "crypto_rsi_reversion"
    asset_class = "crypto"

    def scan(self, universe: List[str], **kwargs) -> List[Dict]:
        if not SCFG.get("enabled", False):
            return []

        period = int(SCFG.get("rsi_period", 14))
        oversold = float(SCFG.get("oversold_threshold", 35))
        bb_period = int(SCFG.get("bb_period", 20))
        bb_std = float(SCFG.get("bb_std", 2.0))
        max_signals = int(SCFG.get("max_signals", 3))
        stop_pct = float(SCFG.get("max_loss_exit_pct", 0.10))
        limit = max(period + 30, bb_period + 30, 80)

        log.info(
            "[CryptoRSI] Scanning %s crypto pairs (RSI<%.1f, %%B<%.0f%%)...",
            len(universe),
            oversold,
            float(SCFG.get("max_bollinger_pct_b", 0.40)) * 100.0,
        )

        try:
            bars_data = ac.get_crypto_bars(universe, timeframe=SCFG.get("timeframe", "1Day"), limit=limit)
        except Exception as exc:
            log.error("[CryptoRSI] Failed to fetch crypto bars: %s", exc)
            return []

        if bool(SCFG.get("use_regime_filter", True)):
            regime_bars = kwargs.get("regime_bars")
            if not rm.crypto_regime_ok(
                bars_data=regime_bars,
                allow_warmup=bool(kwargs.get("allow_regime_warmup", False)),
            ):
                log.info("[CryptoRSI] Crypto regime filter blocked entries, skipping scan.")
                return []

        max_pct_b = float(SCFG.get("max_bollinger_pct_b", 0.40))
        min_recovery_pct = float(SCFG.get("min_recovery_pct", 0.0) or 0.0)
        signals = []

        for symbol in universe:
            try:
                bars = _symbol_bars(bars_data, symbol)
                if bars is None or len(bars) < limit:
                    continue

                closes = _series_from_bars(bars, "close")
                price = float(closes.iloc[-1])
                if price <= 0:
                    continue

                rsi = _calc_rsi(closes, period)
                pct_b = _bollinger_pct_b(closes, bb_period, bb_std)
                if not (rsi < oversold and pct_b < max_pct_b):
                    continue

                if min_recovery_pct > 0 and len(closes) >= 2:
                    recovery = (float(closes.iloc[-1]) / float(closes.iloc[-2])) - 1.0
                    if recovery < min_recovery_pct:
                        continue

                stop_price = price * (1.0 - stop_pct)
                if stop_price <= 0:
                    continue

                confidence = min(1.0, max(0.0, (oversold - rsi) / max(oversold, 1.0)))
                signals.append({
                    "symbol": ac.to_crypto_pair_symbol(symbol),
                    "action": "buy",
                    "strategy": self.name,
                    "asset_class": self.asset_class,
                    "confidence": round(confidence, 3),
                    "atr_stop_price": stop_price,
                    "reason": (
                        f"Crypto RSI mean reversion: RSI={rsi:.1f}<{oversold:.1f}, "
                        f"%B={pct_b:.1%}, stop@{stop_price:.4f}"
                    ),
                })
            except Exception as exc:
                log.warning("[CryptoRSI] Error for %s: %s", symbol, exc)

        signals.sort(key=lambda row: row["confidence"], reverse=True)
        selected = signals[:max_signals]
        for sig in selected:
            log.info("[CryptoRSI] Signal: BUY %s | %s", sig["symbol"], sig["reason"])
        return selected

    def should_exit(self, symbol: str, entry_price: float) -> tuple:
        period = int(SCFG.get("rsi_period", 14))
        bb_period = int(SCFG.get("bb_period", 20))
        overbought = float(SCFG.get("overbought_threshold", 55))
        profit_floor = float(SCFG.get("profit_floor_pct", 0.03))
        max_loss = float(SCFG.get("max_loss_exit_pct", 0.10))
        limit = max(period + 10, bb_period + 5)

        try:
            bars_data = ac.get_crypto_bars([symbol], timeframe=SCFG.get("timeframe", "1Day"), limit=limit)
            bars = _symbol_bars(bars_data, symbol)
            if bars is None or len(bars) < limit:
                return False, ""

            closes = _series_from_bars(bars, "close")
            price = float(closes.iloc[-1])
            if price <= entry_price * (1.0 - max_loss):
                return True, (
                    f"Crypto RSI max-loss exit: close {price:.4f} <= "
                    f"entry {entry_price:.4f} - {max_loss:.1%}"
                )

            sma_target = float(closes.rolling(bb_period).mean().iloc[-1])
            target = max(sma_target, entry_price * (1.0 + profit_floor))
            if price >= target:
                return True, f"Crypto mean target reached: {price:.4f} >= {target:.4f}"

            rsi = _calc_rsi(closes, period)
            if rsi > overbought and price > entry_price:
                return True, f"Crypto RSI recovered: {rsi:.1f} > {overbought:.1f}"
        except Exception as exc:
            log.warning("[CryptoRSI] Exit check error for %s: %s", symbol, exc)

        return False, ""
