import unittest
from pathlib import Path
from unittest.mock import patch
import tempfile

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
