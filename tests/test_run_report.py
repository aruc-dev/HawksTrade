import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scheduler import run_report


class RunReportTests(unittest.TestCase):
    def test_daily_report_reconciles_before_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(run_report, "REPORTS_DIR", Path(tmp)),
                patch.object(run_report, "safe_reconcile", return_value={"positions": 0}) as safe_reconcile,
                patch.object(run_report, "get_snapshot", return_value={}),
                patch.object(run_report, "print_snapshot"),
                patch.object(run_report, "compute_summary", return_value={"total_trades": 0}),
                patch.object(run_report, "format_report", return_value="report"),
                patch.object(run_report, "save_performance_snapshot"),
            ):
                run_report.run_daily_report()

        safe_reconcile.assert_called_once_with(
            context="run_report.daily_pre_summary",
            logger=run_report.log,
        )

    def test_weekly_report_reconciles_before_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(run_report, "REPORTS_DIR", Path(tmp)),
                patch.object(run_report, "safe_reconcile", return_value={"positions": 0}) as safe_reconcile,
                patch.object(run_report, "compute_summary", return_value={"total_trades": 0}),
                patch.object(run_report, "format_report", return_value="report"),
            ):
                run_report.run_weekly_report()

        safe_reconcile.assert_called_once_with(
            context="run_report.weekly_pre_summary",
            logger=run_report.log,
        )


if __name__ == "__main__":
    unittest.main()
