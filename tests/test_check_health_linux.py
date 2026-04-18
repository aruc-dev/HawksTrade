import tempfile
import unittest
from datetime import datetime, timedelta
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
35 6 * * 1-5 cd "$HAWKSTRADE_DIR" && mkdir -p logs && ./scripts/run_hawkstrade_job.sh scheduler/run_scan.py --stocks-only >> logs/cron.log 2>&1
0 * * * * cd "$HAWKSTRADE_DIR" && mkdir -p logs && ./scripts/run_hawkstrade_job.sh scheduler/run_scan.py --crypto-only >> logs/cron.log 2>&1
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

    def test_load_runtime_records_prefers_structured_markers_and_dedupes_cron_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_dir = tmp_path / "logs"
            scan_log = log_dir / "scan_20260417.log"
            cron_log = log_dir / "cron.log"
            structured_lines = """
2026-04-17 18:00:00,000 [INFO] run_scan: RUN_START script=run_scan run_id=scan-1 scan_kind=full run_stocks=1 run_crypto=1 dry_run=0
2026-04-17 18:00:01,000 [INFO] run_scan: RUN_END script=run_scan run_id=scan-1 status=ok duration_s=1.000 outcome=completed
""".strip() + "\n"
            self._write(scan_log, structured_lines)
            self._write(cron_log, structured_lines)

            runtime = health.load_runtime_records(log_dir)

            scan_records = runtime["scan"]
            self.assertEqual(len(scan_records), 1)
            self.assertEqual(scan_records[0].job_key, "full_scan")
            self.assertEqual(scan_records[0].run_id, "scan-1")
            self.assertEqual(scan_records[0].source_file.name, "scan_20260417.log")
            self.assertTrue(scan_records[0].success)

    def test_build_report_lists_troubleshooting_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cron_file = tmp_path / "health.cron"
            log_dir = tmp_path / "logs"
            html_output = tmp_path / "health.html"
            self._write(
                cron_file,
                """
0 18 * * * cd "$HAWKSTRADE_DIR" && mkdir -p logs && python3 scheduler/run_scan.py --crypto-only >> logs/cron.log 2>&1
""".strip()
                + "\n",
            )
            scan_lines = """
2026-04-17 18:00:00,000 [INFO] run_scan: RUN_START script=run_scan run_id=scan-2 scan_kind=crypto run_stocks=0 run_crypto=1 dry_run=0
2026-04-17 18:00:00,500 [WARNING] run_scan: Quote fetch timeout for AMD
2026-04-17 18:00:01,000 [ERROR] run_scan: Order rejected for AAPL
2026-04-17 18:00:02,000 [INFO] run_scan: RUN_END script=run_scan run_id=scan-2 status=ok duration_s=2.000 outcome=completed
""".strip() + "\n"
            self._write(log_dir / "scan_20260417.log", scan_lines)
            self._write(log_dir / "cron.log", scan_lines)

            alpaca_state = health.AlpacaState(
                connected=True,
                account_error=None,
                positions_error=None,
                portfolio_value=101213.36,
                cash=51104.76,
                buying_power=289061.23,
                broker_positions=[],
                trade_log_open_rows=[],
            )
            summary = {
                "generated_at": "2026-04-17T18:05:00",
                "total_trades": 1,
                "wins": 1,
                "losses": 0,
                "win_rate": 1.0,
                "avg_win_pct": 0.01,
                "avg_loss_pct": 0.0,
                "total_pnl_pct": 0.01,
                "realized_pnl_pct": 0.01,
                "realized_pnl_dollars": 0.46,
                "open_positions": 0,
                "unrealized_pnl_dollars": 0.0,
                "total_pnl_dollars": 0.46,
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
                    now=datetime(2026, 4, 17, 18, 5, 0),
                    lookback_hours=1.0,
                    alpaca_state=alpaca_state,
                    price_failure_state_file=tmp_path / "missing_price_failures.json",
                )

            terminal = health.format_terminal_report(report, use_color=False)
            html_text = health.write_html_report(report).read_text(encoding="utf-8")

            self.assertEqual(len(report.log_errors), 1)
            self.assertEqual(len(report.log_warnings), 1)
            self.assertIn("Warnings in logs   : YES [WARN] (1 [WARN])", terminal)
            self.assertIn("TROUBLESHOOTING", terminal)
            self.assertIn("Latest errors:", terminal)
            self.assertIn("Latest warnings:", terminal)
            self.assertIn("scan_20260417.log", terminal)
            self.assertIn("Quote fetch timeout for AMD", terminal)
            self.assertIn("Order rejected for AAPL", terminal)
            self.assertIn("Troubleshooting", html_text)
            self.assertIn("Log Warnings", html_text)
            self.assertIn("Quote fetch timeout for AMD", html_text)
            self.assertIn("Order rejected for AAPL", html_text)

    def test_build_report_marks_repeated_price_failures_unhealthy(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cron_file = tmp_path / "health.cron"
            log_dir = tmp_path / "logs"
            html_output = tmp_path / "health.html"
            price_state = tmp_path / "price_fetch_failures.json"
            self._write(
                cron_file,
                """
0 18 * * * cd "$HAWKSTRADE_DIR" && mkdir -p logs && python3 scheduler/run_risk_check.py >> logs/cron.log 2>&1
""".strip()
                + "\n",
            )
            self._write(
                log_dir / "risk_20260417.log",
                """
2026-04-17 18:00:00,000 [INFO] run_risk_check: RUN_START script=run_risk_check run_id=risk-1 dry_run=0
2026-04-17 18:00:01,000 [INFO] run_risk_check: RUN_END script=run_risk_check run_id=risk-1 status=error duration_s=1.000 stage=price_fetch error_type=RepeatedPriceFetchFailure
""".strip()
                + "\n",
            )
            self._write(
                price_state,
                """
{
  "version": 1,
  "threshold": 3,
  "updated_at": "2026-04-17T18:00:01Z",
  "symbols": {
    "AAPL": {
      "symbol": "AAPL",
      "price_symbol": "AAPL",
      "asset_class": "stock",
      "count": 3,
      "threshold": 3,
      "status": "nok",
      "reason": "exception",
      "last_failed_at": "2026-04-17T18:00:01Z",
      "last_error": "quote timeout",
      "error_category": "timeout",
      "retryable": true,
      "status_code": 408
    }
  }
}
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
                broker_positions=[],
                trade_log_open_rows=[],
            )
            summary = {
                "generated_at": "2026-04-17T18:05:00",
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "avg_win_pct": 0.0,
                "avg_loss_pct": 0.0,
                "total_pnl_pct": 0.0,
                "realized_pnl_pct": 0.0,
                "realized_pnl_dollars": 0.0,
                "open_positions": 0,
                "unrealized_pnl_dollars": 0.0,
                "total_pnl_dollars": 0.0,
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
                    now=datetime(2026, 4, 17, 18, 5, 0),
                    lookback_hours=1.0,
                    alpaca_state=alpaca_state,
                    price_failure_state_file=price_state,
                )

            terminal = health.format_terminal_report(report, use_color=False)
            html_text = health.write_html_report(report).read_text(encoding="utf-8")

            self.assertEqual(report.overall_status, "red")
            self.assertEqual(len(report.price_failures), 1)
            self.assertIn("PRICE FETCH HEALTH", terminal)
            self.assertIn("Repeated price failures : YES [NOK]", terminal)
            self.assertIn("[NOK] Price fetch AAPL: 3/3 consecutive failure(s)", terminal)
            self.assertIn("Price Fetch Health", html_text)
            self.assertIn("AAPL", html_text)

    def test_read_log_lines_preserves_raw_tracebacks(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "logs"
            self._write(
                log_dir / "scan_20260417.log",
                """
2026-04-17 18:00:00,000 [INFO] run_scan: RUN_START script=run_scan run_id=scan-3 scan_kind=full run_stocks=1 run_crypto=1 dry_run=0
Traceback (most recent call last):
  File "/tmp/example.py", line 10, in <module>
    raise RuntimeError("boom")
RuntimeError: boom
2026-04-17 18:00:02,000 [INFO] run_scan: RUN_END script=run_scan run_id=scan-3 status=ok duration_s=2.000 outcome=completed
""".strip()
                + "\n",
            )

            runtime = health.load_runtime_records(log_dir)
            errors, warnings = health._find_matching_error_lines(runtime["findings_by_file"])

            self.assertGreaterEqual(len(errors), 1)
            self.assertTrue(any("Traceback" in finding.message for finding in errors))
            self.assertTrue(any("RuntimeError: boom" in finding.message for finding in errors))
            self.assertEqual(len(warnings), 0)

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
                    price_failure_state_file=tmp_path / "missing_price_failures.json",
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
            self.assertEqual(report.job_health[0].age, timedelta(minutes=5))
            self.assertNotIn("\x1b[", terminal)
            self.assertIn("[OK]", terminal)
            self.assertIn("Generated", html_text)
            self.assertIn("Linux Health Check", html_text)
            self.assertIn("Daily report", html_text)
            self.assertIn("Window", html_text)
            self.assertIn("2026-04-16 16:35:00", html_text)


if __name__ == "__main__":
    unittest.main()
