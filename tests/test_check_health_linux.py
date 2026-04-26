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

    def _sample_report(
        self,
        *,
        overall_status="green",
        job_health=None,
        log_errors=None,
        log_warnings=None,
        price_failures=None,
    ):
        generated_at = datetime(2026, 4, 17, 18, 5, 0)
        return health.HealthReport(
            generated_at=generated_at,
            lookback_hours=4.0,
            cron_template="custom",
            cron_file=Path("/tmp/health.cron"),
            local_timezone="UTC",
            overall_status=overall_status,
            alpaca=health.AlpacaState(
                connected=True,
                account_error=None,
                positions_error=None,
                portfolio_value=100000,
                cash=50000,
                buying_power=200000,
                broker_positions=[],
                trade_log_open_rows=[],
            ),
            job_health=job_health or [],
            trade_summary={
                "total_trades": 0,
                "realized_pnl_dollars": 0.0,
                "realized_pnl_pct": 0.0,
                "unrealized_pnl_dollars": 0.0,
                "total_pnl_dollars": 0.0,
            },
            log_errors=log_errors or [],
            log_warnings=log_warnings or [],
            price_failures=price_failures or [],
            html_output=Path("/tmp/health.html"),
        )

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

    def test_evaluate_job_health_treats_recent_expected_run_as_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            cron_file = Path(tmp) / "hawkstrade-pacific.cron"
            self._write(
                cron_file,
                """
0 * * * * cd "$HAWKSTRADE_DIR" && mkdir -p logs && python3 scheduler/run_scan.py --crypto-only >> logs/cron.log 2>&1
""".strip()
                + "\n",
            )

            jobs = health.load_cron_jobs(cron_file)
            records = [
                health.RunRecord(
                    job_key="crypto_scan",
                    label="Crypto scan",
                    start_time=datetime(2026, 4, 17, 11, 0, 0),
                    end_time=datetime(2026, 4, 17, 11, 0, 5),
                    success=True,
                    source_file=cron_file,
                    lines=["crypto"],
                ),
            ]
            report = health.evaluate_job_health(
                jobs,
                records,
                now=datetime(2026, 4, 17, 12, 0, 29),
                lookback_hours=1.1,
            )

            crypto = report[0]
            self.assertEqual(crypto.expected_runs, 1)
            self.assertEqual(crypto.missed_runs, 0)
            self.assertEqual(crypto.status, "green")

    def test_evaluate_job_health_marks_failed_recent_run_unhealthy_inside_grace_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            cron_file = Path(tmp) / "hawkstrade-pacific.cron"
            self._write(
                cron_file,
                """
30 20 * * 1-5 cd "$HAWKSTRADE_DIR" && mkdir -p logs && python3 scheduler/run_report.py >> logs/cron.log 2>&1
""".strip()
                + "\n",
            )

            jobs = health.load_cron_jobs(cron_file)
            records = [
                health.RunRecord(
                    job_key="daily_report",
                    label="Daily report",
                    start_time=datetime(2026, 4, 17, 20, 30, 0),
                    end_time=datetime(2026, 4, 17, 20, 30, 5),
                    success=False,
                    source_file=cron_file,
                    lines=["report failed"],
                    notes=["marker status=error"],
                ),
            ]
            report = health.evaluate_job_health(
                jobs,
                records,
                now=datetime(2026, 4, 17, 20, 31, 0),
                lookback_hours=4.0,
            )

            daily = report[0]
            self.assertEqual(daily.expected_runs, 0)
            self.assertEqual(daily.missed_runs, 0)
            self.assertEqual(daily.status, "red")
            self.assertEqual(daily.latest_note, "Last run did not complete cleanly")

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

    def test_build_report_alerts_on_pending_exit_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cron_file = tmp_path / "health.cron"
            log_dir = tmp_path / "logs"
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
2026-04-17 18:00:00,000 [INFO] run_risk_check: RUN_START script=run_risk_check run_id=risk-2 dry_run=0
2026-04-17 18:00:00,500 [WARNING] core.order_executor: Exit order submitted for AAPL but not filled yet; leaving trade log open
2026-04-17 18:00:01,000 [INFO] run_risk_check: RUN_END script=run_risk_check run_id=risk-2 status=ok duration_s=1.000 outcome=completed
""".strip()
                + "\n",
            )
            alpaca_state = health.AlpacaState(
                connected=True,
                account_error=None,
                positions_error=None,
                portfolio_value=100000,
                cash=50000,
                buying_power=200000,
                broker_positions=[],
                trade_log_open_rows=[],
            )

            report = health.build_health_report(
                cron_template="custom",
                cron_file=cron_file,
                log_dir=log_dir,
                html_output=tmp_path / "health.html",
                now=datetime(2026, 4, 17, 18, 5, 0),
                lookback_hours=1.0,
                alpaca_state=alpaca_state,
                trade_summary={"total_trades": 0},
                price_failure_state_file=tmp_path / "missing_price_failures.json",
            )

            items = health.collect_alert_items(report)

            self.assertEqual(report.overall_status, "red")
            self.assertTrue(any("Pending exit warnings: 1 in window" in item for item in items))

    def test_alert_items_include_red_operational_failures(self):
        job = health.JobHealth(
            key="risk_check",
            label="Risk check",
            schedule_lines=["0,15,30,45 * * * *"],
            last_run_at=datetime(2026, 4, 17, 17, 0, 0),
            last_success_at=datetime(2026, 4, 17, 17, 0, 0),
            last_duration=timedelta(seconds=10),
            missed_runs=2,
            expected_runs=4,
            status="red",
            latest_note="No run in last 4 hours",
        )
        finding = health.LogFinding(
            timestamp=datetime(2026, 4, 17, 18, 0, 0),
            level="ERROR",
            logger="run_scan",
            message="Alpaca auth failed",
            source_file=Path("scan_20260417.log"),
            raw="raw",
        )
        price_failure = health.PriceFailureState(
            symbol="AAPL",
            price_symbol="AAPL",
            asset_class="stock",
            count=3,
            threshold=3,
            last_failed_at="2026-04-17T18:00:00Z",
            reason="exception",
            error_category="timeout",
            retryable=True,
            status_code=408,
            last_error="quote timeout",
        )
        report = self._sample_report(
            overall_status="red",
            job_health=[job],
            log_errors=[finding],
            price_failures=[price_failure],
        )

        items = health.collect_alert_items(report)

        self.assertTrue(any("Overall health is [NOK]" in item for item in items))
        self.assertTrue(any("Cron Risk check [NOK]: 2 missed run(s)" in item for item in items))
        self.assertTrue(any("Price fetch AAPL [NOK]" in item for item in items))
        self.assertTrue(any("Log errors: 1 in window" in item for item in items))

    def test_write_alert_files_updates_latest_and_timestamped_alert(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self._sample_report(overall_status="red")
            items = ["Overall health is [NOK]"]

            result = health.write_alert_files(report, items, alert_dir=tmp)

            self.assertTrue(result.active)
            self.assertIsNotNone(result.latest_path)
            self.assertIsNotNone(result.event_path)
            self.assertTrue(result.latest_path.exists())
            self.assertTrue(result.event_path.exists())
            self.assertIn("HAWKSTRADE HEALTH ALERT", result.latest_path.read_text(encoding="utf-8"))
            self.assertIn("Overall health is [NOK]", result.event_path.read_text(encoding="utf-8"))

    def test_write_alert_files_clears_latest_when_healthy(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self._sample_report(overall_status="green")

            result = health.write_alert_files(report, [], alert_dir=tmp)

            self.assertFalse(result.active)
            self.assertIsNotNone(result.latest_path)
            self.assertIsNone(result.event_path)
            self.assertTrue(result.latest_path.exists())
            self.assertIn("No active health alert.", result.latest_path.read_text(encoding="utf-8"))

    def test_write_health_snapshots_creates_html_json_and_prunes_old_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_dir = Path(tmp)
            self._write(snapshot_dir / "health_20260401T000000.html", "old html")
            self._write(snapshot_dir / "health_20260401T000000.json", "{}")
            self._write(snapshot_dir / "health_20260416T000000.json", "{}")
            report = self._sample_report(overall_status="yellow")

            result = health.write_health_snapshots(report, snapshot_dir=tmp, retention_days=7)

            self.assertTrue(result.html_path.exists())
            self.assertTrue(result.json_path.exists())
            self.assertFalse((snapshot_dir / "health_20260401T000000.html").exists())
            self.assertFalse((snapshot_dir / "health_20260401T000000.json").exists())
            self.assertTrue((snapshot_dir / "health_20260416T000000.json").exists())
            self.assertEqual(len(result.deleted_paths), 2)
            self.assertIn("Linux Health Check", result.html_path.read_text(encoding="utf-8"))
            payload = health.json.loads(result.json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["overall_status"], "yellow")
            self.assertEqual(payload["alpaca"]["open_position_count"], 0)
            self.assertEqual(payload["lookback_hours"], 4.0)

    def test_prune_health_snapshots_zero_retention_disables_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_dir = Path(tmp)
            old_path = snapshot_dir / "health_20260401T000000.json"
            self._write(old_path, "{}")

            deleted = health.prune_health_snapshots(
                snapshot_dir,
                now=datetime(2026, 4, 17, 18, 5, 0),
                retention_days=0,
            )

            self.assertEqual(deleted, [])
            self.assertTrue(old_path.exists())

    def test_health_report_to_dict_serializes_findings_and_jobs(self):
        job = health.JobHealth(
            key="risk_check",
            label="Risk check",
            schedule_lines=["0,15,30,45 * * * *"],
            last_run_at=datetime(2026, 4, 17, 18, 0, 0),
            last_success_at=datetime(2026, 4, 17, 18, 0, 0),
            last_duration=timedelta(seconds=5),
            missed_runs=0,
            expected_runs=1,
            status="green",
        )
        finding = health.LogFinding(
            timestamp=datetime(2026, 4, 17, 18, 1, 0),
            level="ERROR",
            logger="run_scan",
            message="boom",
            source_file=Path("scan.log"),
            raw="raw",
        )
        report = self._sample_report(job_health=[job], log_errors=[finding])

        payload = health.health_report_to_dict(report)

        self.assertEqual(payload["job_health"][0]["last_duration_s"], 5.0)
        self.assertEqual(payload["job_health"][0]["last_run_at"], "2026-04-17T18:00:00")
        self.assertEqual(payload["log_errors"][0]["message"], "boom")
        self.assertEqual(payload["html_output"], "/tmp/health.html")

    def test_fetch_alpaca_state_reconciles_before_trade_log_snapshot(self):
        from core import alpaca_client as ac

        account = type(
            "Account",
            (),
            {"portfolio_value": "100000", "cash": "50000", "buying_power": "200000"},
        )()
        position = type(
            "Position",
            (),
            {
                "symbol": "AAPL",
                "qty": "2",
                "avg_entry_price": "100",
                "current_price": "101",
                "market_value": "202",
                "unrealized_pl": "2",
                "unrealized_plpc": "0.01",
            },
        )()
        reconciled_rows = [
            {
                "symbol": "AAPL",
                "qty": "2",
                "entry_price": "100",
                "side": "buy",
                "status": "open",
            }
        ]

        with (
            patch.object(ac, "get_account", return_value=account),
            patch.object(ac, "get_all_positions", return_value=[position]),
            patch.object(ac, "get_open_orders", return_value=[]),
            patch.object(ac, "get_closed_orders", return_value=[]),
            patch.object(health, "get_open_trades", side_effect=[[], reconciled_rows]),
            patch.object(health, "safe_reconcile", return_value={"positions": 1}) as safe_reconcile,
        ):
            state = health.fetch_alpaca_state()

        safe_reconcile.assert_called_once_with(
            positions=[position],
            open_orders=[],
            closed_orders=[],
            context="health.pre_summary",
            logger=health.log,
        )
        self.assertTrue(state.connected)
        self.assertEqual(state.trade_log_open_rows, reconciled_rows)

    def test_fetch_alpaca_state_keeps_connection_when_closed_orders_fail(self):
        from core import alpaca_client as ac

        account = type(
            "Account",
            (),
            {"portfolio_value": "100000", "cash": "50000", "buying_power": "200000"},
        )()
        position = type(
            "Position",
            (),
            {
                "symbol": "AAPL",
                "qty": "2",
                "avg_entry_price": "100",
                "current_price": "101",
                "market_value": "202",
                "unrealized_pl": "2",
                "unrealized_plpc": "0.01",
            },
        )()

        with (
            patch.object(ac, "get_account", return_value=account),
            patch.object(ac, "get_all_positions", return_value=[position]),
            patch.object(ac, "get_open_orders", return_value=[]),
            patch.object(ac, "get_closed_orders", side_effect=RuntimeError("closed orders unavailable")),
            patch.object(health, "get_open_trades", return_value=[]),
            patch.object(health, "safe_reconcile", return_value={"positions": 1}) as safe_reconcile,
            self.assertLogs("check_health_linux", level="WARNING") as logs,
        ):
            state = health.fetch_alpaca_state()

        self.assertTrue(state.connected)
        self.assertIsNone(state.positions_error)
        safe_reconcile.assert_called_once_with(
            positions=[position],
            open_orders=[],
            closed_orders=[],
            context="health.pre_summary",
            logger=health.log,
        )
        self.assertTrue(any("Could not fetch closed broker orders during health snapshot" in message for message in logs.output))

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
            self.assertTrue(all(finding.timestamp == datetime(2026, 4, 17, 18, 0, 0) for finding in errors))
            self.assertEqual(len(warnings), 0)

    def test_tracebacks_older_than_lookback_are_filtered(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "logs"
            self._write(
                log_dir / "scan_20260414.log",
                """
2026-04-14 18:00:00,000 [ERROR] run_scan: Failed to enter AAPL
Traceback (most recent call last):
  File "/tmp/example.py", line 10, in <module>
    raise RuntimeError("old boom")
RuntimeError: old boom
""".strip()
                + "\n",
            )

            runtime = health.load_runtime_records(log_dir)
            errors, warnings = health._find_matching_error_lines(
                runtime["findings_by_file"],
                since=datetime(2026, 4, 17, 18, 0, 0),
            )

            self.assertEqual(errors, [])
            self.assertEqual(warnings, [])

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
