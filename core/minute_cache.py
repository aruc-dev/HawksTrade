"""
Minute-bar cache for execution-realism backtests.

The cache stores one symbol/month per file under data/minute_cache/ by default.
Parquet is preferred when a parquet engine is installed; a compressed CSV
fallback keeps developer and CI environments usable before pyarrow is installed.
"""

from __future__ import annotations

import contextlib
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None
    import msvcrt

from core import alpaca_client as ac

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_DIR = BASE_DIR / "data" / "minute_cache"
OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]
log = logging.getLogger("minute_cache")


def _as_utc(value) -> datetime:
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        dt = value
    else:
        dt = pd.to_datetime(value).to_pydatetime()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _safe_symbol(symbol: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "", str(symbol or "").upper().replace("/", ""))
    if not cleaned:
        raise ValueError("symbol is required for minute cache")
    return cleaned


def _month_start(dt: datetime) -> datetime:
    dt = _as_utc(dt)
    return datetime(dt.year, dt.month, 1, tzinfo=timezone.utc)


def _next_month(dt: datetime) -> datetime:
    start = _month_start(dt)
    if start.month == 12:
        return datetime(start.year + 1, 1, 1, tzinfo=timezone.utc)
    return datetime(start.year, start.month + 1, 1, tzinfo=timezone.utc)


def _month_starts(start: datetime, end: datetime) -> Iterable[datetime]:
    current = _month_start(start)
    final = _month_start(end)
    while current <= final:
        yield current
        current = _next_month(current)


def cache_path(symbol: str, month: datetime, cache_dir: Path | str | None = None) -> Path:
    root = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
    return root / _safe_symbol(symbol) / f"{_month_start(month):%Y%m}.parquet"


def _csv_fallback_path(path: Path) -> Path:
    return path.with_suffix(".csv.gz")


def _lock_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".lock")


@contextlib.contextmanager
def _file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path(path)
    with open(lock_path, "a+b") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        else:  # pragma: no cover - Windows fallback
            lock_file.seek(0, 2)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            else:  # pragma: no cover - Windows fallback
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


def _bar_value(bar, field: str):
    if isinstance(bar, dict):
        return bar.get(field)
    return getattr(bar, field, None)


def _bars_to_frame(bars) -> pd.DataFrame:
    if bars is None:
        return pd.DataFrame(columns=["timestamp", *OHLCV_COLUMNS])
    if isinstance(bars, pd.DataFrame):
        frame = bars.copy()
        if "timestamp" not in frame.columns:
            frame = frame.reset_index().rename(columns={"index": "timestamp"})
        return _normalise_frame(frame)
    rows = []
    for bar in bars:
        timestamp = _bar_value(bar, "timestamp")
        rows.append({
            "timestamp": timestamp,
            "open": _bar_value(bar, "open"),
            "high": _bar_value(bar, "high"),
            "low": _bar_value(bar, "low"),
            "close": _bar_value(bar, "close"),
            "volume": _bar_value(bar, "volume"),
        })
    return _normalise_frame(pd.DataFrame(rows))


def _normalise_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["timestamp", *OHLCV_COLUMNS])
    df = frame.copy()
    if isinstance(df.index, pd.MultiIndex):
        df = df.reset_index()
    if "timestamp" not in df.columns:
        timestamp_col = next((col for col in df.columns if str(col).lower() in {"time", "datetime", "date"}), None)
        if timestamp_col is None:
            df = df.reset_index().rename(columns={"index": "timestamp"})
        else:
            df = df.rename(columns={timestamp_col: "timestamp"})
    missing = [col for col in OHLCV_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"minute bars missing required columns: {', '.join(missing)}")
    df = df[["timestamp", *OHLCV_COLUMNS]].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp"])
    for col in OHLCV_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=OHLCV_COLUMNS)
    df = df[df[["open", "high", "low", "close"]].gt(0).all(axis=1)]
    df = df[df["volume"].ge(0)]
    df = df.drop_duplicates(subset=["timestamp"], keep="last").sort_values("timestamp")
    return df.reset_index(drop=True)


def _read_cache(path: Path) -> pd.DataFrame:
    fallback = _csv_fallback_path(path)
    if path.exists():
        return _normalise_frame(pd.read_parquet(path))
    if fallback.exists():
        return _normalise_frame(pd.read_csv(fallback))
    return pd.DataFrame(columns=["timestamp", *OHLCV_COLUMNS])


def _write_cache(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = _normalise_frame(frame)
    try:
        df.to_parquet(path, index=False)
        fallback = _csv_fallback_path(path)
        if fallback.exists():
            fallback.unlink()
    except ImportError:
        fallback = _csv_fallback_path(path)
        df.to_csv(fallback, index=False)
        log.warning("Parquet engine unavailable; wrote minute cache fallback %s", fallback)


def fetch_stock_minute_bars(
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    source: Callable | None = None,
) -> pd.DataFrame:
    """Fetch real Alpaca stock minute bars and normalise them to cache schema."""
    source = source or ac.get_stock_bars
    bars_by_symbol = source(
        [symbol],
        timeframe="1Min",
        limit=1,
        start=start,
        end=end,
    )
    bars = None
    if isinstance(bars_by_symbol, dict):
        bars = bars_by_symbol.get(symbol)
    else:
        try:
            bars = bars_by_symbol[symbol]
        except Exception:
            bars = None
    return _bars_to_frame(bars)


def _default_fetcher(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    return fetch_stock_minute_bars(symbol, start, end)


def ensure_month_cached(
    symbol: str,
    month: datetime,
    *,
    cache_dir: Path | str | None = None,
    fetcher: Callable[[str, datetime, datetime], pd.DataFrame] | None = None,
    force_refresh: bool = False,
) -> Path:
    """Ensure the symbol/month file exists and return its preferred cache path."""
    path = cache_path(symbol, month, cache_dir)
    fetcher = fetcher or _default_fetcher
    with _file_lock(path):
        if not force_refresh and (path.exists() or _csv_fallback_path(path).exists()):
            return path
        start = _month_start(month)
        end = _next_month(month) - timedelta(microseconds=1)
        frame = fetcher(symbol, start, end)
        _write_cache(path, frame)
    return path


def get_minute_bars(
    symbol: str,
    start,
    end,
    *,
    cache_dir: Path | str | None = None,
    fetcher: Callable[[str, datetime, datetime], pd.DataFrame] | None = None,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Return cached/fetched 1-minute bars for symbol between start and end."""
    start_dt = _as_utc(start)
    end_dt = _as_utc(end)
    if start_dt > end_dt:
        raise ValueError("minute cache start must be before end")

    frames = []
    for month in _month_starts(start_dt, end_dt):
        path = ensure_month_cached(
            symbol,
            month,
            cache_dir=cache_dir,
            fetcher=fetcher,
            force_refresh=force_refresh,
        )
        frame = _read_cache(path)
        if not frame.empty:
            frames.append(frame)

    if not frames:
        return pd.DataFrame(columns=["timestamp", *OHLCV_COLUMNS])
    combined = _normalise_frame(pd.concat(frames, ignore_index=True))
    mask = (combined["timestamp"] >= pd.Timestamp(start_dt)) & (combined["timestamp"] <= pd.Timestamp(end_dt))
    return combined.loc[mask].reset_index(drop=True)
