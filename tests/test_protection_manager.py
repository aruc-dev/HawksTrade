import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.protection_manager import ProtectionConfig, ProtectionManager, ProtectionLock
from scripts import check_health_linux as health


class ProtectionManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.lock_file = Path(self.tmpdir.name) / "protection_locks.json"
        self.now = datetime(2026, 5, 15, 14, 30, tzinfo=timezone.utc)

    def _manager(self, **kwargs):
        config = ProtectionConfig(enabled=True, **kwargs)
        return ProtectionManager(config, lock_file=self.lock_file)

    def _sell_row(self, symbol="AAPL", strategy="momentum", pnl_pct=-0.01, days_ago=0, reason="exit"):
        ts = self.now - timedelta(days=days_ago)
        return {
            "timestamp": ts.isoformat(),
            "side": "sell",
            "status": "closed",
            "symbol": symbol,
            "strategy": strategy,
            "pnl_pct": pnl_pct,
            "exit_reason": reason,
        }

    def test_disabled_manager_allows_entries_and_has_no_locks(self):
        manager = ProtectionManager(ProtectionConfig(enabled=False), lock_file=self.lock_file)

        decision = manager.evaluate_entry("AAPL", "momentum", now=self.now)

        self.assertTrue(decision.allowed)
        self.assertEqual(manager.active_locks(now=self.now), [])

    def test_symbol_cooldown_blocks_then_expires(self):
        manager = self._manager(symbol_cooldown_days=1, symbol_stoploss_cooldown_days=0)
        manager.refresh_from_rows([self._sell_row(reason="profit exit")], now=self.now)

        blocked = manager.evaluate_entry("AAPL", "momentum", now=self.now)
        expired = manager.evaluate_entry("AAPL", "momentum", now=self.now + timedelta(days=2))

        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.lock.lock_type, "symbol_cooldown_after_exit")
        self.assertTrue(expired.allowed)

    def test_symbol_stoploss_guard_locks_symbol(self):
        manager = self._manager(symbol_cooldown_days=0, symbol_stoploss_cooldown_days=3)
        manager.refresh_from_rows([
            self._sell_row(symbol="MSFT", pnl_pct=-0.04, reason="Stop-loss hit"),
        ], now=self.now)

        decision = manager.evaluate_entry("MSFT", "momentum", now=self.now)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.lock.lock_type, "symbol_stoploss_guard")
        self.assertTrue(manager.evaluate_entry("AAPL", "momentum", now=self.now).allowed)

    def test_strategy_stoploss_guard_locks_strategy(self):
        manager = self._manager(
            symbol_cooldown_days=0,
            symbol_stoploss_cooldown_days=0,
            strategy_stoploss_threshold=2,
        )
        rows = [
            self._sell_row(symbol="AAPL", strategy="momentum", reason="Stop-loss hit"),
            self._sell_row(symbol="MSFT", strategy="momentum", reason="max-loss exit"),
        ]
        manager.refresh_from_rows(rows, now=self.now)

        decision = manager.evaluate_entry("GOOGL", "momentum", now=self.now)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.lock.lock_type, "strategy_stoploss_guard")
        self.assertTrue(manager.evaluate_entry("GOOGL", "range_breakout", now=self.now).allowed)

    def test_low_profit_strategy_lock_uses_recent_average(self):
        manager = self._manager(
            symbol_cooldown_days=0,
            symbol_stoploss_cooldown_days=0,
            strategy_stoploss_threshold=99,
            low_profit_min_trades=3,
            low_profit_threshold_pct=0.0,
        )
        rows = [
            self._sell_row(symbol="AAPL", strategy="momentum", pnl_pct=-0.01, reason="flat exit"),
            self._sell_row(symbol="MSFT", strategy="momentum", pnl_pct=0.0, reason="flat exit"),
            self._sell_row(symbol="GOOGL", strategy="momentum", pnl_pct=0.005, reason="flat exit"),
        ]
        manager.refresh_from_rows(rows, now=self.now)

        decision = manager.evaluate_entry("NVDA", "momentum", now=self.now)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.lock.lock_type, "low_profit_strategy_lock")

    def test_rolling_drawdown_lock_blocks_all_entries(self):
        manager = self._manager(
            symbol_cooldown_days=0,
            symbol_stoploss_cooldown_days=0,
            strategy_stoploss_threshold=99,
            low_profit_min_trades=99,
            max_drawdown_pct=0.03,
        )
        rows = [
            self._sell_row(symbol="AAPL", strategy="momentum", pnl_pct=-0.02),
            self._sell_row(symbol="MSFT", strategy="gap_up", pnl_pct=-0.03),
        ]
        manager.refresh_from_rows(rows, now=self.now)

        decision = manager.evaluate_entry("NVDA", "range_breakout", now=self.now)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.lock.lock_type, "rolling_max_drawdown_lock")

    def test_rolling_drawdown_uses_portfolio_impact_when_available(self):
        manager = self._manager(
            symbol_cooldown_days=0,
            symbol_stoploss_cooldown_days=0,
            strategy_stoploss_threshold=99,
            low_profit_min_trades=99,
            max_drawdown_pct=0.03,
        )
        rows = [
            {**self._sell_row(symbol="AAPL", strategy="momentum", pnl_pct=-0.04), "portfolio_pnl_pct": -0.004},
            {**self._sell_row(symbol="MSFT", strategy="gap_up", pnl_pct=-0.04), "portfolio_pnl_pct": -0.004},
        ]
        manager.refresh_from_rows(rows, now=self.now)

        decision = manager.evaluate_entry("NVDA", "range_breakout", now=self.now)

        self.assertTrue(decision.allowed)

    def test_rolling_drawdown_falls_back_to_trade_return_without_portfolio_impact(self):
        manager = self._manager(
            symbol_cooldown_days=0,
            symbol_stoploss_cooldown_days=0,
            strategy_stoploss_threshold=99,
            low_profit_min_trades=99,
            max_drawdown_pct=0.03,
        )
        rows = [
            self._sell_row(symbol="AAPL", strategy="momentum", pnl_pct=-0.02),
            self._sell_row(symbol="MSFT", strategy="gap_up", pnl_pct=-0.03),
        ]
        locks = manager.refresh_from_rows(rows, now=self.now)

        drawdown_lock = next(lock for lock in locks if lock.lock_type == "rolling_max_drawdown_lock")
        self.assertEqual(drawdown_lock.metadata["return_sources"], ["pnl_pct"])

    def test_existing_active_lock_is_preserved_across_refresh(self):
        future_lock = ProtectionLock(
            lock_type="manual_test_lock",
            scope="symbol",
            key="AAPL",
            reason="manual test",
            trigger="test",
            created_at=self.now,
            expires_at=self.now + timedelta(days=1),
        )
        manager = self._manager(symbol_cooldown_days=0, symbol_stoploss_cooldown_days=0)
        manager._write_locks([future_lock])

        locks = manager.refresh_from_rows([], now=self.now)

        self.assertEqual(len(locks), 1)
        self.assertEqual(locks[0].lock_type, "manual_test_lock")

    def test_malformed_lock_file_raises_instead_of_failing_open(self):
        manager = self._manager()
        self.lock_file.write_text("{not valid json", encoding="utf-8")

        with self.assertRaises(ValueError):
            manager.active_locks(now=self.now)

    def test_negative_low_profit_threshold_config_is_preserved(self):
        manager = ProtectionManager.from_config(
            {"protections": {"enabled": True, "low_profit_threshold_pct": -0.01}},
            lock_file=self.lock_file,
        )

        self.assertEqual(manager.config.low_profit_threshold_pct, -0.01)

    def test_health_report_dict_includes_active_protection_locks(self):
        lock = {
            "lock_type": "symbol_cooldown_after_exit",
            "scope": "symbol",
            "key": "AAPL",
            "reason": "cooldown",
            "expires_at": self.now.isoformat(),
        }
        report = health.HealthReport(
            generated_at=self.now.replace(tzinfo=None),
            lookback_hours=4.0,
            cron_template="custom",
            cron_file=Path("/tmp/health.cron"),
            local_timezone="UTC",
            overall_status="green",
            alpaca=health.AlpacaState(True, None, None),
            job_health=[],
            trade_summary={},
            log_errors=[],
            log_warnings=[],
            price_failures=[],
            html_output=Path("/tmp/health.html"),
            active_protection_locks=[lock],
        )

        payload = health.health_report_to_dict(report)
        terminal = health.format_terminal_report(report)
        rendered_html = health.render_html_report(report)

        self.assertEqual(payload["active_protection_locks"], [lock])
        self.assertIn("PROTECTION LOCKS", terminal)
        self.assertIn("AAPL", terminal)
        self.assertIn("Protection Locks", rendered_html)
        self.assertIn("symbol_cooldown_after_exit", rendered_html)
        self.assertIn("AAPL", rendered_html)


if __name__ == "__main__":
    unittest.main()
