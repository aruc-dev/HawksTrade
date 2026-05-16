import unittest
from pathlib import Path
from unittest.mock import patch
import tempfile
import yaml

from core.config_loader import get_config_path, get_config, _deep_merge, BASE_DIR


class TestGetConfigPath(unittest.TestCase):
    def test_returns_local_when_present(self):
        local = BASE_DIR / "config" / "config.local.yaml"
        with patch.object(Path, "is_file", lambda self: self == local):
            result = get_config_path()
        self.assertEqual(result, local)

    def test_falls_back_to_default_when_local_absent(self):
        with patch.object(Path, "is_file", return_value=False):
            result = get_config_path()
        self.assertEqual(result, BASE_DIR / "config" / "config.yaml")

    def test_accepts_custom_base_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "config").mkdir()
            local = root / "config" / "config.local.yaml"
            local.write_text("mode: live\n")
            result = get_config_path(base_dir=root)
        self.assertEqual(result, local)

    def test_custom_base_dir_falls_back_when_no_local(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "config").mkdir()
            result = get_config_path(base_dir=root)
        self.assertEqual(result, root / "config" / "config.yaml")


class TestDeepMerge(unittest.TestCase):
    def test_shallow_override(self):
        base = {"mode": "paper", "a": 1}
        override = {"mode": "live"}
        result = _deep_merge(base, override)
        self.assertEqual(result["mode"], "live")
        self.assertEqual(result["a"], 1)

    def test_nested_merge(self):
        base = {"trading": {"stop_loss": 0.035, "max_positions": 10}}
        override = {"trading": {"stop_loss": 0.05}}
        result = _deep_merge(base, override)
        self.assertEqual(result["trading"]["stop_loss"], 0.05)
        self.assertEqual(result["trading"]["max_positions"], 10)

    def test_does_not_mutate_base(self):
        base = {"trading": {"a": 1}}
        override = {"trading": {"b": 2}}
        _deep_merge(base, override)
        self.assertNotIn("b", base["trading"])

    def test_new_keys_added(self):
        base = {"a": 1}
        override = {"b": 2}
        result = _deep_merge(base, override)
        self.assertEqual(result, {"a": 1, "b": 2})


class TestGetConfig(unittest.TestCase):
    def test_loads_base_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "config").mkdir()
            (root / "config" / "config.yaml").write_text(
                "mode: paper\ntrading:\n  stop_loss: 0.035\n"
            )
            result = get_config(base_dir=root)
        self.assertEqual(result["mode"], "paper")
        self.assertEqual(result["trading"]["stop_loss"], 0.035)

    def test_merges_local_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "config").mkdir()
            (root / "config" / "config.yaml").write_text(
                "mode: paper\ntrading:\n  stop_loss: 0.035\n  max_positions: 10\n"
            )
            (root / "config" / "config.local.yaml").write_text(
                "mode: live\ntrading:\n  stop_loss: 0.05\n"
            )
            result = get_config(base_dir=root)
        self.assertEqual(result["mode"], "live")
        self.assertEqual(result["trading"]["stop_loss"], 0.05)
        # Base key not overridden should survive
        self.assertEqual(result["trading"]["max_positions"], 10)

    def test_no_local_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "config").mkdir()
            (root / "config" / "config.yaml").write_text("mode: paper\n")
            result = get_config(base_dir=root)
        self.assertEqual(result["mode"], "paper")

    def test_empty_local_file_does_not_break(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "config").mkdir()
            (root / "config" / "config.yaml").write_text("mode: paper\n")
            (root / "config" / "config.local.yaml").write_text("")
            result = get_config(base_dir=root)
        self.assertEqual(result["mode"], "paper")

    def test_default_strategy_enablement_profile(self):
        config_path = BASE_DIR / "config" / "config.yaml"
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))

        strategies = cfg["strategies"]
        self.assertTrue(cfg["protections"]["enabled"])
        self.assertTrue(cfg["broker_stops"]["enabled"])
        self.assertFalse(cfg["broker_stops"]["submit_in_paper"])
        self.assertTrue(strategies["momentum"]["enabled"])
        self.assertFalse(strategies["rsi_reversion"]["enabled"])
        self.assertTrue(strategies["ma_crossover"]["enabled"])
        self.assertTrue(strategies["range_breakout"]["enabled"])
        self.assertTrue(strategies["gap_up"]["enabled"])
        self.assertEqual(strategies["momentum"]["top_n"], 2)
        self.assertEqual(strategies["momentum"]["min_momentum_pct"], 0.08)
        self.assertEqual(strategies["momentum"]["volume_confirmation_mode"], "pace")
        self.assertEqual(strategies["momentum"]["volume_pace_ratio"], 1.5)
        self.assertEqual(strategies["momentum"]["volume_pace_timeframe"], "1Min")
        self.assertEqual(strategies["momentum"]["session_minutes"], 390)
        self.assertEqual(strategies["momentum"]["volume_spike_ratio"], 2.0)
        self.assertEqual(strategies["momentum"]["min_breadth_coverage_pct"], 0.65)
        self.assertEqual(strategies["momentum"]["atr_multiplier"], 1.2)
        self.assertEqual(strategies["momentum"]["max_stop_loss_pct"], 0.05)
        self.assertEqual(strategies["rsi_reversion"]["oversold_threshold"], 40)
        self.assertEqual(strategies["rsi_reversion"]["vix_multiplier"], 0.95)
        self.assertEqual(strategies["rsi_reversion"]["volume_spike_ratio"], 0.7)
        self.assertEqual(strategies["rsi_reversion"]["max_entry_atr_pct"], 0.05)
        self.assertEqual(strategies["rsi_reversion"]["max_stop_loss_pct"], 0.06)
        self.assertEqual(strategies["rsi_reversion"]["max_loss_exit_pct"], 0.06)
        self.assertTrue(strategies["rsi_reversion"]["profit_trailing_enabled"])
        self.assertEqual(strategies["rsi_reversion"]["trail_activation_pct"], 0.06)
        self.assertEqual(strategies["rsi_reversion"]["trailing_stop_pct"], 0.04)
        self.assertEqual(strategies["rsi_reversion"]["recent_drawdown_lookback_days"], 5)
        self.assertEqual(strategies["rsi_reversion"]["max_recent_drawdown_pct"], 0.10)
        self.assertEqual(strategies["gap_up"]["min_gap_pct"], 0.05)
        self.assertEqual(strategies["gap_up"]["hold_days"], 2)
        self.assertEqual(strategies["gap_up"]["volume_multiplier"], 1.3)
        self.assertEqual(strategies["gap_up"]["min_breadth_pct"], 0.65)
        self.assertTrue(strategies["gap_up"]["require_prior_close_above_trend"])
        self.assertEqual(strategies["gap_up"]["max_trend_extension_pct"], 0.35)
        self.assertEqual(strategies["ma_crossover"]["fast_ema"], 6)
        self.assertEqual(strategies["ma_crossover"]["slow_ema"], 18)
        self.assertEqual(strategies["ma_crossover"]["entry_cross_lookback_days"], 1)
        self.assertEqual(strategies["ma_crossover"]["min_trend_return_pct"], -0.02)
        self.assertEqual(strategies["ma_crossover"]["min_price_above_slow_pct"], 0.005)
        self.assertEqual(strategies["ma_crossover"]["hold_days"], 16)
        self.assertEqual(strategies["ma_crossover"]["max_loss_exit_pct"], 0.02)
        self.assertEqual(strategies["ma_crossover"]["rsi_entry_max"], 75)
        self.assertEqual(strategies["ma_crossover"]["volume_spike_ratio"], 1.0)
        self.assertEqual(strategies["ma_crossover"]["max_signals"], 1)
        self.assertEqual(strategies["range_breakout"]["breakout_pct"], 0.006)
        self.assertEqual(strategies["range_breakout"]["min_breakout_extension_pct"], 0.008)
        self.assertEqual(strategies["range_breakout"]["min_close_location"], 0.70)
        self.assertEqual(strategies["range_breakout"]["volume_multiplier"], 2.5)
        self.assertEqual(strategies["range_breakout"]["min_range_ratio"], 0.45)
        self.assertEqual(strategies["range_breakout"]["rsi_entry_max"], 82)
        self.assertTrue(strategies["range_breakout"]["profit_trailing_enabled"])
        self.assertEqual(strategies["range_breakout"]["trail_activation_pct"], 0.06)
        self.assertEqual(strategies["range_breakout"]["trailing_stop_pct"], 0.04)

        validation = cfg["validation"]
        production_windows = {
            window["name"]: window
            for window in validation["production_gate"]["windows"]
        }
        self.assertEqual(
            production_windows["default_12m_costed"]["strategies"],
            ["momentum", "gap_up", "ma_crossover", "range_breakout"],
        )
        self.assertEqual(
            production_windows["default_6m_costed"]["strategies"],
            ["momentum", "gap_up", "ma_crossover", "range_breakout"],
        )
        self.assertEqual(production_windows["default_12m_costed"]["end_date"], "04/29/2026")
        self.assertEqual(production_windows["default_6m_costed"]["end_date"], "04/29/2026")
        self.assertEqual(production_windows["crypto_12m_costed"]["end_date"], "04/29/2026")
        self.assertEqual(production_windows["default_12m_costed"]["max_drawdown_pct"], 0.06)
        self.assertEqual(production_windows["default_6m_costed"]["max_drawdown_pct"], 0.04)

        gap_windows = {
            window["name"]: window
            for window in validation["gap_up_enablement"]["backtest_windows"]
        }
        self.assertEqual(gap_windows["gap_up_12m_costed"]["end_date"], "04/29/2026")
        self.assertTrue(gap_windows["gap_up_12m_costed"]["screener"])
        self.assertEqual(gap_windows["gap_up_12m_costed"]["min_profit_factor"], 1.95)
        self.assertTrue(gap_windows["gap_up_recent_30d_watch"]["screener"])
        self.assertEqual(gap_windows["gap_up_recent_30d_watch"]["min_profit_factor"], 1.0)
