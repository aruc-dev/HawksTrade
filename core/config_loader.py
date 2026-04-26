"""
Central config loader — resolves config path with local override support.

get_config() is the preferred API: it always loads config/config.yaml as the
base and deep-merges config/config.local.yaml on top when present, so a local
file only needs to contain the keys being overridden.

get_config_path() is retained for callers that need the file path directly
(e.g. the status UI, which operates outside the normal import graph).  When
config/config.local.yaml exists it returns that path; otherwise it returns
config/config.yaml.  Both functions accept an optional base_dir argument so
that code with a non-standard project root (e.g. status_ui with --project-dir)
can use the same resolution logic.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent


def get_config_path(base_dir: Optional[Path] = None) -> Path:
    """Return the effective config file path.

    Prefers <base_dir>/config/config.local.yaml when it exists as a regular
    file, otherwise falls back to <base_dir>/config/config.yaml.
    """
    root = Path(base_dir) if base_dir is not None else BASE_DIR
    local = root / "config" / "config.local.yaml"
    if local.is_file():
        return local
    return root / "config" / "config.yaml"


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Return a new dict with *override* recursively merged into *base*."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def get_config(base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Load and return the effective configuration dict.

    Always loads config/config.yaml as the base.  If
    config/config.local.yaml is present as a regular file, its keys are
    deep-merged on top, so the local file only needs to contain the keys
    being overridden — all other keys fall back to the base config.
    """
    root = Path(base_dir) if base_dir is not None else BASE_DIR
    base_path = root / "config" / "config.yaml"
    local_path = root / "config" / "config.local.yaml"

    with open(base_path) as f:
        config: Dict[str, Any] = yaml.safe_load(f) or {}

    if local_path.is_file():
        with open(local_path) as f:
            local_config: Dict[str, Any] = yaml.safe_load(f) or {}
        config = _deep_merge(config, local_config)

    return config
