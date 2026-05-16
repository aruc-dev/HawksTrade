import unittest
from datetime import datetime, timezone

from core.strategy_readiness import evaluate_strategy_live_readiness


def _cfg(strategy="range_breakout", min_trades=2, min_days=30):
    return {
        "mode": "live",
        "strategies": {
            strategy: {
                "live_readiness": {
                    "enabled": True,
                    "min_closed_paper_trades": min_trades,
                    "min_paper_days": min_days,
                },
            },
        },
    }


def _row(
    timestamp="2026-01-01T00:00:00+00:00",
    *,
    strategy="range_breakout",
    mode="paper",
    side="sell",
    status="closed",
):
    return {
        "timestamp": timestamp,
        "mode": mode,
        "strategy": strategy,
        "side": side,
        "status": status,
    }


class StrategyReadinessTests(unittest.TestCase):
    def test_gate_is_skipped_outside_live_mode(self):
        decision = evaluate_strategy_live_readiness(
            "range_breakout",
            mode="paper",
            cfg=_cfg(),
            rows=[],
        )

        self.assertTrue(decision.allowed)

    def test_ungated_strategy_is_allowed_in_live_mode(self):
        decision = evaluate_strategy_live_readiness(
            "momentum",
            mode="live",
            cfg={"mode": "live", "strategies": {"momentum": {}}},
            rows=[],
        )

        self.assertTrue(decision.allowed)

    def test_live_gate_blocks_when_closed_paper_trade_count_is_low(self):
        decision = evaluate_strategy_live_readiness(
            "range_breakout",
            mode="live",
            cfg=_cfg(min_trades=2, min_days=0),
            rows=[_row(side="buy"), _row(mode="backtest"), _row(status="submitted")],
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.context["closed_paper_trades"], 0)
        self.assertIn("needs 2 closed paper trades", decision.reason)

    def test_live_gate_blocks_when_paper_validation_age_is_low(self):
        decision = evaluate_strategy_live_readiness(
            "range_breakout",
            mode="live",
            cfg=_cfg(min_trades=2, min_days=30),
            rows=[
                _row("2026-01-20T00:00:00+00:00"),
                _row("2026-01-25T00:00:00+00:00"),
            ],
            now=datetime(2026, 2, 1, tzinfo=timezone.utc),
        )

        self.assertFalse(decision.allowed)
        self.assertIn("needs 30 paper validation days", decision.reason)

    def test_live_gate_allows_sufficient_count_and_age(self):
        decision = evaluate_strategy_live_readiness(
            "range_breakout",
            mode="live",
            cfg=_cfg(min_trades=2, min_days=30),
            rows=[
                _row("2025-12-15T00:00:00+00:00"),
                _row("2026-01-20T00:00:00+00:00"),
            ],
            now=datetime(2026, 2, 1, tzinfo=timezone.utc),
        )

        self.assertTrue(decision.allowed)
