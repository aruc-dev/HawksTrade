import unittest
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
FILES_REQUIRING_POSTPONED_ANNOTATIONS = [
    "core/alpaca_client.py",
    "core/exit_policy.py",
    "core/order_executor.py",
    "dashboard/app.py",
    "dashboard/data_sources.py",
    "scheduler/reconcile_trade_log.py",
    "scheduler/run_backtest.py",
    "scheduler/run_report.py",
    "scheduler/run_risk_check.py",
    "scheduler/run_scan.py",
    "scripts/check_health_linux.py",
    "strategies/gap_up.py",
    "strategies/ma_crossover.py",
    "strategies/range_breakout.py",
    "strategies/rsi_reversion.py",
    "tracking/trade_log.py",
]


class Python39AnnotationCompatTests(unittest.TestCase):
    def test_runtime_modules_with_union_annotations_defer_evaluation(self):
        for rel_path in FILES_REQUIRING_POSTPONED_ANNOTATIONS:
            with self.subTest(path=rel_path):
                text = (BASE_DIR / rel_path).read_text(encoding="utf-8")
                header = "\n".join(text.splitlines()[:40])
                self.assertIn("from __future__ import annotations", header)
