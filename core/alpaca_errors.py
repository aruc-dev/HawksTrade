"""Shared Alpaca retry and error classification helpers."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional, TypeVar

try:
    import requests
except Exception:  # pragma: no cover - requests is an install dependency.
    requests = None

log = logging.getLogger("alpaca_errors")

T = TypeVar("T")

CATEGORY_AUTH = "auth"
CATEGORY_BROKER_REJECTION = "broker_rejection"
CATEGORY_CONFIG = "configuration"
CATEGORY_NETWORK = "network"
CATEGORY_NOT_FOUND = "not_found"
CATEGORY_RATE_LIMIT = "rate_limit"
CATEGORY_SERVER = "server_error"
CATEGORY_TIMEOUT = "timeout"
CATEGORY_UNKNOWN = "unknown"


@dataclass(frozen=True)
class AlpacaErrorInfo:
    """Structured classification for an Alpaca/API exception."""

    category: str
    retryable: bool
    status_code: Optional[int]
    message: str
    exception_type: str


def exception_status_code(exc: Exception) -> Optional[int]:
    """Best-effort HTTP status extraction across Alpaca and requests errors."""
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
        getattr(getattr(getattr(exc, "_http_error", None), "response", None), "status_code", None),
        getattr(getattr(getattr(exc, "http_error", None), "response", None), "status_code", None),
    ):
        if candidate is None:
            continue
        try:
            return int(candidate)
        except (TypeError, ValueError):
            continue
    return None


def exception_text(exc: Exception) -> str:
    pieces = [str(exc)]
    for attr in ("message", "_error"):
        try:
            value = getattr(exc, attr, None)
        except Exception:
            continue
        if value:
            pieces.append(str(value))
    return " ".join(pieces).strip()


def is_not_found_error(exc: Exception) -> bool:
    status_code = exception_status_code(exc)
    if status_code == 404:
        return True
    if status_code is not None:
        return False

    text = exception_text(exc).lower()
    return (
        "position does not exist" in text
        or "position not found" in text
        or ("not found" in text and "position" in text)
    )


def _is_timeout_error(exc: Exception) -> bool:
    timeout_types = (TimeoutError,)
    if requests is not None:
        timeout_types = timeout_types + (requests.exceptions.Timeout,)
    return isinstance(exc, timeout_types)


def _is_network_error(exc: Exception) -> bool:
    network_types = (ConnectionError,)
    if requests is not None:
        network_types = network_types + (requests.exceptions.ConnectionError,)
    return isinstance(exc, network_types)


def classify_alpaca_error(exc: Exception) -> AlpacaErrorInfo:
    """Classify an exception into operational categories used by entrypoints."""
    status_code = exception_status_code(exc)
    message = exception_text(exc)

    if _is_timeout_error(exc):
        return AlpacaErrorInfo(CATEGORY_TIMEOUT, True, status_code, message, type(exc).__name__)

    if _is_network_error(exc):
        return AlpacaErrorInfo(CATEGORY_NETWORK, True, status_code, message, type(exc).__name__)

    if isinstance(exc, (EnvironmentError, PermissionError)):
        return AlpacaErrorInfo(CATEGORY_CONFIG, False, status_code, message, type(exc).__name__)

    if status_code in {401, 403}:
        return AlpacaErrorInfo(CATEGORY_AUTH, False, status_code, message, type(exc).__name__)

    if is_not_found_error(exc):
        return AlpacaErrorInfo(CATEGORY_NOT_FOUND, False, status_code, message, type(exc).__name__)

    if status_code == 429:
        return AlpacaErrorInfo(CATEGORY_RATE_LIMIT, True, status_code, message, type(exc).__name__)

    if status_code == 408:
        return AlpacaErrorInfo(CATEGORY_TIMEOUT, True, status_code, message, type(exc).__name__)

    if status_code in {409, 425} or (status_code is not None and status_code >= 500):
        return AlpacaErrorInfo(CATEGORY_SERVER, True, status_code, message, type(exc).__name__)

    if status_code is not None and 400 <= status_code < 500:
        return AlpacaErrorInfo(CATEGORY_BROKER_REJECTION, False, status_code, message, type(exc).__name__)

    return AlpacaErrorInfo(CATEGORY_UNKNOWN, False, status_code, message, type(exc).__name__)


def call_alpaca(
    operation: str,
    fn: Callable[[], T],
    *,
    max_attempts: int = 3,
    base_delay_s: float = 0.25,
    max_delay_s: float = 1.0,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> T:
    """Run an Alpaca call with bounded retry for retryable categories."""
    attempts = max(1, int(max_attempts))
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            info = classify_alpaca_error(exc)
            if not info.retryable or attempt >= attempts:
                raise

            delay = min(max_delay_s, base_delay_s * (2 ** (attempt - 1)))
            log.warning(
                "Retrying Alpaca call operation=%s attempt=%s/%s category=%s "
                "status_code=%s delay_s=%.2f error=%s",
                operation,
                attempt + 1,
                attempts,
                info.category,
                info.status_code or "",
                delay,
                info.message,
            )
            if delay > 0:
                sleep_fn(delay)

    raise RuntimeError(f"unreachable Alpaca retry state for {operation}")
