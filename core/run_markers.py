"""Structured start/end markers for scheduled HawksTrade entrypoints."""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    """Return a naive UTC timestamp for log markers."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_run_id(script: str) -> str:
    """Create a unique run id that is easy to correlate in logs."""
    return f"{script}-{utc_now().strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def _normalize_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        text = f"{value:.3f}"
        return text.rstrip("0").rstrip(".")
    return str(value).replace(" ", "_")


def _format_marker(event: str, fields: dict[str, Any]) -> str:
    parts = [event]
    for key, value in fields.items():
        normalized = _normalize_value(value)
        if normalized:
            parts.append(f"{key}={normalized}")
    return " ".join(parts)


@dataclass
class RunScope(AbstractContextManager["RunScope"]):
    """Context manager that emits non-functional start/end log markers."""

    logger: logging.Logger
    script: str
    fields: dict[str, Any] = field(default_factory=dict)
    run_id: str = ""
    started_at: datetime = field(default_factory=utc_now)
    status: str = "ok"
    end_fields: dict[str, Any] = field(default_factory=dict)
    previous_env_run_id: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if not self.run_id:
            self.run_id = new_run_id(self.script)

    def __enter__(self) -> "RunScope":
        self.previous_env_run_id = os.environ.get("HAWKSTRADE_RUN_ID")
        os.environ["HAWKSTRADE_RUN_ID"] = self.run_id
        self.logger.info(
            _format_marker(
                "RUN_START",
                {
                    "script": self.script,
                    "run_id": self.run_id,
                    **self.fields,
                },
            )
        )
        return self

    def mark_ok(self, **fields: Any) -> None:
        self.status = "ok"
        self.end_fields.update(fields)

    def mark_error(self, **fields: Any) -> None:
        self.status = "error"
        self.end_fields.update(fields)

    def mark_status(self, status: str, **fields: Any) -> None:
        self.status = status
        self.end_fields.update(fields)

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            self.status = "error"
            self.end_fields.setdefault("error_type", exc_type.__name__)
        duration_s = max(0.0, (utc_now() - self.started_at).total_seconds())
        self.logger.info(
            _format_marker(
                "RUN_END",
                {
                    "script": self.script,
                    "run_id": self.run_id,
                    "status": self.status,
                    "duration_s": f"{duration_s:.3f}",
                    **self.fields,
                    **self.end_fields,
                },
            )
        )
        if self.previous_env_run_id is None:
            os.environ.pop("HAWKSTRADE_RUN_ID", None)
        else:
            os.environ["HAWKSTRADE_RUN_ID"] = self.previous_env_run_id
        return False


def run_scope(logger: logging.Logger, script: str, **fields: Any) -> RunScope:
    """Create a run scope for a scheduled entrypoint."""
    return RunScope(logger=logger, script=script, fields=fields)
