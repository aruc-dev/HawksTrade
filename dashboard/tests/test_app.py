"""End-to-end tests for the FastAPI app via TestClient, in local auth mode."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch


def _skip_if_fastapi_missing():
    try:
        import fastapi  # noqa: F401
        from fastapi.testclient import TestClient  # noqa: F401
    except ImportError:
        raise unittest.SkipTest("fastapi not installed; run pip install -r requirements-dashboard.txt")


class AppEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _skip_if_fastapi_missing()
        os.environ["DASHBOARD_AUTH_MODE"] = "local"
        from dashboard.app import create_app
        from fastapi.testclient import TestClient

        cls.client = TestClient(create_app())

    def test_healthz_always_returns_json(self):
        # Even when Alpaca is unreachable, /healthz must respond with JSON.
        from dashboard import app as app_module

        with patch.object(app_module, "alpaca_reachable", return_value=False):
            r = self.client.get("/healthz")
        self.assertIn(r.status_code, (200, 503))
        self.assertIn("status", r.json())

    def test_state_endpoint_returns_expected_shape(self):
        from dashboard import app as app_module
        fake_positions = [
            {"symbol": "AAPL", "asset_class": "us_equity", "unrealized_pl": 50, "qty": 10,
             "avg_entry_price": 100, "current_price": 105, "unrealized_plpc": 0.05,
             "market_value": 1050, "cost_basis": 1000, "side": "long"}
        ]
        fake_rows = [
            {"timestamp": "2026-04-18T12:00:00+00:00", "symbol": "AAPL",
             "strategy": "momentum", "side": "buy", "status": "open"}
        ]
        fake_account = {"portfolio_value": 100000.0, "cash": 50000.0, "buying_power": 50000.0}
        fake_snapshot = {
            "generated_at": "2026-04-20T12:00:00+00:00",
            "lookback_hours": 4,
            "cron_template": "utc",
            "overall_status": "green",
            "alpaca": {"connected": True, "portfolio_value": 100000.0, "open_position_count": 1},
            "job_health": [{"label": "Crypto scan", "status": "green", "missed_runs": 0, "last_run_at": "2026-04-20T12:00:00+00:00"}],
            "log_errors": [],
            "log_warnings": [],
        }
        with patch.object(app_module, "get_positions_as_dicts", return_value=fake_positions), \
                patch.object(app_module, "read_trades", return_value=fake_rows), \
                patch.object(app_module, "get_account_summary", return_value=fake_account), \
                patch.object(app_module, "alpaca_reachable", return_value=True), \
                patch.object(app_module, "read_latest_health_snapshot",
                             return_value={"ok": True, "path": "/tmp/health.json", "data": fake_snapshot, "error": None}), \
                patch.object(app_module, "read_recent_log_issues", return_value=[]):
            r = self.client.get("/api/state")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("account", body)
        self.assertIn("positions", body)
        self.assertIn("realized_today", body)
        self.assertIn("daily_loss_headroom", body)
        self.assertIn("strategies", body)
        self.assertIn("health", body)
        self.assertEqual(len(body["positions"]), 1)
        self.assertEqual(body["positions"][0]["strategy"], "momentum")
        self.assertIn("hold_days", body["positions"][0])
        self.assertEqual(body["health"]["status"], "green")

    def test_no_mutation_endpoints_exist(self):
        # The app must not expose anything that could place/cancel orders.
        from dashboard.app import create_app

        app = create_app()
        forbidden_substrings = ["order", "buy", "sell", "cancel", "close_position"]
        for route in app.routes:
            path = getattr(route, "path", "")
            for token in forbidden_substrings:
                # Allow /api/trades/recent (reading) and the "strategies" path; we're
                # searching for mutation-ish substrings specifically as path tokens.
                if token in path.lower() and path not in ("/api/trades/recent",):
                    self.fail(f"Suspicious route path may indicate mutation: {path}")

    def test_recent_trades_endpoint_respects_limit(self):
        r = self.client.get("/api/trades/recent?limit=5")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("trades", body)
        self.assertLessEqual(len(body["trades"]), 5)

    def test_index_html_served(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])
        self.assertIn("HawksTrade", r.text)

    def test_docs_endpoints_disabled(self):
        # OpenAPI / Swagger endpoints should not be exposed.
        for path in ("/docs", "/redoc", "/openapi.json"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 404, f"{path} should be disabled")

    def test_cloudflare_mode_rejects_protected_routes_without_jwt(self):
        from dashboard.app import create_app
        from fastapi.testclient import TestClient

        env = {
            "DASHBOARD_AUTH_MODE": "cloudflare",
            "CF_ACCESS_TEAM_DOMAIN": "test.cloudflareaccess.com",
            "CF_ACCESS_AUD": "aud",
            "DASHBOARD_ALLOWED_EMAILS": "arun@example.com",
        }
        with patch.dict(os.environ, env, clear=False):
            client = TestClient(create_app())

            for path in ("/", "/api/state", "/api/health", "/api/positions",
                         "/api/pnl/today", "/api/trades/recent",
                         "/api/strategies/summary"):
                with self.subTest(path=path):
                    r = client.get(path)
                    self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()
