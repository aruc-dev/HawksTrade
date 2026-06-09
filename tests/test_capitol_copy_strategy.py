import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from strategies.capitol_copy import CapitolCopyStrategy


def _signal(**overrides):
    row = {
        "signal_id": "sig-aapl",
        "created_at": "2026-06-08T14:00:00+00:00",
        "ticker": "AAPL",
        "asset_type": "stock",
        "side": "buy",
        "source_tx_ids": ["tx-1"],
        "conviction_score": 0.8,
        "freshness_score": 0.7,
        "entry_quality_score": 0.75,
        "target_weight_pct": 0.03,
        "rationale": "high scoring member buy",
        "blocked_reason": None,
    }
    row.update(overrides)
    return row


class CapitolCopyStrategyTests(unittest.TestCase):
    def _cfg(self, path: Path, **overrides):
        strategy_cfg = {
            "enabled": True,
            "signal_path": str(path),
            "max_signal_age_hours": 72,
            "min_conviction_score": 0.65,
            "min_freshness_score": 0.35,
            "min_entry_quality_score": 0.55,
            "max_signals": 2,
            "allowed_asset_types": ["stock"],
        }
        strategy_cfg.update(overrides)
        return {"strategies": {"capitol_copy": strategy_cfg}}

    def test_scan_accepts_ranked_hawkscapitol_buy_signals_with_provenance(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "signals.json"
            path.write_text(
                json.dumps([
                    _signal(signal_id="sig-low", ticker="MSFT", conviction_score=0.7, target_weight_pct=0.01),
                    _signal(signal_id="sig-high", ticker="NVDA", conviction_score=0.9, source_tx_ids=["tx-2"]),
                    _signal(signal_id="blocked", ticker="TSLA", blocked_reason="entry_quality_below_threshold"),
                    _signal(signal_id="sell", ticker="AMZN", side="sell"),
                    _signal(signal_id="stale", ticker="META", created_at="2026-06-01T14:00:00+00:00"),
                ]),
                encoding="utf-8",
            )

            strategy = CapitolCopyStrategy(cfg=self._cfg(path, max_signals=1))
            signals = strategy.scan(
                ["AAPL"],
                current_time=datetime(2026, 6, 8, 15, 0, tzinfo=timezone.utc),
            )

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["symbol"], "NVDA")
        self.assertEqual(signals[0]["strategy"], "capitol_copy")
        self.assertEqual(signals[0]["source_system"], "HawksCapitol")
        self.assertEqual(signals[0]["source_signal_id"], "sig-high")
        self.assertEqual(signals[0]["source_tx_ids"], ["tx-2"])
        self.assertIn("composite_score", signals[0]["source_scores"])

    def test_scan_skips_existing_symbols_and_threshold_failures(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "signals.json"
            path.write_text(
                json.dumps([
                    _signal(signal_id="held", ticker="AAPL"),
                    _signal(signal_id="weak", ticker="MSFT", conviction_score=0.2),
                ]),
                encoding="utf-8",
            )

            strategy = CapitolCopyStrategy(cfg=self._cfg(path))
            signals = strategy.scan(
                ["AAPL", "MSFT"],
                existing_symbols=["AAPL"],
                current_time=datetime(2026, 6, 8, 15, 0, tzinfo=timezone.utc),
            )

        self.assertEqual(signals, [])

    def test_missing_signal_file_fails_closed(self):
        strategy = CapitolCopyStrategy(cfg=self._cfg(Path("/tmp/does-not-exist-hawkscapitol-signals.json")))

        self.assertEqual(strategy.scan([], current_time=datetime(2026, 6, 8, tzinfo=timezone.utc)), [])

    def test_non_positive_max_signals_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "signals.json"
            path.write_text(json.dumps([_signal(ticker="AAPL")]), encoding="utf-8")

            strategy = CapitolCopyStrategy(cfg=self._cfg(path, max_signals=-1))
            signals = strategy.scan(
                ["AAPL"],
                current_time=datetime(2026, 6, 8, 15, 0, tzinfo=timezone.utc),
            )

        self.assertEqual(signals, [])


if __name__ == "__main__":
    unittest.main()
