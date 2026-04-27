"""P&L calculations for the dashboard.

Pure functions, no I/O. I/O happens in data_sources.py; this module is
deterministic given its inputs so it can be unit-tested with fixture data.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from dashboard.data_sources import NY_TZ, _parse_iso, _to_utc, trades_closed_on_ny_date


def current_ny_date() -> str:
    return datetime.now(timezone.utc).astimezone(NY_TZ).date().isoformat()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def realized_pnl_for_trade(row: Dict[str, Any]) -> float:
    """Compute realized P&L (in dollars) for a single closed sell row.

    The trade log stores pnl_pct (return) and qty + entry_price. Dollar P&L is
    approximately qty * entry_price * pnl_pct. This is an approximation because
    the actual exit price is the source of truth, but pnl_pct is what the bot
    persists. For the dashboard we prefer explicit dollar math when exit_price
    is populated.
    """
    qty = _float(row.get("qty"))
    entry = _float(row.get("entry_price"))
    exit_price = _float(row.get("exit_price"))
    pnl_pct = _float(row.get("pnl_pct"))

    # Side 'sell' on a closed row is the closing leg (written by mark_trade_closed).
    if exit_price > 0 and entry > 0 and qty > 0:
        return (exit_price - entry) * qty

    # Fallback to pct-based estimate if explicit exit price is missing.
    if entry > 0 and qty > 0 and pnl_pct:
        return entry * qty * pnl_pct

    return 0.0


def _summarize_realized_rows(closed_rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    total = 0.0
    wins = 0
    losses = 0
    trade_count = 0
    per_symbol: Dict[str, float] = {}
    for row in closed_rows:
        pnl = realized_pnl_for_trade(row)
        total += pnl
        trade_count += 1
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
        symbol = (row.get("symbol") or "").strip()
        if symbol:
            per_symbol[symbol] = per_symbol.get(symbol, 0.0) + pnl
    return {
        "total_usd": round(total, 2),
        "trade_count": trade_count,
        "wins": wins,
        "losses": losses,
        "per_symbol": {k: round(v, 2) for k, v in per_symbol.items()},
    }


def realized_pnl_today(
    all_rows: Iterable[Dict[str, Any]],
    ny_date_str: Optional[str] = None,
) -> Dict[str, Any]:
    """Sum realized P&L (and count) for trades closed on the given NY date."""
    ny_date_str = ny_date_str or current_ny_date()
    closed_today = trades_closed_on_ny_date(all_rows, ny_date_str)
    return {
        "date": ny_date_str,
        **_summarize_realized_rows(closed_today),
    }


def realized_pnl_window(
    all_rows: Iterable[Dict[str, Any]],
    lookback_days: int = 7,
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Sum realized P&L over a rolling lookback window measured from now."""
    now_utc = _to_utc(now_utc or datetime.now(timezone.utc))
    lookback_days = max(1, int(lookback_days or 1))
    cutoff = now_utc - timedelta(days=lookback_days)
    closed_rows = []
    for row in all_rows:
        if (row.get("status") or "").strip().lower() != "closed":
            continue
        if (row.get("side") or "").strip().lower() != "sell":
            continue
        dt = _parse_iso(row.get("timestamp", ""))
        if dt is None:
            continue
        if _to_utc(dt) < cutoff:
            continue
        closed_rows.append(row)
    return {
        "window_days": lookback_days,
        "window_start_utc": cutoff.isoformat(),
        "window_end_utc": now_utc.isoformat(),
        **_summarize_realized_rows(closed_rows),
    }


def unrealized_pnl_summary(positions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate unrealized P&L across positions, split by asset class."""
    total = 0.0
    crypto_total = 0.0
    stock_total = 0.0
    crypto_count = 0
    stock_count = 0
    for p in positions:
        pl = _float(p.get("unrealized_pl"))
        total += pl
        ac = (p.get("asset_class") or "").lower()
        symbol = (p.get("symbol") or "")
        is_crypto = "crypto" in ac or "/" in symbol
        if is_crypto:
            crypto_total += pl
            crypto_count += 1
        else:
            stock_total += pl
            stock_count += 1
    return {
        "total_usd": round(total, 2),
        "crypto_usd": round(crypto_total, 2),
        "stock_usd": round(stock_total, 2),
        "crypto_count": crypto_count,
        "stock_count": stock_count,
        "position_count": len(positions),
    }


def daily_loss_headroom(
    baseline: Optional[Dict[str, Any]],
    current_portfolio_value: float,
    daily_loss_limit_pct: float,
) -> Dict[str, Any]:
    """Compute how far the portfolio is from the daily-loss kill switch.

    Returns:
      {
        'baseline_value': float,
        'current_value': float,
        'delta_usd': float  # current - baseline (positive = green day)
        'delta_pct': float  # as a decimal, 0.012 = +1.2%
        'limit_pct': float  # e.g. 0.05
        'limit_usd': float  # baseline * limit_pct
        'remaining_usd': float  # how much more loss we can absorb before tripping
        'status': 'ok' | 'warn' | 'critical' | 'unknown'
      }
    """
    out = {
        "baseline_value": 0.0,
        "current_value": round(float(current_portfolio_value or 0), 2),
        "delta_usd": 0.0,
        "delta_pct": 0.0,
        "limit_pct": float(daily_loss_limit_pct),
        "limit_usd": 0.0,
        "remaining_usd": 0.0,
        "status": "unknown",
    }
    if not baseline or not isinstance(baseline, dict):
        return out
    base = _float(baseline.get("portfolio_value"))
    if base <= 0:
        return out
    out["baseline_value"] = round(base, 2)
    delta = out["current_value"] - base
    out["delta_usd"] = round(delta, 2)
    out["delta_pct"] = round(delta / base, 6) if base else 0.0
    out["limit_usd"] = round(base * daily_loss_limit_pct, 2)
    # Remaining = limit_usd - current loss. Positive = we have headroom.
    loss = -delta if delta < 0 else 0.0
    out["remaining_usd"] = round(out["limit_usd"] - loss, 2)

    # Status: critical if loss exceeds 80% of limit; warn at 50%; else ok.
    if base > 0:
        loss_pct = loss / base
        if loss_pct >= daily_loss_limit_pct:
            out["status"] = "tripped"
        elif loss_pct >= 0.8 * daily_loss_limit_pct:
            out["status"] = "critical"
        elif loss_pct >= 0.5 * daily_loss_limit_pct:
            out["status"] = "warn"
        else:
            out["status"] = "ok"
    return out


def realized_pnl_all_time(all_rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Sum realized P&L across all recorded trades (no time window)."""
    closed_rows = [
        r for r in all_rows
        if (r.get("status") or "").strip().lower() == "closed"
        and (r.get("side") or "").strip().lower() == "sell"
    ]
    return _summarize_realized_rows(closed_rows)


def active_days_since_first_trade(
    all_rows: Iterable[Dict[str, Any]],
    now_utc: Optional[datetime] = None,
) -> Optional[int]:
    """Return number of calendar days since the first trade in the log.

    Returns None when the trade log is empty.
    """
    now_utc = _to_utc(now_utc or datetime.now(timezone.utc))
    earliest: Optional[datetime] = None
    for row in all_rows:
        dt = _parse_iso(row.get("timestamp", ""))
        if dt is None:
            continue
        dt_utc = _to_utc(dt)
        if earliest is None or dt_utc < earliest:
            earliest = dt_utc
    if earliest is None:
        return None
    return max(0, (now_utc - earliest).days)


def strategy_summary(
    closed_rows: Iterable[Dict[str, Any]],
    lookback_days: int = 30,
    now_utc: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Per-strategy win rate + trade count over the last N days.

    A 'trade' for this summary is one closed sell row.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    cutoff = now_utc.timestamp() - (lookback_days * 86400)
    by_strategy: Dict[str, Dict[str, Any]] = {}
    for row in closed_rows:
        if (row.get("status") or "").strip().lower() != "closed":
            continue
        if (row.get("side") or "").strip().lower() != "sell":
            continue
        dt = _parse_iso(row.get("timestamp", ""))
        if dt is None:
            continue
        if _to_utc(dt).timestamp() < cutoff:
            continue
        strategy = (row.get("strategy") or "unknown").strip()
        bucket = by_strategy.setdefault(
            strategy,
            {"strategy": strategy, "count": 0, "wins": 0, "losses": 0, "total_usd": 0.0},
        )
        bucket["count"] += 1
        pnl = realized_pnl_for_trade(row)
        bucket["total_usd"] += pnl
        if pnl > 0:
            bucket["wins"] += 1
        elif pnl < 0:
            bucket["losses"] += 1

    out: List[Dict[str, Any]] = []
    for s in by_strategy.values():
        count = s["count"]
        s["win_rate"] = round(s["wins"] / count, 4) if count else 0.0
        s["total_usd"] = round(s["total_usd"], 2)
        out.append(s)
    out.sort(key=lambda x: x["strategy"])
    return out
