"""Dashboard configuration loader.

Reads from:
- config/config.local.yaml if present, otherwise config/config.yaml
  (same local-override resolution used by the bot; read-only)
- Environment variables (for auth mode, Cloudflare Access settings)

Never reads /dev/shm/.hawkstrade.env — the dashboard runs as an isolated user
with its own read-only Alpaca key supplied via /etc/hawkstrade-dash/env.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml

from core.config_loader import get_config_path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = get_config_path()

AUTH_MODE_LOCAL = "local"
AUTH_MODE_CLOUDFLARE = "cloudflare"
VALID_AUTH_MODES = {AUTH_MODE_LOCAL, AUTH_MODE_CLOUDFLARE}


class DashboardConfig:
    """Lazily loaded dashboard configuration."""

    def __init__(self, config_path: Path = CONFIG_PATH) -> None:
        self.config_path = config_path
        self._cached: Dict[str, Any] | None = None

    def _load(self) -> Dict[str, Any]:
        if self._cached is None:
            with open(self.config_path) as f:
                self._cached = yaml.safe_load(f) or {}
        return self._cached

    @property
    def mode(self) -> str:
        return str(self._load().get("mode", "paper")).lower()

    @property
    def daily_loss_limit_pct(self) -> float:
        return float(self._load().get("trading", {}).get("daily_loss_limit_pct", 0.05))

    @property
    def max_positions(self) -> int:
        return int(self._load().get("trading", {}).get("max_positions", 10))

    @property
    def max_crypto_positions(self) -> int:
        return int(self._load().get("trading", {}).get("max_crypto_positions", 0))

    @property
    def min_crypto_positions(self) -> int:
        return int(self._load().get("trading", {}).get("min_crypto_positions", 0))

    @property
    def trade_log_path(self) -> Path:
        rel = self._load().get("reporting", {}).get("trade_log_file", "data/trades.csv")
        return BASE_DIR / rel

    @property
    def daily_baseline_path(self) -> Path:
        return BASE_DIR / "data" / "daily_loss_baseline.json"

    @property
    def logs_dir(self) -> Path:
        rel = self._load().get("reporting", {}).get("logs_dir", "logs/")
        return BASE_DIR / rel

    @property
    def health_snapshot_dir(self) -> Path:
        return BASE_DIR / "reports" / "health_snapshots"

    @property
    def check_systemd_script(self) -> Path:
        return BASE_DIR / "scripts" / "check_systemd.sh"


def get_auth_mode() -> str:
    """Return DASHBOARD_AUTH_MODE env var, defaulting to 'cloudflare'.

    Assertion: in production the env MUST be explicitly set. Accepting a default
    of 'cloudflare' (the stricter option) fails closed if the env is missing.
    """
    mode = os.environ.get("DASHBOARD_AUTH_MODE", AUTH_MODE_CLOUDFLARE).lower()
    if mode not in VALID_AUTH_MODES:
        raise ValueError(
            f"Invalid DASHBOARD_AUTH_MODE={mode!r}; must be one of {VALID_AUTH_MODES}"
        )
    return mode


def get_cf_team_domain() -> str:
    """e.g. yourname.cloudflareaccess.com — required in cloudflare mode."""
    return os.environ.get("CF_ACCESS_TEAM_DOMAIN", "")


def get_cf_audience() -> str:
    """Application Audience tag from Cloudflare — required in cloudflare mode."""
    return os.environ.get("CF_ACCESS_AUD", "")


def get_allowed_emails() -> set[str]:
    """Comma-separated env var listing allowed emails (defense-in-depth vs CF).

    Cloudflare Access already enforces the allowlist at the edge; this is a
    second line of defense in case of misconfiguration. Lowercased for
    case-insensitive comparison.
    """
    raw = os.environ.get("DASHBOARD_ALLOWED_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


# Single shared instance for convenience.
_shared = DashboardConfig()


def cfg() -> DashboardConfig:
    return _shared
