"""
Locked out-of-sample data controls.

Research backtests must not consume the current OOS lockup window unless the
caller is running the explicit validation workflow. These helpers are kept
small and side-effect free except for the validation/rollover functions so they
can be used by tests, hooks, and backtest code without broker dependencies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_LOCKUP_FILE = BASE_DIR / "data" / "oos_lockup.json"


@dataclass(frozen=True)
class OOSLockup:
    start_date: date
    end_date: date
    created_at: str = ""
    last_validation_at: str | None = None
    last_validation_outcome: str | None = None
    unlock_token: str | None = None
    unlock_token_used_at: str | None = None

    @property
    def days(self) -> int:
        return max((self.end_date - self.start_date).days + 1, 0)

    def overlaps(self, start: date | datetime, end: date | datetime) -> bool:
        start_date = _as_utc_date(start)
        end_date = _as_utc_date(end)
        return start_date <= self.end_date and end_date >= self.start_date


def _parse_date(value: str) -> date:
    return date.fromisoformat(str(value)[:10])


def _as_utc_datetime(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


def _as_utc_date(value: date | datetime) -> date:
    return _as_utc_datetime(value).date()


def load_lockup_metadata(path: Path | None = None) -> dict[str, Any]:
    lockup_path = Path(path or DEFAULT_LOCKUP_FILE)
    if not lockup_path.exists():
        return {}
    with lockup_path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _write_lockup_metadata(metadata: dict[str, Any], path: Path | None = None) -> None:
    lockup_path = Path(path or DEFAULT_LOCKUP_FILE)
    lockup_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = lockup_path.with_suffix(lockup_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(lockup_path)


def current_lockup(path: Path | None = None) -> OOSLockup | None:
    metadata = load_lockup_metadata(path)
    raw = metadata.get("current_lockup") or {}
    if not raw:
        return None
    return OOSLockup(
        start_date=_parse_date(raw["start_date"]),
        end_date=_parse_date(raw["end_date"]),
        created_at=str(raw.get("created_at") or ""),
        last_validation_at=raw.get("last_validation_at"),
        last_validation_outcome=raw.get("last_validation_outcome"),
        unlock_token=raw.get("unlock_token"),
        unlock_token_used_at=raw.get("unlock_token_used_at"),
    )


def validate_oos_unlock_token(
    token: str | None,
    *,
    consume: bool = False,
    path: Path | None = None,
    now: datetime | None = None,
) -> bool:
    """Validate and optionally consume the current lockup's one-shot token."""
    metadata = load_lockup_metadata(path)
    raw = metadata.get("current_lockup") or {}
    expected = str(raw.get("unlock_token") or "")
    if not expected or token != expected:
        return False
    if raw.get("unlock_token_used_at"):
        return False
    if consume:
        timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        raw["unlock_token_used_at"] = timestamp
        metadata["current_lockup"] = raw
        _write_lockup_metadata(metadata, path)
    return True


def clamp_backtest_window(
    *,
    start_dt: datetime,
    end_dt: datetime,
    allow_oos: bool = False,
    oos_unlock_token: str | None = None,
    path: Path | None = None,
) -> tuple[datetime, datetime, str | None]:
    """Return a backtest window that excludes current locked dates unless allowed."""
    lockup = current_lockup(path)
    token_allowed = bool(oos_unlock_token and validate_oos_unlock_token(oos_unlock_token, path=path))
    if allow_oos or token_allowed or lockup is None or not lockup.overlaps(start_dt, end_dt):
        return start_dt, end_dt, None

    clipped_end = min(end_dt, _as_utc_datetime(lockup.start_date) - timedelta(days=1))
    if clipped_end < start_dt:
        raise ValueError(
            "Backtest window falls entirely inside the locked OOS range "
            f"{lockup.start_date.isoformat()}..{lockup.end_date.isoformat()}; "
            "use --oos-validation for the one-shot validation workflow."
        )
    note = (
        "OOS lockup enforced: backtest end clipped from "
        f"{_as_utc_date(end_dt).isoformat()} to {clipped_end.date().isoformat()} "
        f"to exclude {lockup.start_date.isoformat()}..{lockup.end_date.isoformat()}."
    )
    return start_dt, clipped_end, note


def oos_validation_window(path: Path | None = None) -> tuple[datetime, datetime, int]:
    lockup = current_lockup(path)
    if lockup is None:
        raise ValueError("No current OOS lockup is configured.")
    return _as_utc_datetime(lockup.start_date), _as_utc_datetime(lockup.end_date), lockup.days


def filter_locked_bars(
    df: pd.DataFrame,
    *,
    allow_oos: bool = False,
    oos_unlock_token: str | None = None,
    path: Path | None = None,
) -> pd.DataFrame:
    """Drop rows inside the current lockup unless explicit validation access is active."""
    if allow_oos or df is None or df.empty:
        return df
    if oos_unlock_token and validate_oos_unlock_token(oos_unlock_token, path=path):
        return df
    lockup = current_lockup(path)
    if lockup is None:
        return df
    index = pd.DatetimeIndex(df.index)
    if index.tz is None:
        index_dates = index.date
    else:
        index_dates = index.tz_convert("UTC").date
    mask = [
        not (lockup.start_date <= idx_date <= lockup.end_date)
        for idx_date in index_dates
    ]
    return df.loc[mask]


def record_oos_validation(
    *,
    outcome: str,
    report_path: str | None = None,
    path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Mark the active lockup as validated and roll forward when possible."""
    metadata = load_lockup_metadata(path)
    raw = metadata.get("current_lockup")
    if not raw:
        raise ValueError("No current OOS lockup is configured.")
    if raw.get("last_validation_at"):
        raise ValueError("Current OOS lockup has already been validated.")

    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    completed = dict(raw)
    completed["last_validation_at"] = timestamp.isoformat()
    completed["last_validation_outcome"] = str(outcome)
    if report_path:
        completed["last_validation_report"] = str(report_path)

    history = list(metadata.get("history") or [])
    history.append(completed)
    metadata["history"] = history

    latest_available = timestamp.date() - timedelta(days=2)
    proposed_end = latest_available
    proposed_start = proposed_end - timedelta(days=89)
    current_end = _parse_date(raw["end_date"])
    if proposed_start > current_end:
        metadata["current_lockup"] = {
            "start_date": proposed_start.isoformat(),
            "end_date": proposed_end.isoformat(),
            "created_at": timestamp.isoformat(),
            "last_validation_at": None,
            "last_validation_outcome": None,
            "unlock_token": f"oos-{proposed_start.isoformat()}-{proposed_end.isoformat()}",
            "unlock_token_used_at": None,
        }
    else:
        metadata["current_lockup"] = completed

    _write_lockup_metadata(metadata, path)
    return metadata


def report_mentions_locked_date(
    text: str,
    *,
    path: Path | None = None,
) -> bool:
    lockup = current_lockup(path)
    if lockup is None:
        return False
    current = lockup.start_date
    while current <= lockup.end_date:
        if current.isoformat() in text or current.strftime("%m/%d/%Y") in text:
            return True
        current += timedelta(days=1)
    return False
