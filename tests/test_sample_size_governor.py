import unittest
from datetime import date

from core.sample_size_governor import (
    closed_trade_count,
    effective_risk_for,
    format_tier_report,
    next_tier_for,
    scale_quantity,
)


def _cfg():
    return {
        "trading": {"max_position_pct": 0.08},
        "strategies": {
            "momentum": {},
            "gap_up": {},
        },
        "validation": {
            "sample_size_scaling": {
                "enabled": True,
                "tiers": [
                    {"name": "exploration", "min_trades": 0, "risk_multiplier": 0.25, "position_cap_pct": 0.02},
                    {"name": "half", "min_trades": 30, "risk_multiplier": 0.50, "position_cap_pct": 0.04},
                    {"name": "full", "min_trades": 100, "risk_multiplier": 1.00, "position_cap_pct": 0.08},
                ],
                "overrides": {},
            }
        },
    }


class SampleSizeGovernorTests(unittest.TestCase):
    def test_tier_boundaries(self):
        cfg = _cfg()

        self.assertEqual(effective_risk_for("gap_up", cfg, closed_trades=0).name, "exploration")
        self.assertEqual(effective_risk_for("gap_up", cfg, closed_trades=29).name, "exploration")
        self.assertEqual(effective_risk_for("gap_up", cfg, closed_trades=30).name, "half")
        self.assertEqual(effective_risk_for("gap_up", cfg, closed_trades=100).name, "full")

    def test_strategy_without_closed_trades_gets_exploration_risk(self):
        tier = effective_risk_for("range_breakout", _cfg(), rows=[])

        self.assertEqual(tier.closed_trades, 0)
        self.assertEqual(tier.risk_multiplier, 0.25)
        self.assertEqual(tier.position_cap_pct, 0.02)

    def test_scale_quantity_applies_risk_and_position_cap(self):
        tier = effective_risk_for("gap_up", _cfg(), closed_trades=0)

        self.assertEqual(
            scale_quantity(10, tier, base_position_cap_pct=0.08, base_cap_qty=8),
            2.0,
        )

    def test_override_requires_reason_and_unexpired_date(self):
        cfg = _cfg()
        cfg["validation"]["sample_size_scaling"]["overrides"]["gap_up"] = {
            "name": "temporary_half",
            "risk_multiplier": 0.5,
            "position_cap_pct": 0.04,
            "reason": "human approved paper ramp",
            "expires_on": "2026-12-31",
        }

        tier = effective_risk_for("gap_up", cfg, closed_trades=3, now=date(2026, 5, 17))

        self.assertTrue(tier.override)
        self.assertEqual(tier.name, "temporary_half")
        self.assertEqual(tier.risk_multiplier, 0.5)

    def test_expired_override_is_ignored(self):
        cfg = _cfg()
        cfg["validation"]["sample_size_scaling"]["overrides"]["gap_up"] = {
            "risk_multiplier": 1.0,
            "position_cap_pct": 0.08,
            "reason": "expired",
            "expires_on": "2025-01-01",
        }

        tier = effective_risk_for("gap_up", cfg, closed_trades=3, now=date(2026, 5, 17))

        self.assertFalse(tier.override)
        self.assertEqual(tier.name, "exploration")

    def test_closed_trade_count_uses_closed_sell_rows_only(self):
        rows = [
            {"strategy": "gap_up", "side": "buy", "status": "closed"},
            {"strategy": "gap_up", "side": "sell", "status": "submitted"},
            {"strategy": "gap_up", "side": "sell", "status": "closed"},
            {"strategy": "gap_up", "side": "sell"},
            {"strategy": "gap_up", "status": "closed"},
            {"strategy": "gap_up"},
        ]

        self.assertEqual(closed_trade_count("gap_up", rows), 1)

    def test_format_report_includes_next_tier(self):
        rows = [{"strategy": "gap_up", "side": "sell", "status": "closed"} for _ in range(29)]

        report = format_tier_report(_cfg(), rows)

        self.assertIn("gap_up", report)
        self.assertIn("1 trades to half", report)
        self.assertEqual(next_tier_for("gap_up", _cfg(), 100), None)


if __name__ == "__main__":
    unittest.main()
