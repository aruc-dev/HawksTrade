import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.scan_audit import ScanAuditRecorder, latest_audit_records
from scheduler import run_scan
from scripts import show_scan_audit


class ScanAuditTests(unittest.TestCase):
    def test_recorder_writes_universe_signals_rejections_and_blocks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audit = ScanAuditRecorder(
                output_dir=Path(tmpdir),
                run_id="scan-test",
                mode="paper",
                dry_run=True,
                run_stocks=True,
                run_crypto=False,
                strategy_filter={"momentum"},
            )
            audit.record_market_context(market_open=True, open_symbols=["MSFT"])
            audit.record_universe(
                "stock",
                ["AAPL", "MSFT"],
                source="screener",
                static_symbols=["MSFT"],
                dynamic_symbols=["AAPL"],
            )
            audit.record_strategy_scan(
                strategy="momentum",
                asset_class="stock",
                universe=["AAPL", "MSFT"],
                signals=[{"symbol": "AAPL", "action": "buy", "reason": "strong"}],
            )
            audit.record_block(
                stage="risk_pipeline",
                code="duplicate_planned_symbol",
                reason="MSFT is already held",
                symbol="MSFT",
                strategy="momentum",
                asset_class="stock",
            )
            path = audit.finish(status="ok", outcome="completed")

            records = latest_audit_records(Path(tmpdir))

        self.assertEqual(path.name[:11], "scan_audit_")
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["run_id"], "scan-test")
        self.assertEqual(record["universes"]["stock"]["dynamic_symbols"], ["AAPL"])
        self.assertEqual(record["signals"][0]["symbol"], "AAPL")
        self.assertEqual(record["strategies"][0]["rejections"][0]["symbol"], "MSFT")
        self.assertEqual(record["blocks"][0]["code"], "duplicate_planned_symbol")
        self.assertEqual(record["summary"]["no_signal_rejections"], 1)

    def test_run_scan_writes_audit_for_no_signal_and_entry_result(self):
        class FakeMomentum:
            name = "momentum"
            asset_class = "stocks"

            def scan(self, universe, **kwargs):
                return [{"symbol": "AAPL", "action": "buy", "reason": "test signal"}]

        class AllowProtectionManager:
            enabled = False

            def evaluate_entry(self, symbol, strategy):
                return SimpleNamespace(allowed=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            audit = ScanAuditRecorder(
                output_dir=Path(tmpdir),
                run_id="scan-integration",
                mode="paper",
                dry_run=True,
                run_stocks=True,
                run_crypto=False,
            )
            with (
                patch.object(run_scan.ProtectionManager, "from_config", return_value=AllowProtectionManager()),
                patch.object(run_scan.ac, "is_market_open", return_value=True),
                patch.object(
                    run_scan.ac,
                    "get_stock_bars",
                    return_value={"SPY": [object()] * 252, "QQQ": [object()] * 51},
                ),
                patch.object(run_scan, "get_open_symbols", side_effect=[[], []]),
                patch.object(run_scan.rm, "daily_loss_exceeded", return_value=False),
                patch.object(run_scan, "get_stock_universe", return_value=["AAPL", "MSFT"]),
                patch.object(run_scan, "STOCK_STRATEGIES", [FakeMomentum()]),
                patch.object(run_scan, "get_open_trades", return_value=[]),
                patch.object(run_scan, "print_snapshot"),
                patch.object(run_scan.oe, "enter_position", return_value={"symbol": "AAPL", "status": "dry_run"}),
            ):
                run_scan.run(
                    run_stocks=True,
                    run_crypto=False,
                    dry_run=True,
                    audit_recorder=audit,
                )

            files = list(Path(tmpdir).glob("scan_audit_*.jsonl"))
            self.assertEqual(len(files), 1)
            record = json.loads(files[0].read_text(encoding="utf-8").strip())

        self.assertEqual(record["run_id"], "scan-integration")
        self.assertEqual(record["universes"]["stock"]["evaluated_symbols"], ["AAPL", "MSFT"])
        self.assertEqual(record["universes"]["crypto"]["skipped_reason"], "run_crypto=false")
        self.assertEqual(record["strategies"][0]["signals"][0]["symbol"], "AAPL")
        self.assertEqual(record["strategies"][0]["rejections"][0]["symbol"], "MSFT")
        self.assertEqual(record["entry_results"][0]["status"], "dry_run")
        self.assertEqual(record["summary"]["signals"], 1)

    def test_show_scan_audit_formats_record(self):
        text = show_scan_audit.format_record(
            {
                "run_id": "scan-view",
                "status": "ok",
                "outcome": "completed",
                "dry_run": True,
                "market_open": True,
                "open_symbols": ["AAPL"],
                "universes": {
                    "stock": {
                        "evaluated_count": 2,
                        "source": "screener",
                        "dynamic_symbols": ["MSFT"],
                        "static_symbols": ["AAPL"],
                        "evaluated_symbols": ["MSFT", "AAPL"],
                    }
                },
                "summary": {"signals": 1, "entry_results": 1, "blocks": 1, "no_signal_rejections": 1},
                "strategies": [
                    {"strategy": "momentum", "evaluated_count": 2, "signal_count": 1, "rejections": [{}], "status": "completed"}
                ],
                "blocks": [
                    {
                        "stage": "risk_pipeline",
                        "code": "duplicate_planned_symbol",
                        "strategy": "momentum",
                        "symbol": "AAPL",
                        "reason": "already planned",
                    }
                ],
            }
        )

        self.assertIn("Run scan-view", text)
        self.assertIn("Stock universe: 2 evaluated", text)
        self.assertIn("momentum:AAPL - already planned", text)


if __name__ == "__main__":
    unittest.main()
