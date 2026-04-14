"""
HawksTrade - Performance Analytics
=====================================
Reads trades.csv and computes:
  - Total / monthly / weekly P&L
  - Win rate
  - Max drawdown
  - Strategy-level breakdown
  - Outputs both to log and as a formatted string for reports
"""

import logging
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Dict

import yaml
import pandas as pd

BASE_DIR  = Path(__file__).resolve().parent.parent
with open(BASE_DIR / "config" / "config.yaml") as f:
    CFG = yaml.safe_load(f)

TRADE_LOG = BASE_DIR / CFG["reporting"]["trade_log_file"]
PERF_FILE = BASE_DIR / CFG["reporting"]["performance_file"]
log = logging.getLogger("performance")


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def load_closed_trades() -> pd.DataFrame:
    if not TRADE_LOG.exists():
        return pd.DataFrame()
    df = pd.read_csv(TRADE_LOG)
    df = df[(df["status"] == "closed") & (df["side"] == "sell")].copy()
    if df.empty:
        return df
    timestamps = pd.to_datetime(df["timestamp"], format="mixed", utc=True, errors="coerce")
    df = df[timestamps.notna()].copy()
    df["timestamp"] = timestamps[timestamps.notna()].dt.tz_convert(None)
    df["pnl_pct"]    = pd.to_numeric(df["pnl_pct"], errors="coerce").fillna(0)
    df["qty"]         = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
    df["entry_price"] = pd.to_numeric(df["entry_price"], errors="coerce")
    df["exit_price"]  = pd.to_numeric(df["exit_price"], errors="coerce")
    df["realized_pnl_dollars"] = (df["exit_price"] - df["entry_price"]) * df["qty"]
    return df


def _position_value(position, name: str, default=None):
    if isinstance(position, dict):
        return position.get(name, default)
    return getattr(position, name, default)


def load_open_positions() -> pd.DataFrame:
    try:
        from core import alpaca_client as ac

        positions = ac.get_all_positions()
    except Exception as e:
        log.warning(f"Could not load open positions for performance report: {e}")
        return pd.DataFrame()

    rows = []
    for pos in positions or []:
        qty = float(_position_value(pos, "qty", 0) or 0)
        entry = float(_position_value(pos, "avg_entry_price", 0) or 0)
        current = float(_position_value(pos, "current_price", entry) or entry)
        raw_unrealized = _position_value(pos, "unrealized_pl", None)
        if raw_unrealized in (None, ""):
            unrealized = (current - entry) * qty
        else:
            unrealized = float(raw_unrealized)
        rows.append({
            "symbol": _position_value(pos, "symbol", ""),
            "qty": abs(qty),
            "entry_price": entry,
            "current_price": current,
            "unrealized_pnl_dollars": unrealized,
        })
    return pd.DataFrame(rows)


def compute_summary(df: pd.DataFrame = None, open_positions: pd.DataFrame = None) -> Dict:
    if df is None:
        df = load_closed_trades()
        if open_positions is None:
            open_positions = load_open_positions()
    if open_positions is None:
        open_positions = pd.DataFrame()
    elif not isinstance(open_positions, pd.DataFrame):
        open_positions = pd.DataFrame(open_positions)

    if df.empty and open_positions.empty:
        return {"error": "No closed trades yet."}

    if df.empty:
        total_trades = 0
        wins = 0
        losses = 0
        win_rate = 0
        avg_win = 0
        avg_loss = 0
        realized_pnl_pct = 0
        realized_pnl_dollars = 0
        monthly = {}
        by_strategy = {}
    else:
        df = df.copy()
        if "realized_pnl_dollars" not in df:
            df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
            df["entry_price"] = pd.to_numeric(df["entry_price"], errors="coerce")
            df["exit_price"] = pd.to_numeric(df["exit_price"], errors="coerce")
            df["realized_pnl_dollars"] = (df["exit_price"] - df["entry_price"]) * df["qty"]
        total_trades  = len(df)
        wins          = (df["pnl_pct"] > 0).sum()
        losses        = (df["pnl_pct"] <= 0).sum()
        win_rate      = wins / total_trades if total_trades else 0
        avg_win       = df[df["pnl_pct"] > 0]["pnl_pct"].mean() if wins else 0
        avg_loss      = df[df["pnl_pct"] <= 0]["pnl_pct"].mean() if losses else 0
        realized_pnl_pct = df["pnl_pct"].sum()
        realized_pnl_dollars = df["realized_pnl_dollars"].sum()

        # Monthly P&L grouped
        df["month"] = df["timestamp"].dt.to_period("M")
        monthly     = df.groupby("month")["pnl_pct"].sum().to_dict()
        monthly     = {str(k): round(v, 4) for k, v in monthly.items()}

        # Strategy breakdown
        by_strategy = df.groupby("strategy").agg(
            trades=("pnl_pct", "count"),
            total_pnl=("pnl_pct", "sum"),
            realized_pnl_dollars=("realized_pnl_dollars", "sum"),
            win_rate=("pnl_pct", lambda x: (x > 0).mean()),
        ).round(4).to_dict(orient="index")

    if open_positions.empty:
        open_count = 0
        unrealized_pnl_dollars = 0.0
    else:
        open_count = len(open_positions)
        if "unrealized_pnl_dollars" in open_positions:
            unrealized = pd.to_numeric(open_positions["unrealized_pnl_dollars"], errors="coerce").fillna(0)
            unrealized_pnl_dollars = unrealized.sum()
        else:
            unrealized_pnl_dollars = 0.0

    total_pnl_dollars = float(realized_pnl_dollars + unrealized_pnl_dollars)

    return {
        "generated_at":   _utc_now().isoformat(),
        "total_trades":   total_trades,
        "wins":           int(wins),
        "losses":         int(losses),
        "win_rate":       round(win_rate, 4),
        "avg_win_pct":    round(avg_win, 4),
        "avg_loss_pct":   round(avg_loss, 4),
        "total_pnl_pct":  round(realized_pnl_pct, 4),
        "realized_pnl_pct": round(realized_pnl_pct, 4),
        "realized_pnl_dollars": round(float(realized_pnl_dollars), 2),
        "open_positions": int(open_count),
        "unrealized_pnl_dollars": round(float(unrealized_pnl_dollars), 2),
        "total_pnl_dollars": round(total_pnl_dollars, 2),
        "monthly_pnl":    monthly,
        "by_strategy":    by_strategy,
    }


def format_report(summary: Dict = None) -> str:
    if summary is None:
        summary = compute_summary()

    if "error" in summary:
        return f"\n  {summary['error']}\n"

    lines = [
        "",
        "=" * 60,
        f"  PERFORMANCE REPORT  [{summary['generated_at']}]",
        "=" * 60,
        f"  Closed Trades  : {summary['total_trades']}",
        f"  Wins / Losses  : {summary['wins']} / {summary['losses']}",
        f"  Win Rate       : {summary['win_rate']:.1%}",
        f"  Avg Win        : {summary['avg_win_pct']:+.2%}",
        f"  Avg Loss       : {summary['avg_loss_pct']:+.2%}",
        f"  Realized P&L   : {summary['realized_pnl_pct']:+.2%} (${summary['realized_pnl_dollars']:+,.2f})",
        f"  Open Positions : {summary['open_positions']}",
        f"  Unrealized P&L : ${summary['unrealized_pnl_dollars']:+,.2f}",
        f"  Total P&L      : ${summary['total_pnl_dollars']:+,.2f}",
        "",
        "  Monthly Breakdown:",
    ]
    for month, pnl in summary["monthly_pnl"].items():
        lines.append(f"    {month}: {pnl:+.2%}")

    lines += ["", "  Strategy Breakdown:"]
    for strat, stats in summary["by_strategy"].items():
        lines.append(
            f"    {strat:<20} trades={stats['trades']:>4}  "
            f"pnl={stats['total_pnl']:+.2%}  dollars=${stats['realized_pnl_dollars']:+,.2f}  "
            f"win_rate={stats['win_rate']:.1%}"
        )
    lines.append("=" * 60)
    return "\n".join(lines)


def save_performance_snapshot():
    summary = compute_summary()
    if "error" in summary:
        log.warning(summary["error"])
        return

    PERF_FILE.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([{
        "timestamp":      summary["generated_at"],
        "total_trades":   summary["total_trades"],
        "win_rate":       summary["win_rate"],
        "total_pnl_pct":  summary["total_pnl_pct"],
        "realized_pnl_dollars": summary["realized_pnl_dollars"],
        "unrealized_pnl_dollars": summary["unrealized_pnl_dollars"],
        "total_pnl_dollars": summary["total_pnl_dollars"],
        "avg_win_pct":    summary["avg_win_pct"],
        "avg_loss_pct":   summary["avg_loss_pct"],
    }])

    if PERF_FILE.exists():
        existing = pd.read_csv(PERF_FILE)
        df = pd.concat([existing, df], ignore_index=True)

    df.to_csv(PERF_FILE, index=False)
    log.info(f"Performance snapshot saved to {PERF_FILE}")
