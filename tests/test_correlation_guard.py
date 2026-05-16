import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core import correlation_guard


def _cfg(enabled=True, fail_closed=True):
    return {
        "trading": {
            "crypto_correlation_guard": {
                "enabled": enabled,
                "max_correlation": 0.85,
                "lookback_days": 5,
                "fail_closed": fail_closed,
            },
        },
    }


def _bars(closes):
    return [SimpleNamespace(close=value) for value in closes]


class CorrelationGuardTests(unittest.TestCase):
    def test_allows_when_guard_disabled(self):
        decision = correlation_guard.evaluate_crypto_correlation(
            "SOL/USD",
            ["BTC/USD"],
            cfg=_cfg(enabled=False),
            bars_data={},
        )

        self.assertTrue(decision.allowed)

    def test_allows_when_no_existing_or_planned_crypto_symbols(self):
        decision = correlation_guard.evaluate_crypto_correlation(
            "SOL/USD",
            [],
            cfg=_cfg(),
            bars_data={},
        )

        self.assertTrue(decision.allowed)

    def test_blocks_high_positive_correlation(self):
        data = {
            "SOL/USD": _bars([100, 110, 105, 120, 115, 130]),
            "BTC/USD": _bars([50, 55, 52.5, 60, 57.5, 65]),
        }

        decision = correlation_guard.evaluate_crypto_correlation(
            "SOL/USD",
            ["BTCUSD"],
            cfg=_cfg(),
            bars_data=data,
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.context["planned_symbol"], "BTC/USD")
        self.assertGreaterEqual(decision.context["correlation"], 0.85)

    def test_matches_equivalent_bar_symbol_keys(self):
        data = {
            "SOLUSD": _bars([100, 110, 105, 120, 115, 130]),
            "BTCUSD": _bars([50, 55, 52.5, 60, 57.5, 65]),
        }

        decision = correlation_guard.evaluate_crypto_correlation(
            "SOL/USD",
            ["BTC/USD"],
            cfg=_cfg(),
            bars_data=data,
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.context["planned_symbol"], "BTC/USD")

    def test_allows_low_correlation(self):
        data = {
            "SOL/USD": _bars([100, 110, 105, 120, 115, 130]),
            "BTC/USD": _bars([50, 45, 47, 42, 44, 40]),
        }

        decision = correlation_guard.evaluate_crypto_correlation(
            "SOL/USD",
            ["BTC/USD"],
            cfg=_cfg(),
            bars_data=data,
        )

        self.assertTrue(decision.allowed)

    def test_fetch_failure_blocks_when_fail_closed(self):
        with patch.object(correlation_guard.ac, "get_crypto_bars", side_effect=RuntimeError("timeout")):
            decision = correlation_guard.evaluate_crypto_correlation(
                "SOL/USD",
                ["BTC/USD"],
                cfg=_cfg(fail_closed=True),
            )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "crypto_correlation_data_unavailable")


if __name__ == "__main__":
    unittest.main()
