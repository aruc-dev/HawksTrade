"""
HawksTrade - Report Generator
================================
Generates daily and weekly performance reports.
Saves reports to reports/ folder as plain text and CSV snapshots.

Run directly:
  python scheduler/run_report.py [--weekly]
"""

from __future__ import annotations

import sys
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import alpaca_client as ac
from core.config_loader import get_config
from core.logging_config import runtime_log_handlers
from core.portfolio import get_snapshot, print_snapshot
from core.protection_manager import active_locks_for_reporting
from core.run_markers import run_scope
from core.sample_size_governor import format_tier_report
from core.version import __version__
from scheduler.reconcile_trade_log import safe_reconcile
from tracking.trade_log import get_closed_trades
from tracking.performance import compute_summary, format_report, save_performance_snapshot

BASE_DIR    = Path(__file__).resolve().parent.parent
LOG_DIR     = BASE_DIR / "logs"
REPORTS_DIR = BASE_DIR / "reports"
LOG_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=runtime_log_handlers(LOG_DIR, f"report_{_utc_now().strftime('%Y%m%d')}.log"),
)
log = logging.getLogger("run_report")

CFG = get_config()


def _format_protection_locks() -> str:
    try:
        locks = active_locks_for_reporting()
    except Exception as exc:
        log.warning("Protection lock reporting unavailable: %s", exc, exc_info=True)
        return f"Protection lock reporting unavailable: {exc}"
    if not locks:
        return ""
    lines = ["Active Protection Locks:"]
    for lock in locks:
        lines.append(
            f"  {lock.get('lock_type')} scope={lock.get('scope')} key={lock.get('key')} "
            f"expires={lock.get('expires_at')} reason={lock.get('reason')}"
        )
    return "\n".join(lines)


def _format_sample_size_tiers() -> str:
    try:
        return format_tier_report(CFG, get_closed_trades())
    except Exception as exc:
        log.warning("Sample-size tier reporting unavailable: %s", exc, exc_info=True)
        return f"Sample-size tier reporting unavailable: {exc}"


def run_daily_report():
    log.info("=== DAILY REPORT ===")
    ts = _utc_now().strftime("%Y-%m-%d")
    safe_reconcile(context="run_report.daily_pre_summary", logger=log)

    # Portfolio snapshot
    snap = get_snapshot()
    print_snapshot()

    # Performance summary
    summary = compute_summary()
    report_text = format_report(summary)
    log.info(report_text)
    protection_text = _format_protection_locks()
    if protection_text:
        log.warning("\n%s", protection_text)
    sample_size_text = _format_sample_size_tiers()
    if sample_size_text:
        log.info("\n%s", sample_size_text)

    # Save report to file
    report_path = REPORTS_DIR / f"daily_{ts}.txt"
    with open(report_path, "w") as f:
        f.write(f"HawksTrade Daily Report — {ts}\n")
        f.write(f"Version: {__version__}\n")
        f.write(f"Mode: {CFG['mode'].upper()}\n\n")
        if snap:
            f.write(f"Portfolio Value : ${snap['portfolio_value']:,.2f}\n")
            f.write(f"Cash            : ${snap['cash']:,.2f}\n")
            f.write(f"Open Positions  : {snap['position_count']}\n\n")
            f.write("Open Positions Detail:\n")
            for p in snap["positions"]:
                f.write(
                    f"  {p['symbol']:<10} qty={p['qty']:>8.4f}  "
                    f"entry=${p['avg_entry_price']:>10.4f}  "
                    f"now=${p['current_price']:>10.4f}  "
                    f"P&L={p['unrealized_pnl_pct']:>+.2%}\n"
                )
        if protection_text:
            f.write(protection_text)
            f.write("\n\n")
        if sample_size_text:
            f.write(sample_size_text)
            f.write("\n\n")
        f.write(report_text)
        f.write("\n")

    save_performance_snapshot()
    log.info(f"Daily report saved: {report_path}")


def run_weekly_report():
    log.info("=== WEEKLY REPORT ===")
    ts = _utc_now().strftime("%Y-W%W")
    safe_reconcile(context="run_report.weekly_pre_summary", logger=log)

    summary = compute_summary()
    report_text = format_report(summary)
    log.info(report_text)
    protection_text = _format_protection_locks()
    if protection_text:
        log.warning("\n%s", protection_text)
    sample_size_text = _format_sample_size_tiers()
    if sample_size_text:
        log.info("\n%s", sample_size_text)

    report_path = REPORTS_DIR / f"weekly_{ts}.txt"
    with open(report_path, "w") as f:
        f.write(f"HawksTrade Weekly Report — {ts}\n")
        f.write(f"Version: {__version__}\n")
        f.write(f"Mode: {CFG['mode'].upper()}\n\n")
        if protection_text:
            f.write(protection_text)
            f.write("\n\n")
        if sample_size_text:
            f.write(sample_size_text)
            f.write("\n\n")
        f.write(report_text)
        f.write("\n")

    log.info(f"Weekly report saved: {report_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HawksTrade Report Generator")
    parser.add_argument("--weekly", action="store_true", help="Generate weekly report")
    args = parser.parse_args()

    with run_scope(
        log,
        "run_report",
        weekly=args.weekly,
        report_kind="weekly" if args.weekly else "daily",
    ) as marker:
        try:
            if args.weekly:
                run_weekly_report()
            else:
                run_daily_report()
        except Exception as e:
            info = ac.classify_alpaca_error(e)
            marker.mark_error(
                stage="report_generation",
                error_type=type(e).__name__,
                error_category=info.category,
                retryable=info.retryable,
                status_code=info.status_code,
            )
            log.error(
                "Report generation failed: %s | category=%s retryable=%s status_code=%s",
                e,
                info.category,
                info.retryable,
                info.status_code or "",
                exc_info=True,
            )
            raise
