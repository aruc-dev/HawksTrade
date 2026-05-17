"""
Point-in-time stock universe construction.

The backtester needs a candidate stock pool that would have been knowable on
the simulated date. This module loads a small auditable membership ledger and
exposes date-keyed membership helpers. Dynamic liquidity/quality screening
still happens in ``UniverseBuilder``; this layer defines which symbols may be
considered before the screen runs.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_UNIVERSE_FILE = BASE_DIR / "data" / "universe" / "sp500_constituents.csv"


def _as_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _optional_date(value: str | None) -> date | None:
    value = (value or "").strip()
    return _as_date(value) if value else None


@dataclass(frozen=True)
class UniverseMembership:
    symbol: str
    added_date: date | None
    removed_date: date | None = None
    ipo_date: date | None = None
    first_liquid_date: date | None = None
    delisted_date: date | None = None
    source: str = "index"

    def active_as_of(self, as_of: date, *, ipo_grace_days: int = 90) -> bool:
        if self.delisted_date is not None and as_of >= self.delisted_date:
            return False

        source = self.source.lower()
        if source == "non_index":
            anchor = self.first_liquid_date or (
                self.ipo_date + timedelta(days=ipo_grace_days)
                if self.ipo_date is not None
                else self.added_date
            )
            return anchor is not None and as_of >= anchor

        if self.added_date is not None and as_of < self.added_date:
            return False
        if self.removed_date is not None and as_of >= self.removed_date:
            return False
        return True

    def overlaps(self, start: date, end: date, *, ipo_grace_days: int = 90) -> bool:
        return any(
            self.active_as_of(day, ipo_grace_days=ipo_grace_days)
            for day in (start, end)
        ) or (
            self.added_date is not None
            and start <= self.added_date <= end
            and self.active_as_of(self.added_date, ipo_grace_days=ipo_grace_days)
        )


class PITUniverseBuilder:
    """Build an auditable point-in-time universe from a membership ledger."""

    def __init__(self, csv_path: Path | None = None, *, ipo_grace_days: int = 90):
        self.csv_path = Path(csv_path or DEFAULT_UNIVERSE_FILE)
        self.ipo_grace_days = int(ipo_grace_days)
        self._memberships: list[UniverseMembership] | None = None

    @property
    def memberships(self) -> list[UniverseMembership]:
        if self._memberships is None:
            self._memberships = self._load()
        return self._memberships

    def _load(self) -> list[UniverseMembership]:
        if not self.csv_path.exists():
            raise FileNotFoundError(f"PIT universe file not found: {self.csv_path}")

        rows: list[UniverseMembership] = []
        with self.csv_path.open(newline="", encoding="utf-8") as handle:
            for raw in csv.DictReader(handle):
                symbol = (raw.get("symbol") or "").strip().upper()
                if not symbol:
                    continue
                rows.append(
                    UniverseMembership(
                        symbol=symbol,
                        added_date=_optional_date(raw.get("added_date")),
                        removed_date=_optional_date(raw.get("removed_date")),
                        ipo_date=_optional_date(raw.get("ipo_date")),
                        first_liquid_date=_optional_date(raw.get("first_liquid_date")),
                        delisted_date=_optional_date(raw.get("delisted_date")),
                        source=(raw.get("source") or "index").strip() or "index",
                    )
                )
        return rows

    def members_as_of(self, as_of: date | datetime | str) -> list[str]:
        as_of_date = _as_date(as_of)
        return sorted({
            row.symbol
            for row in self.memberships
            if row.active_as_of(as_of_date, ipo_grace_days=self.ipo_grace_days)
        })

    def members_between(self, start: date | datetime | str, end: date | datetime | str) -> list[str]:
        start_date = _as_date(start)
        end_date = _as_date(end)
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        return sorted({
            row.symbol
            for row in self.memberships
            if row.overlaps(start_date, end_date, ipo_grace_days=self.ipo_grace_days)
        })

    def filter(self, symbols: Iterable[str], as_of: date | datetime | str) -> list[str]:
        allowed = set(self.members_as_of(as_of))
        return [symbol for symbol in symbols if symbol.upper() in allowed]
