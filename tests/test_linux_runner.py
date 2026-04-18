import subprocess
import unittest
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


class LinuxRunnerTests(unittest.TestCase):
    def test_runner_script_has_valid_bash_syntax(self):
        script = BASE_DIR / "scripts" / "run_hawkstrade_job.sh"

        subprocess.run(["bash", "-n", str(script)], check=True)

    def test_cron_templates_route_scheduled_jobs_through_runner(self):
        for cron_file in (BASE_DIR / "scheduler" / "cron").glob("hawkstrade-*.cron"):
            text = cron_file.read_text(encoding="utf-8")

            self.assertIn("./scripts/run_hawkstrade_job.sh scheduler/run_scan.py", text)
            self.assertIn("./scripts/run_hawkstrade_job.sh scheduler/run_risk_check.py", text)
            self.assertIn("./scripts/run_hawkstrade_job.sh scheduler/run_report.py", text)
            self.assertNotIn("python3 scheduler/run_scan.py", text)
            self.assertNotIn("python3 scheduler/run_risk_check.py", text)
            self.assertNotIn("python3 scheduler/run_report.py", text)

    def test_runner_uses_single_trade_mutation_lock(self):
        script = (BASE_DIR / "scripts" / "run_hawkstrade_job.sh").read_text(encoding="utf-8")

        self.assertIn("local/locks/trade-mutating-jobs.lock", script)
        self.assertIn("flock -n -E 75", script)
        self.assertIn('flock -w "$LOCK_TIMEOUT_SECONDS" -E 75', script)


if __name__ == "__main__":
    unittest.main()
