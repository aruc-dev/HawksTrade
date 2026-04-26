import unittest
from pathlib import Path
from unittest.mock import patch

from core.config_loader import get_config_path, BASE_DIR


class TestGetConfigPath(unittest.TestCase):
    def test_returns_local_when_present(self):
        local = BASE_DIR / "config" / "config.local.yaml"
        with patch.object(Path, "exists", lambda self: self == local):
            result = get_config_path()
        self.assertEqual(result, local)

    def test_falls_back_to_default_when_local_absent(self):
        with patch.object(Path, "exists", return_value=False):
            result = get_config_path()
        self.assertEqual(result, BASE_DIR / "config" / "config.yaml")
