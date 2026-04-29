import os
import subprocess
import tempfile
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

    def test_launchd_runner_routes_scheduled_jobs_through_runner(self):
        text = (BASE_DIR / "scheduler" / "launchd" / "hawkstrade_launchd_runner.sh").read_text(encoding="utf-8")

        self.assertIn('scripts/run_hawkstrade_job.sh" scheduler/run_scan.py --stocks-only', text)
        self.assertIn('scripts/run_hawkstrade_job.sh" scheduler/run_scan.py', text)
        self.assertIn('scripts/run_hawkstrade_job.sh" scheduler/run_risk_check.py', text)
        self.assertIn('scripts/run_hawkstrade_job.sh" scheduler/run_report.py', text)
        self.assertNotIn("/usr/bin/env python3 scheduler/run_scan.py", text)
        self.assertNotIn("/usr/bin/env python3 scheduler/run_risk_check.py", text)
        self.assertNotIn("/usr/bin/env python3 scheduler/run_report.py", text)

    def test_runner_uses_single_trade_mutation_lock(self):
        script = (BASE_DIR / "scripts" / "run_hawkstrade_job.sh").read_text(encoding="utf-8")

        self.assertIn("local/locks/trade-mutating-jobs.lock", script)
        self.assertIn("flock -n -E 75", script)
        self.assertIn('flock -w "$LOCK_TIMEOUT_SECONDS" -E 75', script)

    def test_runner_has_secret_and_connectivity_preflight(self):
        script = (BASE_DIR / "scripts" / "run_hawkstrade_job.sh").read_text(encoding="utf-8")

        self.assertIn("PREFLIGHT_START", script)
        self.assertIn("PREFLIGHT_OK", script)
        self.assertIn("preflight_failed", script)
        self.assertIn("get_clock()", script)

    def test_runner_exits_before_job_when_preflight_fails(self):
        script = BASE_DIR / "scripts" / "run_hawkstrade_job.sh"
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "project"
            project_dir.mkdir()
            fake_python = Path(tmpdir) / "python"
            fake_flock = Path(tmpdir) / "flock"
            call_log = Path(tmpdir) / "calls.log"
            fake_python.write_text(
                "#!/usr/bin/env bash\n"
                "echo \"$*\" >> \"$FAKE_PYTHON_CALL_LOG\"\n"
                "if [[ \"${1:-}\" == \"-\" ]]; then\n"
                "  echo 'fake preflight failure' >&2\n"
                "  exit 70\n"
                "fi\n"
                "echo 'job should not run' >&2\n"
                "exit 99\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            fake_flock.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
            fake_flock.chmod(0o755)
            env = os.environ.copy()
            env.update({
                "HAWKSTRADE_DIR": str(project_dir),
                "PYTHON": str(fake_python),
                "FAKE_PYTHON_CALL_LOG": str(call_log),
                "PATH": f"{tmpdir}{os.pathsep}{env.get('PATH', '')}",
            })

            result = subprocess.run(
                ["bash", str(script), "scheduler/run_report.py"],
                cwd=project_dir,
                env=env,
                capture_output=True,
                text=True,
            )
            call_count = call_log.read_text(encoding="utf-8").count("- scheduler/run_report.py")

        self.assertEqual(result.returncode, 70)
        self.assertIn("status=preflight_failed", result.stdout)
        self.assertNotIn("job should not run", result.stderr)
        self.assertEqual(call_count, 1)


if __name__ == "__main__":
    unittest.main()
