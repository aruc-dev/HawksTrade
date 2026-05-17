import unittest

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


if __name__ == "__main__":
    unittest.main()
