import unittest
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
SYSTEMD_DIR = BASE_DIR / "scheduler" / "systemd"


TRADE_SERVICES = [
    "hawkstrade-stock-scan.service",
    "hawkstrade-full-scan.service",
    "hawkstrade-crypto-scan.service",
    "hawkstrade-risk-check.service",
    "hawkstrade-daily-report.service",
    "hawkstrade-weekly-report.service",
]

ALL_JOB_SERVICES = TRADE_SERVICES + ["hawkstrade-health-check.service"]

TIMERS = [
    "hawkstrade-stock-scan.timer",
    "hawkstrade-full-scan.timer",
    "hawkstrade-crypto-scan.timer",
    "hawkstrade-risk-check.timer",
    "hawkstrade-daily-report.timer",
    "hawkstrade-weekly-report.timer",
    "hawkstrade-health-check.timer",
]


class SystemdTemplateTests(unittest.TestCase):
    def test_expected_systemd_templates_exist(self):
        expected = {
            "README.md",
            "hawkstrade.env.example",
            "hawkstrade-secrets.service",
            *TRADE_SERVICES,
            "hawkstrade-health-check.service",
            *TIMERS,
        }

        self.assertEqual(
            expected,
            {path.name for path in SYSTEMD_DIR.iterdir() if path.is_file()},
        )

    def test_job_services_require_network_and_shm_secrets(self):
        for service_name in ALL_JOB_SERVICES:
            with self.subTest(service=service_name):
                text = (SYSTEMD_DIR / service_name).read_text(encoding="utf-8")

                self.assertIn("Wants=network-online.target", text)
                self.assertIn("After=network-online.target hawkstrade-secrets.service", text)
                self.assertIn("EnvironmentFile=/etc/hawkstrade/hawkstrade.env", text)
                self.assertIn("Environment=HAWKSTRADE_REQUIRE_SHM=1", text)
                self.assertIn("WorkingDirectory=/home/ec2-user/HawksTrade", text)
                self.assertIn("Restart=no", text)
                self.assertIn("StandardOutput=journal", text)
                self.assertIn("StandardError=journal", text)

    def test_trade_services_require_shm_secret_loader(self):
        for service_name in TRADE_SERVICES:
            with self.subTest(service=service_name):
                text = (SYSTEMD_DIR / service_name).read_text(encoding="utf-8")

                self.assertIn("Requires=hawkstrade-secrets.service", text)

    def test_health_service_can_run_when_secret_loader_fails(self):
        text = (SYSTEMD_DIR / "hawkstrade-health-check.service").read_text(encoding="utf-8")

        self.assertIn("Wants=network-online.target hawkstrade-secrets.service", text)
        self.assertNotIn("Requires=hawkstrade-secrets.service", text)

    def test_trade_jobs_route_through_existing_runner(self):
        for service_name in TRADE_SERVICES:
            with self.subTest(service=service_name):
                text = (SYSTEMD_DIR / service_name).read_text(encoding="utf-8")

                self.assertIn("/scripts/run_hawkstrade_job.sh", text)
                self.assertNotIn("python3 scheduler/run_scan.py", text)
                self.assertNotIn("python3 scheduler/run_risk_check.py", text)
                self.assertNotIn("python3 scheduler/run_report.py", text)

    def test_health_service_uses_venv_python_fallback(self):
        text = (SYSTEMD_DIR / "hawkstrade-health-check.service").read_text(encoding="utf-8")

        self.assertIn("PY=.venv/bin/python", text)
        self.assertIn("PY=.venv/bin/python3", text)
        self.assertIn("scripts/check_health_linux.py", text)
        self.assertIn("${HAWKSTRADE_HEALTH_HOURS:-4}", text)

    def test_secret_loader_populates_dev_shm(self):
        text = (SYSTEMD_DIR / "hawkstrade-secrets.service").read_text(encoding="utf-8")

        self.assertIn("Before=hawkstrade-stock-scan.service", text)
        self.assertIn("ConditionPathExists=/etc/hawkstrade/hawkstrade.secrets", text)
        self.assertIn("RemainAfterExit=yes", text)
        self.assertIn("install -m 0600", text)
        self.assertIn("/dev/shm/.hawkstrade.env", text)
        self.assertIn("ExecStop=", text)

    def test_timers_are_persistent_and_installed_to_timers_target(self):
        expected_calendar = {
            "hawkstrade-stock-scan.timer": "OnCalendar=Mon..Fri *-*-* 13:35:00",
            "hawkstrade-full-scan.timer": "OnCalendar=Mon..Fri *-*-* 14..19:00:00",
            "hawkstrade-crypto-scan.timer": "OnCalendar=hourly",
            "hawkstrade-risk-check.timer": "OnCalendar=Mon..Fri *-*-* 13:45:00",
            "hawkstrade-daily-report.timer": "OnCalendar=Mon..Fri *-*-* 20:30:00",
            "hawkstrade-weekly-report.timer": "OnCalendar=Mon *-*-* 12:00:00",
            "hawkstrade-health-check.timer": "OnCalendar=*:0/15",
        }

        for timer_name, calendar_line in expected_calendar.items():
            with self.subTest(timer=timer_name):
                text = (SYSTEMD_DIR / timer_name).read_text(encoding="utf-8")

                self.assertIn(calendar_line, text)
                self.assertIn("Persistent=true", text)
                self.assertIn("WantedBy=timers.target", text)
                self.assertIn(f"Unit={timer_name.removesuffix('.timer')}.service", text)

    def test_docs_include_install_and_operational_commands(self):
        text = (SYSTEMD_DIR / "README.md").read_text(encoding="utf-8")

        self.assertIn("sudo systemctl enable --now hawkstrade-secrets.service", text)
        self.assertIn("sudo systemctl enable --now", text)
        self.assertIn("journalctl -u hawkstrade-risk-check.service", text)
        self.assertIn("systemctl list-timers 'hawkstrade-*'", text)


if __name__ == "__main__":
    unittest.main()
