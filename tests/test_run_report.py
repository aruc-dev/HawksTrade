import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.version import __version__
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
            report_files = list(Path(tmp).glob("daily_*.txt"))
            report_text = report_files[0].read_text()

        safe_reconcile.assert_called_once_with(
            context="run_report.daily_pre_summary",
            logger=run_report.log,
        )
        self.assertIn(f"Version: {__version__}", report_text)

    def test_weekly_report_reconciles_before_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(run_report, "REPORTS_DIR", Path(tmp)),
                patch.object(run_report, "safe_reconcile", return_value={"positions": 0}) as safe_reconcile,
                patch.object(run_report, "compute_summary", return_value={"total_trades": 0}),
                patch.object(run_report, "format_report", return_value="report"),
            ):
                run_report.run_weekly_report()
            report_files = list(Path(tmp).glob("weekly_*.txt"))
            report_text = report_files[0].read_text()

        safe_reconcile.assert_called_once_with(
            context="run_report.weekly_pre_summary",
            logger=run_report.log,
        )
        self.assertIn(f"Version: {__version__}", report_text)

    def test_protection_lock_reporting_failure_does_not_abort_report(self):
        with (
            patch.object(run_report, "active_locks_for_reporting", side_effect=ValueError("bad lock json")),
            self.assertLogs("run_report", level="WARNING") as logs,
        ):
            text = run_report._format_protection_locks()

        self.assertIn("Protection lock reporting unavailable", text)
        self.assertIn("bad lock json", text)
        self.assertTrue(any("Protection lock reporting unavailable" in message for message in logs.output))


if __name__ == "__main__":
    unittest.main()
