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
    df = df[df["status"] == "closed"].copy()
    if df.empty:
        return df
    df["timestamp"]  = pd.to_datetime(df["timestamp"])
    df["pnl_pct"]    = pd.to_numeric(df["pnl_pct"], errors="coerce").fillna(0)
    df["entry_price"] = pd.to_numeric(df["entry_price"], errors="coerce")
    df["exit_price"]  = pd.to_numeric(df["exit_price"], errors="coerce")
    return df


def compute_summary(df: pd.DataFrame = None) -> Dict:
    if df is None:
        df = load_closed_trades()

    if df.empty:
        return {"error": "No closed trades yet."}

    total_trades  = len(df)
    wins          = (df["pnl_pct"] > 0).sum()
    losses        = (df["pnl_pct"] <= 0).sum()
    win_rate      = wins / total_trades if total_trades else 0
    avg_win       = df[df["pnl_pct"] > 0]["pnl_pct"].mean() if wins else 0
    avg_loss      = df[df["pnl_pct"] <= 0]["pnl_pct"].mean() if losses else 0
    total_pnl_pct = df["pnl_pct"].sum()

    # Monthly P&L grouped
    df["month"] = df["timestamp"].dt.to_period("M")
    monthly     = df.groupby("month")["pnl_pct"].sum().to_dict()
    monthly     = {str(k): round(v, 4) for k, v in monthly.items()}

    # Strategy breakdown
    by_strategy = df.groupby("strategy")["pnl_pct"].agg(
        trades="count", total_pnl="sum", win_rate=lambda x: (x > 0).mean()
    ).round(4).to_dict(orient="index")

    return {
        "generated_at":   _utc_now().isoformat(),
        "total_trades":   total_trades,
        "wins":           int(wins),
        "losses":         int(losses),
        "win_rate":       round(win_rate, 4),
        "avg_win_pct":    round(avg_win, 4),
        "avg_loss_pct":   round(avg_loss, 4),
        "total_pnl_pct":  round(total_pnl_pct, 4),
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
        f"  Total Trades   : {summary['total_trades']}",
        f"  Wins / Losses  : {summary['wins']} / {summary['losses']}",
        f"  Win Rate       : {summary['win_rate']:.1%}",
        f"  Avg Win        : {summary['avg_win_pct']:+.2%}",
        f"  Avg Loss       : {summary['avg_loss_pct']:+.2%}",
        f"  Total P&L      : {summary['total_pnl_pct']:+.2%}",
        "",
        "  Monthly Breakdown:",
    ]
    for month, pnl in summary["monthly_pnl"].items():
        lines.append(f"    {month}: {pnl:+.2%}")

    lines += ["", "  Strategy Breakdown:"]
    for strat, stats in summary["by_strategy"].items():
        lines.append(
            f"    {strat:<20} trades={stats['trades']:>4}  "
            f"pnl={stats['total_pnl']:+.2%}  win_rate={stats['win_rate']:.1%}"
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
        "avg_win_pct":    summary["avg_win_pct"],
        "avg_loss_pct":   summary["avg_loss_pct"],
    }])

    if PERF_FILE.exists():
        existing = pd.read_csv(PERF_FILE)
        df = pd.concat([existing, df], ignore_index=True)

    df.to_csv(PERF_FILE, index=False)
    log.info(f"Performance snapshot saved to {PERF_FILE}")
