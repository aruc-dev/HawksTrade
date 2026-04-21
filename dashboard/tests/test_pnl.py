"""Unit tests for dashboard.pnl — realized/unrealized + headroom math."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dashboard import pnl


def _closed_sell(symbol, strategy, entry, exit_px, qty, pnl_pct, ts_iso, reason="tp"):
    return {
        "timestamp": ts_iso,
        "mode": "paper",
        "symbol": symbol,
        "strategy": strategy,
        "asset_class": "crypto" if "/" in symbol else "stock",
        "side": "sell",
        "qty": str(qty),
        "entry_price": str(entry),
        "exit_price": str(exit_px),
        "stop_loss": "",
        "take_profit": "",
        "pnl_pct": str(pnl_pct),
        "exit_reason": reason,
        "order_id": "abc",
        "status": "closed",
    }


class RealizedTodayTests(unittest.TestCase):
    def test_empty_rows_returns_zero(self):
        out = pnl.realized_pnl_today([], ny_date_str="2026-04-20")
        self.assertEqual(out["total_usd"], 0.0)
        self.assertEqual(out["trade_count"], 0)

    def test_filters_by_ny_date_only(self):
        rows = [
            _closed_sell("AAPL", "momentum", 100, 110, 10, 0.10,
                         "2026-04-20T14:00:00+00:00"),   # NY date 2026-04-20
            _closed_sell("MSFT", "momentum", 100, 90,  10, -0.10,
                         "2026-04-19T14:00:00+00:00"),   # NY date 2026-04-19 (excluded)
        ]
        out = pnl.realized_pnl_today(rows, ny_date_str="2026-04-20")
        self.assertEqual(out["trade_count"], 1)
        self.assertEqual(out["total_usd"], 100.0)  # (110-100)*10
        self.assertEqual(out["wins"], 1)

    def test_timezone_boundary_late_evening_counts_same_day(self):
        # 23:50 ET on 2026-04-20 is 2026-04-21 03:50 UTC → NY date still 2026-04-20
        late_et_iso = "2026-04-21T03:50:00+00:00"
        rows = [_closed_sell("AAPL", "momentum", 100, 105, 5, 0.05, late_et_iso)]
        out = pnl.realized_pnl_today(rows, ny_date_str="2026-04-20")
        self.assertEqual(out["trade_count"], 1)

    def test_includes_only_sell_side_closed_rows(self):
        rows = [
            # a 'buy' close row should not count toward realized P&L today
            {**_closed_sell("AAPL", "momentum", 100, 110, 10, 0.10, "2026-04-20T14:00:00+00:00"),
             "side": "buy"},
            _closed_sell("MSFT", "momentum", 50, 60, 10, 0.20, "2026-04-20T14:00:00+00:00"),
        ]
        out = pnl.realized_pnl_today(rows, ny_date_str="2026-04-20")
        self.assertEqual(out["trade_count"], 1)
        self.assertEqual(out["total_usd"], 100.0)

    def test_ignores_non_closed_rows(self):
        row = _closed_sell("AAPL", "momentum", 100, 110, 10, 0.10, "2026-04-20T14:00:00+00:00")
        row["status"] = "open"
        out = pnl.realized_pnl_today([row], ny_date_str="2026-04-20")
        self.assertEqual(out["trade_count"], 0)

    def test_falls_back_to_pct_when_exit_price_missing(self):
        row = _closed_sell("AAPL", "momentum", 100, 0, 10, 0.05, "2026-04-20T14:00:00+00:00")
        row["exit_price"] = ""
        out = pnl.realized_pnl_today([row], ny_date_str="2026-04-20")
        # entry * qty * pnl_pct = 100 * 10 * 0.05 = 50
        self.assertEqual(out["total_usd"], 50.0)


class RealizedWindowTests(unittest.TestCase):
    def test_empty_rows_returns_zero(self):
        now = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
        out = pnl.realized_pnl_window([], lookback_days=7, now_utc=now)
        self.assertEqual(out["total_usd"], 0.0)
        self.assertEqual(out["trade_count"], 0)
        self.assertEqual(out["window_days"], 7)

    def test_includes_only_rows_within_rolling_window(self):
        now = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
        rows = [
            _closed_sell("AAPL", "momentum", 100, 110, 10, 0.10,
                         "2026-04-19T14:00:00+00:00"),
            _closed_sell("MSFT", "momentum", 100, 90, 10, -0.10,
                         "2026-04-12T11:59:59+00:00"),
        ]
        out = pnl.realized_pnl_window(rows, lookback_days=7, now_utc=now)
        self.assertEqual(out["trade_count"], 1)
        self.assertEqual(out["total_usd"], 100.0)
        self.assertEqual(out["wins"], 1)

    def test_counts_boundary_rows_inside_window(self):
        now = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
        rows = [
            _closed_sell("AAPL", "momentum", 100, 110, 10, 0.10,
                         "2026-04-13T12:00:00+00:00"),
        ]
        out = pnl.realized_pnl_window(rows, lookback_days=7, now_utc=now)
        self.assertEqual(out["trade_count"], 1)
        self.assertEqual(out["total_usd"], 100.0)


class UnrealizedSummaryTests(unittest.TestCase):
    def test_splits_crypto_and_stock(self):
        positions = [
            {"symbol": "BTC/USD", "asset_class": "crypto", "unrealized_pl": "100"},
            {"symbol": "AAPL", "asset_class": "us_equity", "unrealized_pl": "-25"},
            {"symbol": "ETH/USD", "asset_class": "crypto", "unrealized_pl": "50"},
        ]
        out = pnl.unrealized_pnl_summary(positions)
        self.assertEqual(out["total_usd"], 125.0)
        self.assertEqual(out["crypto_usd"], 150.0)
        self.assertEqual(out["stock_usd"], -25.0)
        self.assertEqual(out["crypto_count"], 2)
        self.assertEqual(out["stock_count"], 1)

    def test_empty_positions(self):
        out = pnl.unrealized_pnl_summary([])
        self.assertEqual(out["total_usd"], 0.0)
        self.assertEqual(out["position_count"], 0)

    def test_infers_crypto_from_slash_if_asset_class_missing(self):
        out = pnl.unrealized_pnl_summary([{"symbol": "DOGE/USD", "unrealized_pl": "10"}])
        self.assertEqual(out["crypto_count"], 1)
        self.assertEqual(out["stock_count"], 0)


class DailyLossHeadroomTests(unittest.TestCase):
    def test_no_baseline_returns_unknown(self):
        out = pnl.daily_loss_headroom(None, 100000, 0.05)
        self.assertEqual(out["status"], "unknown")

    def test_green_day(self):
        out = pnl.daily_loss_headroom(
            {"portfolio_value": 100000, "date": "2026-04-20"},
            102000,
            0.05,
        )
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["delta_usd"], 2000.0)
        self.assertEqual(out["remaining_usd"], 5000.0)

    def test_warn_status_at_half_of_limit(self):
        # 3% loss with 5% limit → loss_pct 0.03, half the limit is 0.025 → warn
        out = pnl.daily_loss_headroom(
            {"portfolio_value": 100000, "date": "2026-04-20"},
            97000,
            0.05,
        )
        self.assertEqual(out["status"], "warn")

    def test_critical_status_at_80pct(self):
        # 4.5% loss with 5% limit → 90% of limit → critical
        out = pnl.daily_loss_headroom(
            {"portfolio_value": 100000, "date": "2026-04-20"},
            95500,
            0.05,
        )
        self.assertEqual(out["status"], "critical")

    def test_tripped_when_loss_meets_limit(self):
        out = pnl.daily_loss_headroom(
            {"portfolio_value": 100000, "date": "2026-04-20"},
            94999,  # 5.001% loss
            0.05,
        )
        self.assertEqual(out["status"], "tripped")


class StrategySummaryTests(unittest.TestCase):
    def test_only_counts_sells_within_window(self):
        now = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
        rows = [
            _closed_sell("AAPL", "momentum", 100, 110, 10, 0.10,
                         (now - timedelta(days=5)).isoformat()),
            _closed_sell("MSFT", "momentum", 100, 90, 10, -0.10,
                         (now - timedelta(days=1)).isoformat()),
            _closed_sell("OLD", "momentum", 100, 110, 10, 0.10,
                         (now - timedelta(days=60)).isoformat()),  # outside window
        ]
        out = pnl.strategy_summary(rows, lookback_days=30, now_utc=now)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["strategy"], "momentum")
        self.assertEqual(out[0]["count"], 2)
        self.assertEqual(out[0]["wins"], 1)
        self.assertEqual(out[0]["losses"], 1)
        self.assertEqual(out[0]["win_rate"], 0.5)

    def test_unknown_strategy_bucketed(self):
        now = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
        row = _closed_sell("AAPL", "", 100, 110, 10, 0.10, now.isoformat())
        out = pnl.strategy_summary([row], lookback_days=30, now_utc=now)
        self.assertEqual(out[0]["strategy"], "unknown")


if __name__ == "__main__":
    unittest.main()
