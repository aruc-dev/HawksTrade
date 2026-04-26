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

def calculate_position_size(price: float) -> float:
    """
    Returns the number of shares/units to buy, capped at max_position_pct of portfolio.
    Returns 0 if trade should not proceed.
    """
    if price <= 0:
        log.info(f"Invalid price for position sizing: {price}")
        return 0.0

    portfolio_value = ac.get_portfolio_value()
    cash = ac.get_cash()
    max_value = portfolio_value * T["max_position_pct"]
    affordable = min(max_value, cash)

    if affordable < T["min_trade_value_usd"]:
        log.info(f"Insufficient funds: ${affordable:.2f} < min ${T['min_trade_value_usd']}")
        return 0.0

    qty = affordable / price
    return round(qty, 6)  # supports fractional shares/crypto


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

    qty = calculate_position_size(price)
    if qty <= 0:
        result["reason"] = "Insufficient funds or below min trade value"
        return result

    result["approved"] = True
    result["qty"] = qty
    result["reason"] = "OK"
    log.info(f"Pre-trade check PASSED for {symbol}: qty={qty} @ ${price}")
    return result


# ── Exit Check (stop-loss / take-profit) ─────────────────────────────────────

def should_exit_position(symbol: str, entry_price: float, current_price: float) -> tuple:
    """
    Returns (should_exit: bool, reason: str).
    """
    sl = stop_loss_price(entry_price)
    tp = take_profit_price(entry_price)

    if current_price <= sl:
        return True, f"Stop-loss hit: {current_price:.4f} <= {sl:.4f}"
    if current_price >= tp:
        return True, f"Take-profit hit: {current_price:.4f} >= {tp:.4f}"
    return False, ""


# ── Market Regime Filter ─────────────────────────────────────────────────────

def market_regime_ok(bars_data=None) -> bool:
    """
    Returns True if SPY is above its 50-day SMA — indicates bull market regime.
    When bars_data is provided (backtest), uses pre-fetched SPY bars dict.
    In live trading, fetches from Alpaca directly.

    Backtest mode: insufficient bars (early warmup) returns True so the
    simulation can begin trading before the full 50-bar window is available.

    Live mode: any exception or insufficient bars returns False (fail closed).
    A regime filter is a safety control — when we cannot confirm conditions are
    favourable, we should block new entries rather than assume they are.
    """
    try:
        if bars_data is not None:
            # backtest mode: bars_data is a dict symbol->list of bar mocks
            spy_bars = bars_data.get("SPY")
            if spy_bars is None or len(spy_bars) < 51:
                # Early in the simulation — not enough history yet; allow trading.
                return True
            closes = pd.Series([float(b.close) if hasattr(b, 'close') else float(b['close']) for b in spy_bars])
        else:
            # live mode — fail closed if data is unavailable or insufficient
            raw = ac.get_stock_bars(["SPY"], timeframe="1Day", limit=55)
            spy_bars = raw["SPY"]
            if spy_bars is None or len(spy_bars) < 51:
                log.warning(
                    "[RegimeFilter] Insufficient SPY bars for SMA50 (%s bars); "
                    "blocking new entries (fail closed).",
                    len(spy_bars) if spy_bars is not None else 0,
                )
                return False
            closes = pd.Series([b.close for b in spy_bars])
        sma50 = closes.rolling(50).mean().iloc[-1]
        current = float(closes.iloc[-1])
        is_bull = current > sma50
        log.debug(f"[RegimeFilter] SPY={current:.2f} SMA50={sma50:.2f} bull={is_bull}")
        return is_bull
    except Exception as e:
        log.warning(
            "[RegimeFilter] Could not determine market regime: %s — "
            "blocking new entries (fail closed).", e,
        )
        return False


def crypto_regime_ok(bars_data=None) -> bool:
    """
    Returns True if BTC/USD is above its 20-day EMA — indicates crypto bull regime.
    When bars_data is provided (backtest), uses pre-fetched BTC bars.
    In live trading, fetches from Alpaca directly.

    Backtest mode: insufficient bars (early warmup) returns True so the
    simulation can begin trading before the full 20-bar window is available.

    Live mode: any exception or insufficient bars returns False (fail closed).
    A regime filter is a safety control — when we cannot confirm conditions are
    favourable, we should block new entries rather than assume they are.
    """
    try:
        if bars_data is not None:
            btc_bars = bars_data.get("BTC/USD")
            if btc_bars is None or len(btc_bars) < 21:
                # Early in the simulation — not enough history yet; allow trading.
                return True
            closes = pd.Series([float(b.close) if hasattr(b, 'close') else float(b['close']) for b in btc_bars])
        else:
            # live mode — fail closed if data is unavailable or insufficient
            raw = ac.get_crypto_bars(["BTC/USD"], timeframe="1Day", limit=25)
            btc_bars = raw["BTC/USD"]
            if btc_bars is None or len(btc_bars) < 21:
                log.warning(
                    "[CryptoRegime] Insufficient BTC/USD bars for EMA20 (%s bars); "
                    "blocking new entries (fail closed).",
                    len(btc_bars) if btc_bars is not None else 0,
                )
                return False
            closes = pd.Series([b.close for b in btc_bars])
        ema20 = closes.ewm(span=20, adjust=False).mean().iloc[-1]
        current = float(closes.iloc[-1])
        is_bull = current > ema20
        log.debug(f"[CryptoRegime] BTC={current:.2f} EMA20={ema20:.2f} bull={is_bull}")
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
