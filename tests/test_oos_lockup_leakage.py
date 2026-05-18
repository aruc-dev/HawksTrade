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


if __name__ == "__main__":
    unittest.main()
