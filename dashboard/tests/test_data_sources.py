"""Unit tests for dashboard.data_sources — CSV/JSON readers + subprocess wrapper."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from dashboard import data_sources


class ReadTradesTests(unittest.TestCase):
    def test_returns_empty_list_when_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nope.csv"
            self.assertEqual(data_sources.read_trades(path), [])

    def test_parses_csv_with_expected_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades.csv"
            path.write_text(
                "timestamp,symbol,strategy,side,qty,entry_price,exit_price,pnl_pct,status\n"
                "2026-04-20T14:00:00+00:00,AAPL,momentum,sell,10,100,110,0.10,closed\n"
            )
            rows = data_sources.read_trades(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["symbol"], "AAPL")
            self.assertEqual(rows[0]["status"], "closed")


class SplitTradesTests(unittest.TestCase):
    def test_partitions_correctly(self):
        rows = [
            {"status": "open"},
            {"status": "closed"},
            {"status": "partially_filled"},
            {"status": "weird"},
            {"status": ""},
        ]
        buckets = data_sources.split_trades_by_status(rows)
        self.assertEqual(len(buckets["open"]), 2)  # open + partially_filled
        self.assertEqual(len(buckets["closed"]), 1)
        self.assertEqual(len(buckets["other"]), 2)


class TimestampParsingTests(unittest.TestCase):
    def test_parse_iso_accepts_z_suffix(self):
        dt = data_sources._parse_iso("2026-04-20T14:00:00Z")
        self.assertIsNotNone(dt)
        self.assertEqual(data_sources._to_utc(dt).isoformat(), "2026-04-20T14:00:00+00:00")


class PositionEnrichmentTests(unittest.TestCase):
    def test_enriches_positions_with_strategy_and_hold_days(self):
        positions = [
            {"symbol": "AAPL", "qty": 1},
            {"symbol": "BTCUSD", "qty": 0.1},
            {"symbol": "MSFT", "qty": 2},
        ]
        rows = [
            {"timestamp": "2026-04-18T12:00:00+00:00", "symbol": "AAPL",
             "strategy": "momentum", "side": "buy", "status": "open"},
            {"timestamp": "2026-04-19T00:00:00Z", "symbol": "BTC/USD",
             "strategy": "range_breakout", "side": "buy", "status": "open"},
            {"timestamp": "2026-04-17T00:00:00+00:00", "symbol": "MSFT",
             "strategy": "old", "side": "sell", "status": "closed"},
        ]
        now = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
        out = data_sources.enrich_positions_with_trade_metadata(positions, rows, now)

        by_symbol = {p["symbol"]: p for p in out}
        self.assertEqual(by_symbol["AAPL"]["strategy"], "momentum")
        self.assertEqual(by_symbol["AAPL"]["hold_days"], 2.0)
        self.assertEqual(by_symbol["BTCUSD"]["strategy"], "range_breakout")
        self.assertEqual(by_symbol["BTCUSD"]["hold_days"], 1.5)
        self.assertEqual(by_symbol["MSFT"]["strategy"], "unknown")
        self.assertIsNone(by_symbol["MSFT"]["hold_days"])


class ReadDailyBaselineTests(unittest.TestCase):
    def test_returns_none_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nope.json"
            self.assertIsNone(data_sources.read_daily_baseline(path))

    def test_rejects_invalid_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "b.json"
            path.write_text(json.dumps({"random": "junk"}))
            self.assertIsNone(data_sources.read_daily_baseline(path))

    def test_returns_valid_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "b.json"
            payload = {
                "date": "2026-04-20",
                "portfolio_value": 101678.79,
                "created_at": "2026-04-20T05:40:45+00:00",
                "session_timezone": "America/New_York",
            }
            path.write_text(json.dumps(payload))
            out = data_sources.read_daily_baseline(path)
            self.assertEqual(out["portfolio_value"], 101678.79)


class RunCheckSystemdTests(unittest.TestCase):
    def test_missing_script_returns_graceful_error(self):
        with patch.object(data_sources, "cfg") as mock_cfg:
            mock_cfg.return_value.check_systemd_script = Path("/nope/missing.sh")
            out = data_sources.run_check_systemd()
        self.assertFalse(out["ok"])
        self.assertIn("not found", out["error"])

    def test_timeout_is_handled_gracefully(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "slow.sh"
            script.write_text("#!/usr/bin/env bash\nexit 0\n")
            script.chmod(0o755)
            with patch.object(data_sources, "cfg") as mock_cfg:
                mock_cfg.return_value.check_systemd_script = script
                with patch.object(
                    data_sources.subprocess, "run",
                    side_effect=subprocess.TimeoutExpired("bash", 10),
                ):
                    out = data_sources.run_check_systemd()
        self.assertIn("timed out", out["error"])

    def test_successful_run_returns_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "ok.sh"
            script.write_text("#!/usr/bin/env bash\necho hello\nexit 0\n")
            script.chmod(0o755)
            with patch.object(data_sources, "cfg") as mock_cfg:
                mock_cfg.return_value.check_systemd_script = script
                out = data_sources.run_check_systemd(timeout_sec=5)
        self.assertTrue(out["ok"])
        self.assertIn("hello", out["stdout"])


class ReadLatestHealthSnapshotTests(unittest.TestCase):
    def test_returns_latest_snapshot_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "health_20260420T010000.json").write_text(json.dumps({"overall_status": "yellow"}))
            latest = root / "health_20260420T020000.json"
            latest.write_text(json.dumps({"overall_status": "green", "generated_at": "2026-04-20T02:00:00"}))

            out = data_sources.read_latest_health_snapshot(root)

        self.assertTrue(out["ok"])
        self.assertEqual(out["path"], str(latest))
        self.assertEqual(out["data"]["overall_status"], "green")

    def test_returns_error_when_snapshot_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = data_sources.read_latest_health_snapshot(Path(tmp))

        self.assertFalse(out["ok"])
        self.assertIn("No health snapshot JSON found", out["error"])


class ReadRecentLogIssuesTests(unittest.TestCase):
    def test_reads_recent_runtime_warnings_and_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scan_20260420.log").write_text(
                "ok line\n"
                "2026-04-20 WARNING strategy issue\n"
                "2026-04-20 ERROR broker issue\n"
            )
            (root / "dashboard_access_20260420.log").write_text(
                "identity=local-ssh status=401\n"
            )

            out = data_sources.read_recent_log_issues(root)

        self.assertEqual([item["level"] for item in out], ["WARNING", "ERROR"])
        self.assertEqual({item["file"] for item in out}, {"scan_20260420.log"})


if __name__ == "__main__":
    unittest.main()
