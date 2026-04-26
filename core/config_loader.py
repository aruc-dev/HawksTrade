"""
Central config loader — resolves config path with local override support.
Uses config/config.local.yaml if present, otherwise falls back to config/config.yaml.
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def get_config_path() -> Path:
    local = BASE_DIR / "config" / "config.local.yaml"
    if local.is_file():
        return local
    return BASE_DIR / "config" / "config.yaml"
