"""
HawksTrade - Weekly TCA Report
==============================
Generates a standalone transaction-cost analysis report from data/trades.csv.

Run directly:
  python3 scheduler/run_weekly_tca.py
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.logging_config import runtime_log_handlers
from core.run_markers import run_scope
from tracking.tca import write_weekly_tca_report


BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
REPORTS_DIR = BASE_DIR / "reports"
LOG_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=runtime_log_handlers(LOG_DIR, f"weekly_tca_{_utc_now().strftime('%Y%m%d')}.log"),
)
log = logging.getLogger("run_weekly_tca")


def run_weekly_tca(*, days: int = 7, output_dir: Path = REPORTS_DIR) -> Path:
    end = _utc_now()
    start = end - timedelta(days=max(1, int(days)))
    report_path = write_weekly_tca_report(output_dir, start=start, end=end, generated_at=end)
    log.info("Weekly TCA report saved: %s", report_path)
    return report_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate HawksTrade weekly TCA report")
    parser.add_argument("--days", type=int, default=7, help="Lookback window in days")
    parser.add_argument("--output-dir", type=Path, default=REPORTS_DIR, help="Directory for report output")
    args = parser.parse_args()

    with run_scope(log, "run_weekly_tca", days=args.days) as marker:
        try:
            path = run_weekly_tca(days=args.days, output_dir=args.output_dir)
            marker.mark_ok(report_path=path)
        except Exception:
            marker.mark_error(stage="weekly_tca_generation", error_type="WeeklyTcaFailed")
            log.error("Weekly TCA generation failed", exc_info=True)
            raise
