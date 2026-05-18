import sys
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from core.data_lockup import OOSLockup
from scripts.run_release_validation import build_release_validation_plan


class ReleaseValidationPlanTests(unittest.TestCase):
    def _args(self, **overrides):
        values = {
            "fund": 10000.0,
            "backtest_days": 30,
            "backtest_end_date": "02/14/2026",
            "skip_operational": False,
            "include_ec2_health": False,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_plan_includes_first_release_gates(self):
        gates = build_release_validation_plan(self._args())
        names = [gate.name for gate in gates]

        self.assertEqual(
            names,
            [
                "unit tests",
                "deprecation strict unit tests",
                "compileall",
                "OOS lockup leakage",
                "production validation gate",
                "30-day backtest",
                "scan dry-run",
                "risk-check dry-run",
                "daily report",
            ],
        )

        commands = [gate.command for gate in gates]
        compileall = next(gate.command for gate in gates if gate.name == "compileall")
        self.assertIn("analysis", compileall)
        self.assertIn((sys.executable, "scheduler/run_validation_gate.py", "--profile", "production"), commands)
        self.assertIn(
            (
                sys.executable,
                "scheduler/run_backtest.py",
                "--days",
                "30",
                "--fund",
                "10000.0",
                "--no-quarterly-output",
                "--end-date",
                "02/14/2026",
            ),
            commands,
        )

    def test_operational_gates_can_be_skipped_and_ec2_checks_added(self):
        gates = build_release_validation_plan(
            self._args(skip_operational=True, include_ec2_health=True, backtest_end_date="none")
        )
        names = [gate.name for gate in gates]

        self.assertNotIn("scan dry-run", names)
        self.assertIn("systemd deployment check", names)
        self.assertIn("linux health check", names)
        backtest = next(gate.command for gate in gates if gate.name == "30-day backtest")
        self.assertNotIn("--end-date", backtest)

    def test_auto_backtest_end_date_uses_day_before_active_lockup(self):
        lockup = OOSLockup(start_date=date(2026, 2, 15), end_date=date(2026, 5, 15))

        with patch("scripts.run_release_validation.current_lockup", return_value=lockup):
            gates = build_release_validation_plan(self._args(backtest_end_date="auto"))

        backtest = next(gate.command for gate in gates if gate.name == "30-day backtest")
        self.assertEqual(backtest[-2:], ("--end-date", "02/14/2026"))


if __name__ == "__main__":
    unittest.main()
