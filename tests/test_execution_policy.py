import unittest

from core.execution_policy import (
    SINGLE_LEG_POLICY,
    TWO_LEG_POLICY,
    build_entry_execution_plan,
    simulate_backtest_entry_price,
)


def _cfg(enabled=True, ab_enabled=False, fraction=1.0):
    return {
        "execution_policy": {
            "enabled": enabled,
            "policy": TWO_LEG_POLICY,
            "ab_test": {"enabled": ab_enabled, "fraction": fraction},
            "per_asset_class": {
                "stock": {
                    "leg1_fraction": 0.5,
                    "leg1_offset_bps": 0.0,
                    "leg1_timeout_seconds": 0,
                    "leg2_offset_bps": "model",
                }
            },
        }
    }


class ExecutionPolicyTests(unittest.TestCase):
    def test_disabled_policy_returns_single_leg(self):
        plan = build_entry_execution_plan(
            symbol="AAPL",
            strategy="momentum",
            asset_class="stock",
            qty=10,
            side="buy",
            price=100,
            order_type="limit",
            expected_slippage_bps=20,
            cfg=_cfg(enabled=False),
            run_id="run-1",
        )

        self.assertEqual(plan.policy_name, SINGLE_LEG_POLICY)
        self.assertEqual(len(plan.legs), 1)
        self.assertAlmostEqual(plan.legs[0].limit_price, 100.2)

    def test_two_leg_policy_splits_passive_and_aggressive_legs(self):
        plan = build_entry_execution_plan(
            symbol="AAPL",
            strategy="momentum",
            asset_class="stock",
            qty=10,
            side="buy",
            price=100,
            order_type="limit",
            expected_slippage_bps=20,
            cfg=_cfg(),
            run_id="run-1",
        )

        self.assertEqual(plan.policy_name, TWO_LEG_POLICY)
        self.assertEqual([leg.role for leg in plan.legs], ["passive", "aggressive"])
        self.assertEqual([leg.qty for leg in plan.legs], [5.0, 5.0])
        self.assertAlmostEqual(plan.legs[0].limit_price, 100.0)
        self.assertAlmostEqual(plan.legs[1].limit_price, 100.2)

    def test_ab_control_bucket_uses_single_leg(self):
        plan = build_entry_execution_plan(
            symbol="AAPL",
            strategy="momentum",
            asset_class="stock",
            qty=10,
            side="buy",
            price=100,
            order_type="limit",
            expected_slippage_bps=20,
            cfg=_cfg(ab_enabled=True, fraction=0.0),
            run_id="run-1",
        )

        self.assertEqual(plan.policy_name, SINGLE_LEG_POLICY)
        self.assertEqual(plan.bucket, "control")

    def test_backtest_two_leg_fill_averages_passive_and_aggressive(self):
        price, policy = simulate_backtest_entry_price(
            symbol="AAPL",
            strategy="momentum",
            asset_class="stock",
            qty=10,
            side="buy",
            price=100,
            expected_slippage_bps=20,
            cfg=_cfg(),
            run_id="run-1",
            next_bar={"low": 99.5, "high": 100.1},
        )

        self.assertEqual(policy, TWO_LEG_POLICY)
        self.assertAlmostEqual(price, 100.1)

    def test_backtest_two_leg_uses_aggressive_when_passive_misses(self):
        price, policy = simulate_backtest_entry_price(
            symbol="AAPL",
            strategy="momentum",
            asset_class="stock",
            qty=10,
            side="buy",
            price=100,
            expected_slippage_bps=20,
            cfg=_cfg(),
            run_id="run-1",
            next_bar={"low": 100.3, "high": 100.5},
        )

        self.assertEqual(policy, TWO_LEG_POLICY)
        self.assertAlmostEqual(price, 100.2)


if __name__ == "__main__":
    unittest.main()
