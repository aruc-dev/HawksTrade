import unittest

from core.slippage_model import estimate_slippage_bps, realised_slippage_bps


class SlippageModelTests(unittest.TestCase):
    def test_slippage_scales_with_square_root_order_size(self):
        small = estimate_slippage_bps(
            symbol="AAPL",
            asset_class="stock",
            side="sell",
            order_size_usd=10_000,
            adv_usd=100_000_000,
            realised_volatility_bps=100,
            cfg={"min_stock_bps": 0, "max_bps": 10_000},
        )
        large = estimate_slippage_bps(
            symbol="AAPL",
            asset_class="stock",
            side="sell",
            order_size_usd=40_000,
            adv_usd=100_000_000,
            realised_volatility_bps=100,
            cfg={"min_stock_bps": 0, "max_bps": 10_000},
        )

        self.assertAlmostEqual(large / small, 2.0, places=5)

    def test_open_window_multiplier_applies_at_935_not_1030(self):
        cfg = {"min_stock_bps": 0, "max_bps": 10_000, "open_multiplier": 1.5, "buy_asymmetry": 1.0}
        open_slip = estimate_slippage_bps(
            symbol="AAPL",
            asset_class="stock",
            side="sell",
            order_size_usd=10_000,
            adv_usd=100_000_000,
            realised_volatility_bps=100,
            time_of_day="09:35",
            cfg=cfg,
        )
        normal_slip = estimate_slippage_bps(
            symbol="AAPL",
            asset_class="stock",
            side="sell",
            order_size_usd=10_000,
            adv_usd=100_000_000,
            realised_volatility_bps=100,
            time_of_day="10:30",
            cfg=cfg,
        )

        self.assertAlmostEqual(open_slip / normal_slip, 1.5, places=5)

    def test_crypto_defaults_higher_than_stock(self):
        stock = estimate_slippage_bps(
            symbol="AAPL",
            asset_class="stock",
            side="sell",
            order_size_usd=10_000,
            adv_usd=100_000_000,
            realised_volatility_bps=100,
            cfg={"min_stock_bps": 0, "max_bps": 10_000},
        )
        crypto = estimate_slippage_bps(
            symbol="DOGE/USD",
            asset_class="crypto",
            side="sell",
            order_size_usd=10_000,
            adv_usd=100_000_000,
            realised_volatility_bps=100,
            cfg={"min_crypto_bps": 0, "max_bps": 10_000},
        )

        self.assertGreater(crypto, stock)

    def test_symbol_override_multiplier_takes_precedence(self):
        base = estimate_slippage_bps(
            symbol="AAPL",
            asset_class="stock",
            side="sell",
            order_size_usd=10_000,
            adv_usd=100_000_000,
            realised_volatility_bps=100,
            cfg={"min_stock_bps": 0, "max_bps": 10_000},
        )
        overridden = estimate_slippage_bps(
            symbol="AAPL",
            asset_class="stock",
            side="sell",
            order_size_usd=10_000,
            adv_usd=100_000_000,
            realised_volatility_bps=100,
            cfg={"min_stock_bps": 0, "max_bps": 10_000, "per_symbol_overrides": {"AAPL": 2.0}},
        )

        self.assertAlmostEqual(overridden, base * 2.0, places=5)

    def test_realised_slippage_is_positive_when_fill_is_adverse(self):
        self.assertAlmostEqual(
            realised_slippage_bps(side="buy", decision_price=100, fill_price=101),
            100.0,
        )
        self.assertAlmostEqual(
            realised_slippage_bps(side="sell", decision_price=100, fill_price=99),
            100.0,
        )


if __name__ == "__main__":
    unittest.main()
