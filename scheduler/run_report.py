"""
HawksTrade - Report Generator
================================
Generates daily and weekly performance reports.
Saves reports to reports/ folder as plain text and CSV snapshots.

Run directly:
  python scheduler/run_report.py [--weekly]
"""

import sys
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
from core.portfolio import get_snapshot, print_snapshot
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
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / f"report_{_utc_now().strftime('%Y%m%d')}.log"),
    ],
)
log = logging.getLogger("run_report")

with open(BASE_DIR / "config" / "config.yaml") as f:
    CFG = yaml.safe_load(f)


def run_daily_report():
    log.info("=== DAILY REPORT ===")
    ts = _utc_now().strftime("%Y-%m-%d")

    # Portfolio snapshot
    snap = get_snapshot()
    print_snapshot()

    # Performance summary
    summary = compute_summary()
    report_text = format_report(summary)
    log.info(report_text)

    # Save report to file
    report_path = REPORTS_DIR / f"daily_{ts}.txt"
    with open(report_path, "w") as f:
        f.write(f"HawksTrade Daily Report — {ts}\n")
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
        f.write(report_text)
        f.write("\n")

    save_performance_snapshot()
    log.info(f"Daily report saved: {report_path}")


def run_weekly_report():
    log.info("=== WEEKLY REPORT ===")
    ts = _utc_now().strftime("%Y-W%W")

    summary = compute_summary()
    report_text = format_report(summary)
    log.info(report_text)

    report_path = REPORTS_DIR / f"weekly_{ts}.txt"
    with open(report_path, "w") as f:
        f.write(f"HawksTrade Weekly Report — {ts}\n")
        f.write(f"Mode: {CFG['mode'].upper()}\n\n")
        f.write(report_text)
        f.write("\n")

    log.info(f"Weekly report saved: {report_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HawksTrade Report Generator")
    parser.add_argument("--weekly", action="store_true", help="Generate weekly report")
    args = parser.parse_args()

    if args.weekly:
        run_weekly_report()
    else:
        run_daily_report()
