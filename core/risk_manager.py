"""
HawksTrade - Risk Manager
==========================
Enforces all risk rules before any order is placed:
  - Max position size as % of portfolio
  - Max number of open positions
  - Daily loss limit (hard stop)
  - Stop-loss / take-profit price calculation
  - Intraday trading gate
"""

import json
import logging
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from core import alpaca_client as ac
from core.config_loader import get_config

BASE_DIR = Path(__file__).resolve().parent.parent
CFG = get_config()

T = CFG["trading"]
INTRADAY_ENABLED = CFG["intraday"]["enabled"]

log = logging.getLogger("risk_manager")


def _cfg_int(value, default: int, *, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)


def _cfg_float(value, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return parsed


def _crypto_regime_filter_config() -> dict:
    return (CFG.get("crypto", {}) or {}).get("regime_filter", {}) or {}


def crypto_regime_required_bars() -> int:
    cfg = _crypto_regime_filter_config()
    ema_period = _cfg_int(cfg.get("ema_period"), 20, minimum=1)
    slope_lookback = _cfg_int(cfg.get("min_ema_slope_lookback_days"), 0, minimum=0)
    return max(ema_period + 1, ema_period + slope_lookback + 1)


# ── Daily P&L Tracking ───────────────────────────────────────────────────────

_session_start_value: Optional[float] = None
_session_date: Optional[date] = None
DAILY_BASELINE_FILE = BASE_DIR / "data" / "daily_loss_baseline.json"
TRADING_SESSION_TIMEZONE = "America/New_York"
_TRADING_SESSION_TZ = ZoneInfo(TRADING_SESSION_TIMEZONE)


def _current_trading_session_date(now: Optional[datetime] = None) -> date:
    """Return the risk-session date in the market timezone, not host local time."""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(_TRADING_SESSION_TZ).date()


def _load_daily_baseline(today: date) -> Optional[float]:
    if not DAILY_BASELINE_FILE.exists():
        return None
    try:
        with open(DAILY_BASELINE_FILE, "r") as f:
            data = json.load(f)
        if data.get("date") != today.isoformat():
            return None
        value = float(data.get("portfolio_value", 0))
        return value if value > 0 else None
    except Exception as e:
        log.warning(f"Could not read daily loss baseline; creating a new one: {e}")
        return None


def _save_daily_baseline(today: date, portfolio_value: float):
    DAILY_BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = DAILY_BASELINE_FILE.with_suffix(".tmp")
    payload = {
        "date": today.isoformat(),
        "portfolio_value": float(portfolio_value),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "session_timezone": TRADING_SESSION_TIMEZONE,
    }
    with open(tmp_path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    tmp_path.replace(DAILY_BASELINE_FILE)


def _refresh_session():
    global _session_start_value, _session_date
    today = _current_trading_session_date()
    if _session_date != today:
        _session_start_value = _load_daily_baseline(today)
        if _session_start_value is None:
            _session_start_value = ac.get_portfolio_value()
            _save_daily_baseline(today, _session_start_value)
        _session_date = today
        log.info(
            "Session start portfolio value: "
            f"${_session_start_value:,.2f} on {today} ({TRADING_SESSION_TIMEZONE})"
        )


def daily_loss_exceeded() -> bool:
    """Returns True if the portfolio has fallen more than daily_loss_limit_pct today."""
    _refresh_session()
    current = ac.get_portfolio_value()
    if not _session_start_value or _session_start_value <= 0:
        log.warning(
            "Invalid session start portfolio value "
            f"({_session_start_value}); daily loss check skipped."
        )
        return False
    loss_pct = (_session_start_value - current) / _session_start_value
    if loss_pct >= T["daily_loss_limit_pct"]:
        log.warning(
            f"DAILY LOSS LIMIT HIT: lost {loss_pct:.1%} "
            f"(limit={T['daily_loss_limit_pct']:.1%}). No new trades today."
        )
        return True
    return False


# ── Position Count ────────────────────────────────────────────────────────────

def _is_crypto_symbol(symbol: str, asset_class: Optional[str] = None) -> bool:
    """Return True if the symbol/asset_class represents a crypto position."""
    if asset_class is not None:
        ac_lower = str(asset_class).lower()
        if "crypto" in ac_lower:
            return True
        if ac_lower in ("us_equity", "stock", "equity"):
            return False
    return "/" in (symbol or "")


def _classify_position(pos) -> str:
    """Return 'crypto' or 'stock' for an Alpaca position-like object or dict."""
    if isinstance(pos, dict):
        symbol = pos.get("symbol", "")
        asset_class = pos.get("asset_class")
    else:
        symbol = getattr(pos, "symbol", "")
        asset_class = getattr(pos, "asset_class", None)
        if asset_class is not None and hasattr(asset_class, "value"):
            asset_class = asset_class.value
    return "crypto" if _is_crypto_symbol(symbol, asset_class) else "stock"


def _position_counts() -> tuple:
    """Return (total, crypto_count, stock_count) from current Alpaca positions."""
    positions = ac.get_all_positions() or []
    crypto_count = 0
    stock_count = 0
    for p in positions:
        if _classify_position(p) == "crypto":
            crypto_count += 1
        else:
            stock_count += 1
    return len(positions), crypto_count, stock_count


def _get_crypto_limits() -> tuple:
    """Return (max_crypto, min_crypto) with safe defaults and invariant enforcement."""
    max_total = int(T.get("max_positions", 0))
    # Sensible defaults if keys missing: crypto unrestricted up to global cap.
    max_crypto = int(T.get("max_crypto_positions", max_total))
    min_crypto = int(T.get("min_crypto_positions", 0))
    # Enforce invariant: 0 <= min <= max <= max_positions
    max_crypto = max(0, min(max_crypto, max_total))
    min_crypto = max(0, min(min_crypto, max_crypto))
    return max_crypto, min_crypto


def max_positions_reached(asset_class: Optional[str] = None, symbol: str = "") -> bool:
    """
    Check asset-class-aware position caps.
    - Global cap: total positions >= max_positions.
    - Crypto cap: crypto positions >= max_crypto_positions (blocks crypto entries only).
    - Stock reservation: stock positions >= max_positions - min_crypto_positions
      (blocks stock entries to preserve reserved crypto slots).
    When called with no args, preserves legacy behavior (global cap only).
    """
    total, crypto_count, stock_count = _position_counts()
    max_total = T["max_positions"]
    max_crypto, min_crypto = _get_crypto_limits()

    if total >= max_total:
        log.info(f"Max positions reached: {total}/{max_total}")
        return True

    if asset_class is None and not symbol:
        return False

    is_crypto = _is_crypto_symbol(symbol, asset_class)
    if is_crypto:
        if max_crypto <= 0:
            log.info(
                f"Crypto entries disabled (max_crypto_positions={max_crypto})."
            )
            return True
        if crypto_count >= max_crypto:
            log.info(
                f"Max crypto positions reached: {crypto_count}/{max_crypto}"
            )
            return True
    else:
        # Stock entries are blocked if they would saturate slots reserved for crypto.
        stock_slots_available = max_total - min_crypto
        if stock_count >= stock_slots_available:
            log.info(
                f"Stock slots exhausted: {stock_count}/{stock_slots_available} "
                f"({min_crypto} reserved for crypto)"
            )
            return True
    return False


# ── Position Sizing ───────────────────────────────────────────────────────────

def position_size_limits(price: float) -> dict:
    """Return portfolio, cash, and base position cap quantities for a price."""
    if price <= 0:
        log.info(f"Invalid price for position sizing: {price}")
        return {
            "portfolio_value": 0.0,
            "cash": 0.0,
            "base_position_value": 0.0,
            "affordable_value": 0.0,
            "base_cap_qty": 0.0,
            "cash_qty": 0.0,
            "qty": 0.0,
        }

    portfolio_value = float(ac.get_portfolio_value())
    cash = float(ac.get_cash())
    base_position_value = portfolio_value * T["max_position_pct"]
    affordable_value = min(base_position_value, cash)

    return {
        "portfolio_value": portfolio_value,
        "cash": cash,
        "base_position_value": base_position_value,
        "affordable_value": affordable_value,
        "base_cap_qty": round(base_position_value / price, 6),
        "cash_qty": round(cash / price, 6),
        "qty": round(affordable_value / price, 6),
    }


def calculate_position_size(price: float) -> float:
    """
    Returns the number of shares/units to buy, capped at max_position_pct of portfolio.
    Returns 0 if trade should not proceed.
    """
    limits = position_size_limits(price)
    if price <= 0:
        return 0.0
    affordable = limits["affordable_value"]

    if affordable < T["min_trade_value_usd"]:
        log.info(f"Insufficient funds: ${affordable:.2f} < min ${T['min_trade_value_usd']}")
        return 0.0

    return limits["qty"]  # supports fractional shares/crypto


def cap_position_qty(price: float, qty: float) -> float:
    """Clamp a requested quantity to the configured per-position value cap."""
    if price <= 0 or qty <= 0:
        return 0.0
    max_qty = calculate_position_size(price)
    if max_qty <= 0:
        return 0.0
    return round(min(float(qty), max_qty), 6)


# ── Stop-Loss / Take-Profit ───────────────────────────────────────────────────

def stop_loss_price(entry_price: float) -> float:
    return round(entry_price * (1 - T["stop_loss_pct"]), 4)


def take_profit_price(entry_price: float) -> float:
    return round(entry_price * (1 + T["take_profit_pct"]), 4)


# ── Intraday Gate ─────────────────────────────────────────────────────────────

def intraday_allowed() -> bool:
    """Returns True if intraday trading is permitted by config."""
    if not INTRADAY_ENABLED:
        log.debug("Intraday trading is disabled in config.")
    return INTRADAY_ENABLED


# ── Master Pre-Trade Check ────────────────────────────────────────────────────

def pre_trade_check(price: float, symbol: str, asset_class: Optional[str] = None) -> dict:
    """
    Run all risk checks before entering a trade.
    Returns dict with 'approved' bool, 'qty', and 'reason'.
    When asset_class is provided ('crypto' or 'stock'), applies asset-class-aware
    position caps (max_crypto_positions, min_crypto_positions reservation).
    """
    result = {"approved": False, "qty": 0.0, "reason": ""}

    if daily_loss_exceeded():
        result["reason"] = "Daily loss limit exceeded"
        return result

    if max_positions_reached(asset_class=asset_class, symbol=symbol):
        is_crypto = _is_crypto_symbol(symbol, asset_class)
        max_crypto, min_crypto = _get_crypto_limits()
        if is_crypto and max_crypto <= 0:
            result["reason"] = "Crypto trading disabled (max_crypto_positions=0)"
        elif is_crypto:
            result["reason"] = f"Max crypto positions reached ({max_crypto})"
        elif min_crypto > 0:
            result["reason"] = "Stock slots exhausted (max_positions - min_crypto_positions reservation)"
        else:
            result["reason"] = "Max open positions reached"
        return result

    if price <= 0:
        result["reason"] = f"Invalid price for {symbol}: {price}"
        return result

    limits = position_size_limits(price)
    qty = limits["qty"]
    if limits["affordable_value"] < T["min_trade_value_usd"] or qty <= 0:
        result["reason"] = "Insufficient funds or below min trade value"
        return result

    result["approved"] = True
    result["qty"] = qty
    result["reason"] = "OK"
    result.update({
        "portfolio_value": limits["portfolio_value"],
        "cash": limits["cash"],
        "base_cap_qty": limits["base_cap_qty"],
        "cash_qty": limits["cash_qty"],
        "base_position_value": limits["base_position_value"],
    })
    log.info(f"Pre-trade check PASSED for {symbol}: qty={qty} @ ${price}")
    return result


# ── Exit Check (stop-loss / take-profit) ─────────────────────────────────────

def should_exit_position(
    symbol: str,
    entry_price: float,
    current_price: float,
    custom_stop_price: float | None = None,
    allow_custom_stop_widening: bool = True,
) -> tuple:
    """
    Returns (should_exit: bool, reason: str).

    custom_stop_price: when provided (for example, an ATR stop computed at entry),
    the effective stop is selected from the global fixed-percentage stop and the
    custom absolute price level. By default, custom stops may widen risk by using
    the lower stop price. Set allow_custom_stop_widening=False when the global
    stop must remain the maximum permitted loss; tighter custom stops are still
    honored in that mode.
    """
    global_sl = stop_loss_price(entry_price)
    if custom_stop_price is not None and (not math.isfinite(custom_stop_price) or custom_stop_price <= 0):
        custom_stop_price = None
    if custom_stop_price is None:
        sl = global_sl
    elif allow_custom_stop_widening:
        sl = min(global_sl, custom_stop_price)
    else:
        sl = max(global_sl, custom_stop_price)
    tp = take_profit_price(entry_price)

    if current_price <= sl:
        label = "Custom stop-loss" if custom_stop_price is not None and sl == custom_stop_price else "Stop-loss"
        return True, f"{label} hit: {current_price:.4f} <= {sl:.4f}"
    if current_price >= tp:
        return True, f"Take-profit hit: {current_price:.4f} >= {tp:.4f}"
    return False, ""


# ── Market Regime Filter ─────────────────────────────────────────────────────

def _get_closes(bars: list) -> pd.Series:
    """Safely extract close prices from a list of bar objects (Alpaca SDK, SimpleNamespace, or dict)."""
    vals = []
    for b in bars:
        if hasattr(b, "close"):
            vals.append(float(b.close))
        elif isinstance(b, dict) and "close" in b:
            vals.append(float(b["close"]))
        else:
            # Fallback for other object types that might have close as a property but not seen by hasattr
            try:
                vals.append(float(b.close))
            except Exception:
                vals.append(np.nan)
    return pd.Series(vals)


def market_regime_ok(bars_data=None, allow_warmup: bool = False) -> bool:
    """
    Returns True if SPY is above its 50-day SMA — indicates bull market regime.
    If SPY is below SMA50 but QQQ is above SMA50, it also returns True (Bifurcation Detection).
    
    When bars_data is provided (backtest), uses pre-fetched bars dict.
    In live trading, fetches from Alpaca directly.

    When allow_warmup=True: insufficient supplied bars return True for
    backtest warmup only.
    Live mode: any exception or insufficient bars returns False (fail closed).
    """
    try:
        if bars_data is not None and not bars_data:
            log.warning("[RegimeFilter] Empty regime bars supplied; blocking new entries (fail closed).")
            return False

        def _is_above_sma50(symbol: str) -> bool:
            if bars_data is not None:
                bars = bars_data.get(symbol)
                if bars is None or len(bars) < 51:
                    if allow_warmup:
                        return True
                    log.warning(f"[RegimeFilter] Insufficient supplied {symbol} bars for SMA50; fail closed.")
                    return False
                closes = _get_closes(bars)
            else:
                raw = ac.get_stock_bars([symbol], timeframe="1Day", limit=55)
                bars = raw[symbol]
                if bars is None or len(bars) < 51:
                    log.warning(f"[RegimeFilter] Insufficient {symbol} bars for SMA50; fail closed.")
                    return False
                closes = _get_closes(bars)
            
            sma50 = closes.rolling(50).mean().iloc[-1]
            current = float(closes.iloc[-1])
            return current > sma50

        spy_bull = _is_above_sma50("SPY")
        if spy_bull:
            log.debug("[RegimeFilter] SPY above SMA50 - Bull regime confirmed.")
            return True
            
        qqq_bull = _is_above_sma50("QQQ")
        if qqq_bull:
            log.info("[RegimeFilter] SPY below SMA50 but QQQ above SMA50 - Bifurcation detected, allowing entries.")
            return True

        log.debug("[RegimeFilter] Both SPY and QQQ below SMA50 - Bear regime confirmed.")
        return False

    except Exception as e:
        log.warning(
            "[RegimeFilter] Could not determine market regime: %s — "
            "blocking new entries (fail closed).", e,
        )
        return False


def market_breadth_pct(universe: list, bars_data: dict | None = None) -> float:
    """
    Returns the fraction (0.0–1.0) of universe symbols trading above their own
    50-day SMA. When bars_data is provided (scan already fetched), reuses it;
    otherwise fetches from Alpaca live.

    Returns 0.5 (neutral) when data is unavailable or insufficient, so callers
    can treat an unknown breadth as neither triggering Yellow nor Red thresholds.
    """
    try:
        source = ac.get_stock_bars(universe, timeframe="1Day", limit=55) if bars_data is None else bars_data
        # Normalize to a plain dict so both live BarSet objects and dicts work.
        fetched: dict = {}
        for sym in universe:
            try:
                b = source[sym]
                if b is not None:
                    fetched[sym] = b
            except Exception:
                pass

        above = 0
        eligible = 0
        for sym in universe:
            bars = fetched.get(sym)
            if bars is None or len(bars) < 51:
                continue
            closes = pd.Series([
                float(b.close) if hasattr(b, "close") else float(b["close"])
                for b in bars
            ])
            sma50 = closes.rolling(50).mean().iloc[-1]
            eligible += 1
            if float(closes.iloc[-1]) > sma50:
                above += 1

        if eligible == 0:
            log.debug("[Breadth] Insufficient bars for breadth calculation — returning neutral 0.5")
            return 0.5
        breadth = above / eligible
        log.debug(f"[Breadth] {above}/{eligible} symbols above SMA50 = {breadth:.1%}")
        return breadth
    except Exception as e:
        log.warning(f"[Breadth] Could not compute market breadth: {e} — returning neutral 0.5")
        return 0.5


def crypto_regime_ok(bars_data=None, allow_warmup: bool = False) -> bool:
    """
    Returns True if BTC/USD is above its configured EMA and the EMA slope guard
    is not deteriorating beyond the configured tolerance.
    When bars_data is provided (backtest), uses pre-fetched BTC bars.
    In live trading, fetches from Alpaca directly.

    When allow_warmup=True: insufficient supplied bars return True so backtests
    can begin before the full 20-bar window is available.

    Live mode: any exception or insufficient bars returns False (fail closed).
    A regime filter is a safety control — when we cannot confirm conditions are
    favourable, we should block new entries rather than assume they are.
    """
    try:
        regime_cfg = _crypto_regime_filter_config()
        ema_period = _cfg_int(regime_cfg.get("ema_period"), 20, minimum=1)
        slope_lookback = _cfg_int(regime_cfg.get("min_ema_slope_lookback_days"), 0, minimum=0)
        min_ema_slope = _cfg_float(regime_cfg.get("min_ema_slope_pct"), float("-inf"))
        min_price_above_ema = _cfg_float(regime_cfg.get("min_price_above_ema_pct"), 0.0)
        required_bars = crypto_regime_required_bars()

        if bars_data is not None and not bars_data:
            log.warning("[CryptoRegime] Empty regime bars supplied; blocking new entries (fail closed).")
            return False

        if bars_data is not None:
            btc_bars = bars_data.get("BTC/USD")
            if btc_bars is None or len(btc_bars) < required_bars:
                if allow_warmup:
                    return True
                log.warning(
                    "[CryptoRegime] Insufficient supplied BTC/USD bars for EMA%s + slope guard; "
                    "blocking new entries (fail closed).",
                    ema_period,
                )
                return False
            closes = _get_closes(btc_bars)
        else:
            # live mode — fail closed if data is unavailable or insufficient
            raw = ac.get_crypto_bars(["BTC/USD"], timeframe="1Day", limit=max(required_bars, 25))
            btc_bars = raw["BTC/USD"]
            if btc_bars is None or len(btc_bars) < required_bars:
                log.warning(
                    "[CryptoRegime] Insufficient BTC/USD bars for EMA%s + slope guard (%s bars); "
                    "blocking new entries (fail closed).",
                    ema_period,
                    len(btc_bars) if btc_bars is not None else 0,
                )
                return False
            closes = _get_closes(btc_bars)
        ema = closes.ewm(span=ema_period, adjust=False).mean()
        ema_now = float(ema.iloc[-1])
        current = float(closes.iloc[-1])
        price_threshold = ema_now * (1 + min_price_above_ema)
        is_bull = current > price_threshold
        ema_slope = 0.0
        if is_bull and slope_lookback > 0:
            ema_then = float(ema.iloc[-(slope_lookback + 1)])
            ema_slope = (ema_now / ema_then) - 1 if ema_then > 0 else float("-inf")
            is_bull = ema_slope >= min_ema_slope
        log.debug(
            "[CryptoRegime] BTC=%.2f EMA%s=%.2f price_threshold=%.2f "
            "ema_slope_%sd=%.2f%% min_slope=%.2f%% bull=%s",
            current,
            ema_period,
            ema_now,
            price_threshold,
            slope_lookback,
            ema_slope * 100,
            min_ema_slope * 100,
            is_bull,
        )
        return is_bull
    except Exception as e:
        log.warning(
            "[CryptoRegime] Could not determine crypto regime: %s — "
            "blocking new entries (fail closed).", e,
        )
        return False


# ── Kelly Criterion Position Sizing ──────────────────────────────────────────

def kelly_position_size(win_rate: float = None, avg_win_pct: float = None,
                        avg_loss_pct: float = None, price: float = 0.0) -> float:
    """
    Half-Kelly position sizing. If win_rate/avg_win_pct/avg_loss_pct are None,
    reads the last 30 closed momentum trades from the trade log to compute them dynamically.
    Falls back to standard calculate_position_size if parameters are invalid or insufficient data.
    Caps position at trading.max_position_pct of portfolio and floors at 1%
    only when that floor is below the configured cap.
    """
    if price <= 0:
        log.warning(f"[Kelly] Invalid price {price}; falling back to standard sizing.")
        return calculate_position_size(price)

    try:
        # Attempt to load dynamic params from recent trade history
        if win_rate is None or avg_win_pct is None or avg_loss_pct is None:
            try:
                from tracking.trade_log import get_closed_trades
                recent = [t for t in get_closed_trades() if t.get("strategy") == "momentum"][-30:]
                if len(recent) >= 10:  # need at least 10 trades for meaningful stats
                    wins = [t for t in recent if float(t.get("pnl_pct", 0)) > 0]
                    losses = [t for t in recent if float(t.get("pnl_pct", 0)) <= 0]
                    win_rate = len(wins) / len(recent)
                    avg_win_pct = float(np.mean([float(t["pnl_pct"]) for t in wins])) if wins else 0.14
                    avg_loss_pct = abs(float(np.mean([float(t["pnl_pct"]) for t in losses]))) if losses else 0.054
                    log.debug(f"[Kelly] Dynamic params: WR={win_rate:.3f} win={avg_win_pct:.3f} loss={avg_loss_pct:.3f} (n={len(recent)})")
                else:
                    # Fall back to v3 defaults
                    win_rate = 0.567
                    avg_win_pct = 0.1398
                    avg_loss_pct = 0.0543
            except Exception:
                win_rate = 0.567
                avg_win_pct = 0.1398
                avg_loss_pct = 0.0543

        if avg_loss_pct == 0 or win_rate <= 0 or win_rate >= 1:
            return calculate_position_size(price)
        b = abs(avg_win_pct / avg_loss_pct)
        kelly_f = (win_rate * b - (1 - win_rate)) / b
        half_kelly = kelly_f / 2
        portfolio_value = ac.get_portfolio_value()
        cash = ac.get_cash()
        max_pct = float(T["max_position_pct"])
        min_pct = min(0.01, max_pct)
        pct = max(min_pct, min(half_kelly, max_pct))
        max_value = portfolio_value * pct
        affordable = min(max_value, cash)
        if affordable < T["min_trade_value_usd"]:
            return 0.0
        return round(affordable / price, 6)
    except Exception as e:
        log.warning(f"[Kelly] Fallback to standard sizing: {e}")
        return calculate_position_size(price)
