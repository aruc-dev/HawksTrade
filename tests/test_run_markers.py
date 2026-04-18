import logging
import os
import unittest
from unittest.mock import patch

from core.run_markers import run_scope


class RunMarkerTests(unittest.TestCase):
    def test_run_scope_emits_start_and_end_markers(self):
        logger_name = "hawks.trade.marker.test"
        logger = logging.getLogger(logger_name)

        with self.assertLogs(logger_name, level="INFO") as captured:
            with run_scope(
                logger,
                "run_scan",
                dry_run="ON",
                scan_kind="full",
                run_stocks=True,
                run_crypto=True,
            ) as marker:
                marker.mark_status("ok", outcome="completed")

        output = "\n".join(captured.output)
        self.assertIn("RUN_START script=run_scan", output)
        self.assertIn("RUN_END script=run_scan", output)
        self.assertIn("run_id=run_scan-", output)
        self.assertIn("scan_kind=full", output)
        self.assertIn("status=ok", output)

    def test_run_scope_marks_errors_on_exception(self):
        logger_name = "hawks.trade.marker.error"
        logger = logging.getLogger(logger_name)

        with self.assertLogs(logger_name, level="INFO") as captured:
            with self.assertRaises(RuntimeError):
                with run_scope(logger, "run_risk_check", dry_run="OFF"):
                    raise RuntimeError("boom")

        output = "\n".join(captured.output)
        self.assertIn("RUN_START script=run_risk_check", output)
        self.assertIn("RUN_END script=run_risk_check", output)
        self.assertIn("status=error", output)
        self.assertIn("error_type=RuntimeError", output)

    def test_run_scope_sets_run_id_environment_and_restores_previous_value(self):
        logger = logging.getLogger("hawks.trade.marker.env")

        with patch.dict(os.environ, {"HAWKSTRADE_RUN_ID": "outer-run"}):
            with run_scope(logger, "run_scan") as marker:
                self.assertEqual(os.environ["HAWKSTRADE_RUN_ID"], marker.run_id)

            self.assertEqual(os.environ["HAWKSTRADE_RUN_ID"], "outer-run")


if __name__ == "__main__":
    unittest.main()
