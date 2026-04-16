"""
Logging helpers for runtime entry points.
"""

import logging
import os
import sys
from pathlib import Path
from typing import Mapping


def should_write_runtime_logs(
    modules: Mapping[str, object] | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return False for unit-test imports so tests do not pollute runtime logs."""
    modules = sys.modules if modules is None else modules
    environ = os.environ if environ is None else environ
    disabled = environ.get("HAWKSTRADE_DISABLE_FILE_LOGS", "").lower()
    if disabled in {"1", "true", "yes", "on"}:
        return False
    return "unittest" not in modules


def runtime_log_handlers(log_dir: Path, filename: str) -> list[logging.Handler]:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if should_write_runtime_logs():
        log_dir.mkdir(exist_ok=True)
        handlers.append(logging.FileHandler(log_dir / filename))
    return handlers
