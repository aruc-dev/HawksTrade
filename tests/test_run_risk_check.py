import unittest
from unittest.mock import patch

from scheduler import run_risk_check


class RunRiskCheckTests(unittest.TestCase):
    def test_run_skips_when_daily_loss_check_fails(self):
        with (
            patch.object(run_risk_check.rm, "daily_loss_exceeded", side_effect=RuntimeError("unauthorized")),
            patch.object(run_risk_check.oe, "exit_position") as exit_position,
        ):
            run_risk_check.run(dry_run=True)

        exit_position.assert_not_called()


if __name__ == "__main__":
    unittest.main()
