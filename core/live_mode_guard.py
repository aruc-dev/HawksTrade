"""
HawksTrade - Live Mode Runtime Interlock
========================================
Fails closed when the runtime is configured for live trading without an
explicit operator acknowledgement in the process environment.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any


LIVE_ACK_ENV_VAR = "HAWKSTRADE_LIVE_ACK"
LIVE_ACK_VALUE = "I_UNDERSTAND_REAL_MONEY"


class LiveModeInterlockError(RuntimeError):
    """Raised when live mode is configured without the runtime acknowledgement."""


def is_live_mode(config: Mapping[str, Any]) -> bool:
    """Return True when the effective config requests live trading."""
    return str(config.get("mode", "paper")).strip().lower() == "live"


def live_mode_acknowledged(environ: Mapping[str, str] | None = None) -> bool:
    """Return True when the live-trading acknowledgement env var is exact."""
    env = os.environ if environ is None else environ
    return env.get(LIVE_ACK_ENV_VAR, "").strip() == LIVE_ACK_VALUE


def require_live_mode_ack(
    config: Mapping[str, Any],
    environ: Mapping[str, str] | None = None,
) -> None:
    """Raise if config is live mode and the operator acknowledgement is absent."""
    if not is_live_mode(config):
        return
    if live_mode_acknowledged(environ):
        return
    raise LiveModeInterlockError(
        "mode=live requires explicit runtime acknowledgement: "
        f"set {LIVE_ACK_ENV_VAR}={LIVE_ACK_VALUE}. "
        "This prevents accidental real-money execution from config.local.yaml "
        "or host-level configuration drift."
    )
