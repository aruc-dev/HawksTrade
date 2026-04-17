import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from scripts import check_health_linux as health


class CheckHealthLinuxTests(unittest.TestCase):
    def _write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_load_cron_jobs_parses_supported_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            cron_file = Path(tmp) / "hawkstrade-pacific.cron"
            self._write(
                cron_file,
                """
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin
35 6 * * 1-5 cd "$HAWKSTRADE_DIR" && mkdir -p logs && python3 scheduler/run_scan.py --stocks-only >> logs/cron.log 2>&1
0 * * * * cd "$HAWKSTRADE_DIR" && mkdir -p logs && python3 scheduler/run_scan.py --crypto-only >> logs/cron.log 2>&1
""".strip()
                + "\n",
            )

            jobs = health.load_cron_jobs(cron_file)

            self.assertEqual([job.key for job in jobs], ["stock_scan", "crypto_scan"])
            self.assertEqual(jobs[0].pattern.cron_text, "35 6 * * 1-5")
            self.assertEqual(jobs[1].pattern.cron_text, "0 * * * *")

    def test_evaluate_job_health_detects_missed_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            cron_file = Path(tmp) / "hawkstrade-pacific.cron"
            log_dir = Path(tmp) / "logs"
            self._write(
                cron_file,
                """
0 * * * * cd "$HAWKSTRADE_DIR" && mkdir -p logs && python3 scheduler/run_scan.py --crypto-only >> logs/cron.log 2>&1
""".strip()
                + "\n",
            )
            self._write(
                log_dir / "scan_20260417.log",
                """
2026-04-17 10:00:00,000 [INFO] run_scan: =======================================================
2026-04-17 10:00:00,100 [INFO] run_scan: HawksTrade scan started | mode=PAPER | intraday=OFF | dry_run=OFF
2026-04-17 10:00:00,200 [INFO] run_scan: --- Running crypto strategies ---
2026-04-17 10:00:00,300 [INFO] run_scan: Scan complete.
""".strip()
                + "\n",
            )

            jobs = health.load_cron_jobs(cron_file)
            runtime = health.load_runtime_records(log_dir)
            report = health.evaluate_job_health(
                jobs,
                runtime["scan"],
                now=datetime(2026, 4, 17, 12, 5, 0),
                lookback_hours=3.0,
            )

            crypto = report[0]
            self.assertEqual(crypto.key, "crypto_scan")
            self.assertEqual(crypto.expected_runs, 3)
            self.assertEqual(crypto.missed_runs, 2)
            self.assertEqual(crypto.status, "red")
            self.assertEqual(crypto.last_run_at, datetime(2026, 4, 17, 10, 0, 0, 100000))

    def test_evaluate_job_health_combines_overlapping_scan_cycles(self):
        with tempfile.TemporaryDirectory() as tmp:
            cron_file = Path(tmp) / "hawkstrade-utc.cron"
            self._write(
                cron_file,
                """
0 14-19 * * 1-5 cd "$HAWKSTRADE_DIR" && mkdir -p logs && python3 scheduler/run_scan.py >> logs/cron.log 2>&1
0 * * * * cd "$HAWKSTRADE_DIR" && mkdir -p logs && python3 scheduler/run_scan.py --crypto-only >> logs/cron.log 2>&1
""".strip()
                + "\n",
            )

            jobs = health.load_cron_jobs(cron_file)
            records = [
                health.RunRecord(
                    job_key="full_scan",
                    label="Full scan",
                    start_time=datetime(2026, 4, 17, 18, 0, 0),
                    end_time=datetime(2026, 4, 17, 18, 0, 5),
                    success=True,
                    source_file=cron_file,
                    lines=["full"],
                ),
                health.RunRecord(
                    job_key="crypto_scan",
                    label="Crypto scan",
                    start_time=datetime(2026, 4, 17, 19, 0, 0),
                    end_time=datetime(2026, 4, 17, 19, 0, 5),
                    success=True,
                    source_file=cron_file,
                    lines=["crypto"],
                ),
            ]

            report = health.evaluate_job_health(
                jobs,
                records,
                now=datetime(2026, 4, 17, 19, 59, 0),
                lookback_hours=2.0,
            )

            full = next(job for job in report if job.key == "full_scan")
            crypto = next(job for job in report if job.key == "crypto_scan")

            self.assertEqual(full.expected_runs, 2)
            self.assertEqual(crypto.expected_runs, 2)
            self.assertEqual(full.missed_runs, 0)
            self.assertEqual(crypto.missed_runs, 0)
            self.assertEqual(full.status, "green")
            self.assertEqual(crypto.status, "green")
            self.assertEqual(health._display_missed_runs(report), 0)

    def test_build_report_renders_html_and_terminal_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cron_file = tmp_path / "health.cron"
            log_dir = tmp_path / "logs"
            html_output = tmp_path / "health.html"
            self._write(
                cron_file,
                """
30 16 * * 1-5 cd "$HAWKSTRADE_DIR" && mkdir -p logs && python3 scheduler/run_report.py >> logs/cron.log 2>&1
""".strip()
                + "\n",
            )
            self._write(
                log_dir / "report_20260416.log",
                """
2026-04-16 16:30:00,000 [INFO] run_report: === DAILY REPORT ===
2026-04-16 16:30:00,500 [INFO] run_report: Daily report saved: /tmp/report.txt
""".strip()
                + "\n",
            )

            alpaca_state = health.AlpacaState(
                connected=True,
                account_error=None,
                positions_error=None,
                portfolio_value=101213.36,
                cash=51104.76,
                buying_power=289061.23,
                broker_positions=[
                    {
                        "symbol": "DOGEUSD",
                        "qty": 52769.1095,
                        "avg_entry_price": 0.0947,
                        "current_price": 0.0978,
                        "market_value": 5160.0,
                        "unrealized_pnl": 163.58,
                        "unrealized_pnl_pct": 0.0327,
                    }
                ],
                trade_log_open_rows=[],
            )

            summary = {
                "generated_at": "2026-04-16T16:35:00",
                "total_trades": 4,
                "wins": 2,
                "losses": 2,
                "win_rate": 0.5,
                "avg_win_pct": 0.01,
                "avg_loss_pct": -0.01,
                "total_pnl_pct": 0.01,
                "realized_pnl_pct": 0.01,
                "realized_pnl_dollars": 0.46,
                "open_positions": 1,
                "unrealized_pnl_dollars": 163.58,
                "total_pnl_dollars": 164.04,
                "monthly_pnl": {},
                "by_strategy": {},
            }

            with (
                patch.object(health, "load_closed_trades", return_value=pd.DataFrame()),
                patch.object(health, "compute_summary", return_value=summary),
            ):
                report = health.build_health_report(
                    cron_template="custom",
                    cron_file=cron_file,
                    log_dir=log_dir,
                    html_output=html_output,
                    now=datetime(2026, 4, 16, 16, 35, 0),
                    alpaca_state=alpaca_state,
                )

            terminal = health.format_terminal_report(report, use_color=False)
            html_text = health.write_html_report(report).read_text(encoding="utf-8")

            self.assertEqual(report.overall_status, "green")
            self.assertEqual(report.lookback_hours, 4.0)
            self.assertIn("HAWKSTRADE LINUX HEALTH CHECK", terminal)
            self.assertIn("Window    : last 4h", terminal)
            self.assertIn("Overall   : [OK]", terminal)
            self.assertIn("Alpaca connectivity : OK [OK]", terminal)
            self.assertIn("Errors in logs     : NO [OK]", terminal)
            self.assertNotIn("\x1b[", terminal)
            self.assertIn("[OK]", terminal)
            self.assertIn("Generated", html_text)
            self.assertIn("Linux Health Check", html_text)
            self.assertIn("Daily report", html_text)
            self.assertIn("Window", html_text)
            self.assertIn("2026-04-16 16:35:00", html_text)


if __name__ == "__main__":
    unittest.main()
