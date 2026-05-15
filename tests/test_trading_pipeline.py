import unittest
from types import SimpleNamespace

from core.portfolio_construction import build_order_plan, build_portfolio_target, construct_entry_size
from core.risk_pipeline import EntryRiskContext, evaluate_entry_target
from core.trading_models import signal_from_strategy_dict


class TradingPipelineTests(unittest.TestCase):
    def test_strategy_signal_adapter_preserves_existing_entry_fields(self):
        signal = signal_from_strategy_dict(
            {
                "symbol": "AAPL",
                "action": "BUY",
                "atr_risk_qty": "12.5",
                "atr_stop_price": "190.25",
                "ignored": "kept in raw",
            },
            strategy_name="momentum",
            asset_class="stock",
        )

        target = build_portfolio_target(signal)
        plan = build_order_plan(evaluate_entry_target(target, EntryRiskContext()))

        self.assertEqual(signal.action, "buy")
        self.assertEqual(target.quantity_hint, 12.5)
        self.assertEqual(target.atr_stop_price, 190.25)
        self.assertEqual(plan.symbol, "AAPL")
        self.assertEqual(plan.strategy, "momentum")
        self.assertEqual(plan.asset_class, "stock")
        self.assertEqual(plan.suggested_qty, 12.5)
        self.assertEqual(plan.atr_stop_price, 190.25)

    def test_entry_risk_blocks_duplicate_planned_symbol(self):
        target = build_portfolio_target(signal_from_strategy_dict(
            {"symbol": "BTC/USD", "action": "buy"},
            strategy_name="ma_crossover",
            asset_class="crypto",
        ))

        decision = evaluate_entry_target(
            target,
            EntryRiskContext(
                planned_symbols={"BTCUSD"},
                normalize_symbol=lambda symbol: symbol.replace("/", "").upper(),
            ),
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "duplicate_planned_symbol")

    def test_entry_risk_blocks_empty_symbol(self):
        target = build_portfolio_target(signal_from_strategy_dict(
            {"action": "buy"},
            strategy_name="momentum",
            asset_class="stock",
        ))

        decision = evaluate_entry_target(target, EntryRiskContext())

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "missing_symbol")
        self.assertIsNone(build_order_plan(decision))

    def test_entry_risk_blocks_when_planned_cap_is_reached(self):
        calls = []
        target = build_portfolio_target(signal_from_strategy_dict(
            {"symbol": "MSFT", "action": "buy"},
            strategy_name="momentum",
            asset_class="stock",
        ))

        def cap_reached(asset_class, planned_asset_classes):
            calls.append((asset_class, dict(planned_asset_classes)))
            return True

        decision = evaluate_entry_target(
            target,
            EntryRiskContext(
                planned_asset_classes={"AAPL": "stock"},
                cap_reached=cap_reached,
            ),
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "planned_cap_reached")
        self.assertEqual(calls, [("stock", {"AAPL": "stock"})])

    def test_entry_risk_blocks_when_protection_refresh_failed(self):
        target = build_portfolio_target(signal_from_strategy_dict(
            {"symbol": "MSFT", "action": "buy"},
            strategy_name="momentum",
            asset_class="stock",
        ))

        decision = evaluate_entry_target(
            target,
            EntryRiskContext(protection_entries_blocked=True),
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "protection_refresh_failed")

    def test_entry_risk_blocks_active_protection_lock(self):
        class FakeProtectionManager:
            def evaluate_entry(self, symbol, strategy):
                return SimpleNamespace(
                    allowed=False,
                    reason="cooldown",
                    lock=SimpleNamespace(lock_type="symbol_cooldown", scope="symbol", key="AAPL"),
                )

        target = build_portfolio_target(signal_from_strategy_dict(
            {"symbol": "AAPL", "action": "buy"},
            strategy_name="momentum",
            asset_class="stock",
        ))

        decision = evaluate_entry_target(
            target,
            EntryRiskContext(protection_manager=FakeProtectionManager()),
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "protection_lock")
        self.assertEqual(decision.context["lock_type"], "symbol_cooldown")

    def test_non_buy_signals_do_not_create_entry_order_plans(self):
        target = build_portfolio_target(signal_from_strategy_dict(
            {"symbol": "AAPL", "action": "sell"},
            strategy_name="momentum",
            asset_class="stock",
        ))

        decision = evaluate_entry_target(target, EntryRiskContext())

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "unsupported_action")
        self.assertIsNone(build_order_plan(decision))

    def test_entry_sizing_preserves_atr_then_kelly_then_pretrade_precedence(self):
        def capper(price, qty):
            return min(qty, 8.0)

        def kelly_sizer(price):
            return 6.0

        atr_sizing = construct_entry_size(
            price=100.0,
            strategy="momentum",
            pre_trade_qty=3.0,
            suggested_qty=10.0,
            kelly_sizer=kelly_sizer,
            capper=capper,
        )
        kelly_sizing = construct_entry_size(
            price=100.0,
            strategy="momentum",
            pre_trade_qty=3.0,
            suggested_qty=None,
            kelly_sizer=kelly_sizer,
            capper=capper,
        )
        pretrade_sizing = construct_entry_size(
            price=100.0,
            strategy="range_breakout",
            pre_trade_qty=3.0,
            suggested_qty=None,
            kelly_sizer=kelly_sizer,
            capper=capper,
        )

        self.assertEqual(atr_sizing.source, "signal_quantity_hint")
        self.assertEqual(atr_sizing.requested_qty, 10.0)
        self.assertEqual(atr_sizing.capped_qty, 8.0)
        self.assertTrue(atr_sizing.capped)
        self.assertEqual(kelly_sizing.source, "kelly")
        self.assertEqual(kelly_sizing.capped_qty, 6.0)
        self.assertEqual(pretrade_sizing.source, "pre_trade")
        self.assertEqual(pretrade_sizing.capped_qty, 3.0)


if __name__ == "__main__":
    unittest.main()
