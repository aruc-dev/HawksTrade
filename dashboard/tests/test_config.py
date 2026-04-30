import tempfile
import unittest
from pathlib import Path

from dashboard.config import DashboardConfig


class DashboardConfigTests(unittest.TestCase):
    def test_local_config_is_deep_merged_with_base_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "config.yaml").write_text(
                "\n".join([
                    "mode: paper",
                    "trading:",
                    "  daily_loss_limit_pct: 0.05",
                    "  max_positions: 10",
                    "  max_position_pct: 0.05",
                    "reporting:",
                    "  trade_log_file: data/trades.csv",
                    "  logs_dir: logs/",
                ]),
                encoding="utf-8",
            )
            (config_dir / "config.local.yaml").write_text(
                "\n".join([
                    "trading:",
                    "  max_position_pct: 0.02",
                ]),
                encoding="utf-8",
            )

            cfg = DashboardConfig(base_dir=root)

            self.assertEqual(cfg.mode, "paper")
            self.assertEqual(cfg.max_positions, 10)
            self.assertEqual(cfg.max_position_pct, 0.02)
            self.assertEqual(cfg.trade_log_path, root / "data" / "trades.csv")


if __name__ == "__main__":
    unittest.main()
