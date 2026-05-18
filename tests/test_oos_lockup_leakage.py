import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import check_oos_lockup_leakage as leakage


class OOSLockupLeakageScriptTests(unittest.TestCase):
    def test_validation_report_exception_is_restricted_to_oos_validation_reports(self):
        text = "# OOS validation\n\nResult for locked dates."

        self.assertTrue(
            leakage._is_allowed_oos_validation_report(
                leakage.ROOT / "reports" / "oos_validation_20260517.md",
                text,
            )
        )
        self.assertFalse(
            leakage._is_allowed_oos_validation_report(
                leakage.ROOT / "reports" / "manual_locked_window.md",
                text,
            )
        )
        self.assertFalse(
            leakage._is_allowed_oos_validation_report(
                leakage.ROOT / "docs" / "oos_validation_20260517.md",
                text,
            )
        )
        self.assertFalse(
            leakage._is_allowed_oos_validation_report(
                leakage.ROOT / "reports" / "oos_validation_20260517.md",
                "Result for locked dates.",
            )
        )

    def test_explicit_relative_files_resolve_from_repo_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            report = root / "reports" / "leak.md"
            report.parent.mkdir()
            report.write_text("locked OOS date", encoding="utf-8")
            subdir = root / "scripts"
            subdir.mkdir()

            old_cwd = os.getcwd()
            try:
                os.chdir(subdir)
                with (
                    patch.object(leakage, "ROOT", root),
                    patch.object(
                        leakage,
                        "current_lockup",
                        return_value=SimpleNamespace(
                            start_date=date(2026, 2, 15),
                            end_date=date(2026, 5, 15),
                        ),
                    ),
                    patch.object(leakage, "report_mentions_locked_date", return_value=True),
                ):
                    result = leakage.main(["reports/leak.md"])
            finally:
                os.chdir(old_cwd)

        self.assertEqual(result, 1)

    def test_default_mode_scans_tracked_reports(self):
        report = leakage.ROOT / "reports" / "leak.md"

        with (
            patch.object(leakage, "current_lockup", return_value=SimpleNamespace(
                start_date=date(2026, 2, 15),
                end_date=date(2026, 5, 15),
            )),
            patch.object(leakage, "_tracked_report_files", return_value=[report]) as tracked,
            patch.object(leakage, "_staged_report_files", side_effect=AssertionError("staged only")),
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "is_dir", return_value=False),
            patch.object(Path, "read_text", return_value="locked OOS date"),
            patch.object(leakage, "report_mentions_locked_date", return_value=True),
        ):
            result = leakage.main([])

        self.assertEqual(result, 1)
        tracked.assert_called_once_with()

    def test_staged_mode_scans_staged_reports(self):
        report = leakage.ROOT / "reports" / "leak.md"

        with (
            patch.object(leakage, "current_lockup", return_value=SimpleNamespace(
                start_date=date(2026, 2, 15),
                end_date=date(2026, 5, 15),
            )),
            patch.object(leakage, "_tracked_report_files", side_effect=AssertionError("tracked only")),
            patch.object(leakage, "_staged_report_files", return_value=[report]) as staged,
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "is_dir", return_value=False),
            patch.object(Path, "read_text", return_value="locked OOS date"),
            patch.object(leakage, "report_mentions_locked_date", return_value=True),
        ):
            result = leakage.main(["--staged"])

        self.assertEqual(result, 1)
        staged.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
