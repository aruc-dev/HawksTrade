#!/usr/bin/env python3
"""
HawksTrade Linux health check.

This script inspects the configured cron template, parses runtime logs, checks
Alpaca connectivity, summarizes portfolio/trade health, and emits both a
terminal report and an HTML dashboard.

Usage:
  python3 scripts/check_health_linux.py
  python3 scripts/check_health_linux.py --cron-template pacific
  python3 scripts/check_health_linux.py --cron-file scheduler/cron/hawkstrade-pacific.cron
  python3 scripts/check_health_linux.py --hours 8
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd
import yaml

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core import alpaca_client as ac
from tracking.performance import compute_summary, load_closed_trades
from tracking.trade_log import get_open_trades

CONFIG_PATH = BASE_DIR / "config" / "config.yaml"
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CFG = yaml.safe_load(f)

LOG_DIR = BASE_DIR / CFG["reporting"]["logs_dir"]
REPORTS_DIR = BASE_DIR / CFG["reporting"]["reports_dir"]
DEFAULT_HTML_OUTPUT = REPORTS_DIR / "health_check_linux.html"
DEFAULT_CRON_FILES = {
    "eastern": BASE_DIR / "scheduler" / "cron" / "hawkstrade-eastern.cron",
    "pacific": BASE_DIR / "scheduler" / "cron" / "hawkstrade-pacific.cron",
    "utc": BASE_DIR / "scheduler" / "cron" / "hawkstrade-utc.cron",
}

# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CronPattern:
    minute_spec: str
    hour_spec: str
    dow_spec: str
    minutes: tuple[int, ...]
    hours: tuple[int, ...]
    dows: tuple[int, ...]

    @property
    def cron_text(self) -> str:
        return f"{self.minute_spec} {self.hour_spec} * * {self.dow_spec}"

    def matches(self, dt: datetime) -> bool:
        cron_dow = 7 if dt.weekday() == 6 else dt.weekday() + 1
        return (
            dt.minute in self.minutes
            and dt.hour in self.hours
            and cron_dow in self.dows
        )

    def cadence_minutes(self) -> int:
        samples = _generate_expected_times(self, datetime(2026, 1, 1), datetime(2026, 1, 3))
        if len(samples) < 2:
            return 60 * 24
        gaps = [
            int((samples[i + 1] - samples[i]).total_seconds() // 60)
            for i in range(len(samples) - 1)
            if samples[i + 1] > samples[i]
        ]
        return min(gaps) if gaps else 60 * 24


@dataclass(frozen=True)
class CronJob:
    key: str
    label: str
    pattern: CronPattern
    command: str
    source_file: Path
    line_no: int


@dataclass
class LogFinding:
    timestamp: datetime | None
    level: str
    logger: str
    message: str
    source_file: Path
    raw: str


@dataclass
class RunRecord:
    job_key: str
    label: str
    start_time: datetime
    end_time: datetime | None
    success: bool
    source_file: Path
    lines: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def duration(self) -> timedelta | None:
        if self.end_time is None:
            return None
        return self.end_time - self.start_time


@dataclass
class JobHealth:
    key: str
    label: str
    schedule_lines: list[str]
    last_run_at: datetime | None
    last_success_at: datetime | None
    last_duration: timedelta | None
    missed_runs: int
    expected_runs: int
    status: str
    latest_note: str | None = None

    @property
    def age(self) -> timedelta | None:
        if self.last_run_at is None:
            return None
        return datetime.now().astimezone().replace(tzinfo=None) - self.last_run_at


@dataclass
class AlpacaState:
    connected: bool
    account_error: str | None
    positions_error: str | None
    portfolio_value: float | None = None
    cash: float | None = None
    buying_power: float | None = None
    broker_positions: list[dict] = field(default_factory=list)
    trade_log_open_rows: list[dict] = field(default_factory=list)

    @property
    def open_position_count(self) -> int:
        if self.connected:
            return len(self.broker_positions)
        return len(self.trade_log_open_rows)


@dataclass
class HealthReport:
    generated_at: datetime
    lookback_hours: float
    cron_template: str
    cron_file: Path
    local_timezone: str
    overall_status: str
    alpaca: AlpacaState
    job_health: list[JobHealth]
    trade_summary: dict
    log_errors: list[LogFinding]
    log_warnings: list[LogFinding]
    html_output: Path


# ── Cron parsing ─────────────────────────────────────────────────────────────


CRON_LINE_RE = re.compile(
    r"^(?P<minute>\S+)\s+(?P<hour>\S+)\s+(?P<dom>\S+)\s+(?P<month>\S+)\s+(?P<dow>\S+)\s+(?P<command>.+)$"
)

LOG_LINE_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) "
    r"\[(?P<level>[A-Z]+)\] (?P<logger>[^:]+): (?P<message>.*)$"
)


def _parse_int_tokens(spec: str, minimum: int, maximum: int, *, normalize_dow: bool = False) -> tuple[int, ...]:
    tokens: set[int] = set()
    raw = spec.strip()
    if raw == "*":
        if normalize_dow:
            return tuple(range(1, 8))
        return tuple(range(minimum, maximum + 1))

    for part in raw.split(","):
        piece = part.strip()
        if not piece:
            continue
        if piece == "*":
            if normalize_dow:
                tokens.update(range(1, 8))
            else:
                tokens.update(range(minimum, maximum + 1))
            continue
        if "-" in piece:
            start_s, end_s = piece.split("-", 1)
            start = int(start_s)
            end = int(end_s)
            if normalize_dow:
                start = 7 if start in (0, 7) else start
                end = 7 if end in (0, 7) else end
                if start == 7 and end == 7:
                    tokens.add(7)
                    continue
                if start <= end:
                    tokens.update(range(start, end + 1))
                else:
                    tokens.update(range(start, 8))
                    tokens.update(range(1, end + 1))
            else:
                tokens.update(range(start, end + 1))
            continue

        value = int(piece)
        if normalize_dow and value in (0, 7):
            value = 7
        tokens.add(value)

    return tuple(sorted(tokens))


def _cron_pattern(minute_spec: str, hour_spec: str, dow_spec: str) -> CronPattern:
    return CronPattern(
        minute_spec=minute_spec,
        hour_spec=hour_spec,
        dow_spec=dow_spec,
        minutes=_parse_int_tokens(minute_spec, 0, 59),
        hours=_parse_int_tokens(hour_spec, 0, 23),
        dows=_parse_int_tokens(dow_spec, 0, 7, normalize_dow=True),
    )


def _job_from_command(command: str) -> tuple[str, str] | None:
    normalized = command.lower()
    if "python3 scheduler/run_scan.py --stocks-only" in normalized:
        return "stock_scan", "Stock scan"
    if "python3 scheduler/run_scan.py --crypto-only" in normalized:
        return "crypto_scan", "Crypto scan"
    if "python3 scheduler/run_scan.py" in normalized and "--stocks-only" not in normalized and "--crypto-only" not in normalized:
        return "full_scan", "Full scan"
    if "python3 scheduler/run_risk_check.py" in normalized:
        return "risk_check", "Risk check"
    if "python3 scheduler/run_report.py --weekly" in normalized:
        return "weekly_report", "Weekly report"
    if "python3 scheduler/run_report.py" in normalized and "--weekly" not in normalized:
        return "daily_report", "Daily report"
    return None


def load_cron_jobs(cron_file: Path) -> list[CronJob]:
    jobs: list[CronJob] = []
    with open(cron_file, "r", encoding="utf-8") as f:
        for line_no, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("SHELL=") or line.startswith("PATH=") or line.startswith("HAWKSTRADE_DIR="):
                continue
            match = CRON_LINE_RE.match(line)
            if not match:
                continue
            if match.group("dom") != "*" or match.group("month") != "*":
                continue
            job = _job_from_command(match.group("command"))
            if job is None:
                continue
            key, label = job
            pattern = _cron_pattern(match.group("minute"), match.group("hour"), match.group("dow"))
            jobs.append(
                CronJob(
                    key=key,
                    label=label,
                    pattern=pattern,
                    command=match.group("command"),
                    source_file=cron_file,
                    line_no=line_no,
                )
            )
    return jobs


def _group_jobs(jobs: Iterable[CronJob]) -> dict[str, list[CronJob]]:
    grouped: dict[str, list[CronJob]] = {}
    for job in jobs:
        grouped.setdefault(job.key, []).append(job)
    return grouped


def _human_cron_line(pattern: CronPattern) -> str:
    return pattern.cron_text


def detect_cron_template(local_timezone: str) -> str:
    tz = (local_timezone or "").upper()
    if tz in {"PDT", "PST"}:
        return "pacific"
    if tz in {"EDT", "EST"}:
        return "eastern"
    if tz in {"UTC", "GMT"}:
        return "utc"
    return "pacific"


def resolve_cron_file(cron_template: str | None = None, cron_file: str | Path | None = None) -> tuple[str, Path]:
    if cron_file:
        path = Path(cron_file).expanduser().resolve()
        template = _template_from_filename(path)
        if cron_template and cron_template != "auto":
            template = cron_template
        return template, path

    template = (cron_template or "auto").lower()
    if template == "auto":
        template = detect_cron_template(datetime.now().astimezone().tzname() or "")
    path = DEFAULT_CRON_FILES.get(template, DEFAULT_CRON_FILES["pacific"])
    return template, path


def _template_from_filename(path: Path) -> str:
    stem = path.stem.lower()
    if "eastern" in stem:
        return "eastern"
    if "utc" in stem:
        return "utc"
    if "pacific" in stem:
        return "pacific"
    return "custom"


# ── Log parsing ──────────────────────────────────────────────────────────────


def _parse_log_line(line: str, source_file: Path) -> LogFinding | None:
    match = LOG_LINE_RE.match(line.rstrip("\n"))
    if not match:
        return None
    timestamp = datetime.strptime(match.group("timestamp"), "%Y-%m-%d %H:%M:%S,%f")
    return LogFinding(
        timestamp=timestamp,
        level=match.group("level"),
        logger=match.group("logger"),
        message=match.group("message"),
        source_file=source_file,
        raw=line.rstrip("\n"),
    )


def _read_log_lines(path: Path) -> list[LogFinding]:
    findings: list[LogFinding] = []
    if not path.exists():
        return findings
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            finding = _parse_log_line(raw, path)
            if finding is not None:
                findings.append(finding)
    return findings


def _find_matching_error_lines(
    paths: Iterable[Path],
    since: datetime | None = None,
) -> tuple[list[LogFinding], list[LogFinding]]:
    errors: list[LogFinding] = []
    warnings: list[LogFinding] = []
    for path in paths:
        for finding in _read_log_lines(path):
            if since is not None and finding.timestamp is not None and finding.timestamp < since:
                continue
            msg = finding.message.lower()
            if finding.level == "ERROR" or "traceback" in msg:
                errors.append(finding)
            elif finding.level == "WARNING":
                warnings.append(finding)
    errors.sort(key=lambda item: (item.timestamp or datetime.min, item.source_file.name))
    warnings.sort(key=lambda item: (item.timestamp or datetime.min, item.source_file.name))
    return errors, warnings


def _log_files(log_dir: Path) -> list[Path]:
    patterns = [
        "scan_*.log",
        "risk_*.log",
        "report_*.log",
        "cron.log",
    ]
    files: list[Path] = []
    for pattern in patterns:
        files.extend(sorted(log_dir.glob(pattern)))
    files = [path for path in files if path.exists()]
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in files:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def _split_scan_records(log_files: Iterable[Path]) -> list[RunRecord]:
    records: list[RunRecord] = []
    for path in sorted(log_files):
        if not path.name.startswith("scan_"):
            continue
        current: RunRecord | None = None
        for finding in _read_log_lines(path):
            if finding.logger != "run_scan":
                if current is not None:
                    current.lines.append(finding.raw)
                continue

            if "HawksTrade scan started" in finding.message:
                if current is not None:
                    _finalize_scan_record(current, records)
                current = RunRecord(
                    job_key="scan",
                    label="Scan",
                    start_time=finding.timestamp or datetime.now(),
                    end_time=None,
                    success=False,
                    source_file=path,
                    lines=[finding.raw],
                )
                continue

            if current is None:
                continue

            current.lines.append(finding.raw)
            if "Scan complete." in finding.message:
                current.end_time = finding.timestamp
                current.success = True
                _finalize_scan_record(current, records)
                current = None

        if current is not None:
            _finalize_scan_record(current, records)
    return records


def _finalize_scan_record(record: RunRecord, records: list[RunRecord]) -> None:
    text = "\n".join(record.lines)
    has_stock = "--- Running stock strategies ---" in text or "Market closed. Stock strategies skipped." in text
    has_crypto = "--- Running crypto strategies ---" in text
    if has_stock and has_crypto:
        record.job_key = "full_scan"
        record.label = "Full scan"
    elif has_crypto and not has_stock:
        record.job_key = "crypto_scan"
        record.label = "Crypto scan"
    elif has_stock:
        record.job_key = "stock_scan"
        record.label = "Stock scan"
    else:
        record.job_key = "scan_unknown"
        record.label = "Scan"

    if any("[ERROR]" in line or "Traceback" in line for line in record.lines):
        record.notes.append("error in run")

    records.append(record)


def _split_risk_records(log_files: Iterable[Path]) -> list[RunRecord]:
    records: list[RunRecord] = []
    for path in sorted(log_files):
        if not path.name.startswith("risk_"):
            continue
        current: RunRecord | None = None
        for finding in _read_log_lines(path):
            if finding.logger != "run_risk_check":
                if current is not None:
                    current.lines.append(finding.raw)
                continue

            if "--- Risk Check" in finding.message:
                if current is not None:
                    _finalize_risk_record(current, records)
                current = RunRecord(
                    job_key="risk_check",
                    label="Risk check",
                    start_time=finding.timestamp or datetime.now(),
                    end_time=None,
                    success=False,
                    source_file=path,
                    lines=[finding.raw],
                )
                continue

            if current is None:
                continue

            current.lines.append(finding.raw)
            if "Risk check complete." in finding.message:
                current.end_time = finding.timestamp
                current.success = True
                _finalize_risk_record(current, records)
                current = None

        if current is not None:
            _finalize_risk_record(current, records)
    return records


def _finalize_risk_record(record: RunRecord, records: list[RunRecord]) -> None:
    if any("[ERROR]" in line or "Traceback" in line for line in record.lines):
        record.notes.append("error in run")
    records.append(record)


def _split_report_records(log_files: Iterable[Path]) -> list[RunRecord]:
    records: list[RunRecord] = []
    for path in sorted(log_files):
        if not path.name.startswith("report_"):
            continue
        current: RunRecord | None = None
        for finding in _read_log_lines(path):
            if finding.logger != "run_report":
                if current is not None:
                    current.lines.append(finding.raw)
                continue

            if "=== DAILY REPORT ===" in finding.message:
                if current is not None:
                    _finalize_report_record(current, records)
                current = RunRecord(
                    job_key="daily_report",
                    label="Daily report",
                    start_time=finding.timestamp or datetime.now(),
                    end_time=None,
                    success=False,
                    source_file=path,
                    lines=[finding.raw],
                )
                continue

            if "=== WEEKLY REPORT ===" in finding.message:
                if current is not None:
                    _finalize_report_record(current, records)
                current = RunRecord(
                    job_key="weekly_report",
                    label="Weekly report",
                    start_time=finding.timestamp or datetime.now(),
                    end_time=None,
                    success=False,
                    source_file=path,
                    lines=[finding.raw],
                )
                continue

            if current is None:
                continue

            current.lines.append(finding.raw)
            if "Daily report saved:" in finding.message or "Weekly report saved:" in finding.message:
                current.end_time = finding.timestamp
                current.success = True
                _finalize_report_record(current, records)
                current = None

        if current is not None:
            _finalize_report_record(current, records)
    return records


def _finalize_report_record(record: RunRecord, records: list[RunRecord]) -> None:
    if any("[ERROR]" in line or "Traceback" in line for line in record.lines):
        record.notes.append("error in run")
    records.append(record)


def load_runtime_records(log_dir: Path) -> dict[str, list[RunRecord]]:
    files = _log_files(log_dir)
    report_records = _split_report_records(files)
    return {
        "scan": _split_scan_records(files),
        "risk_check": _split_risk_records(files),
        "daily_report": [r for r in report_records if r.job_key == "daily_report"],
        "weekly_report": [r for r in report_records if r.job_key == "weekly_report"],
        "all_files": files,
    }


# ── Health evaluation ────────────────────────────────────────────────────────


def _generate_expected_times(pattern: CronPattern, start: datetime, end: datetime) -> list[datetime]:
    start = start.replace(second=0, microsecond=0)
    end = end.replace(second=0, microsecond=0)
    if end < start:
        return []
    current = start
    expected: list[datetime] = []
    while current <= end:
        if pattern.matches(current):
            expected.append(current)
        current += timedelta(minutes=1)
    return expected


def _match_expected_runs(expected: list[datetime], actual: list[datetime], tolerance_minutes: int) -> tuple[int, list[datetime]]:
    missed = 0
    used: set[int] = set()
    matched_actual: list[datetime] = []
    tolerance = timedelta(minutes=tolerance_minutes)
    for exp in expected:
        candidate_idx = None
        candidate_delta = None
        for idx, actual_dt in enumerate(actual):
            if idx in used:
                continue
            delta = abs(actual_dt - exp)
            if delta <= tolerance and (candidate_delta is None or delta < candidate_delta):
                candidate_idx = idx
                candidate_delta = delta
        if candidate_idx is None:
            missed += 1
            continue
        used.add(candidate_idx)
        matched_actual.append(actual[candidate_idx])
    return missed, matched_actual


def _job_status(latest_recent: RunRecord | None, missed_runs: int, expected_runs: int) -> str:
    if expected_runs == 0:
        return "green"
    if latest_recent is None:
        return "red"
    if missed_runs > 0:
        return "red"
    if not latest_recent.success:
        return "red"
    if any("error in run" in note for note in latest_recent.notes):
        return "red"
    return "green"


def evaluate_job_health(
    jobs: list[CronJob],
    records: list[RunRecord],
    *,
    now: datetime,
    lookback_hours: float = 4.0,
) -> list[JobHealth]:
    grouped = _group_jobs(jobs)
    by_key = {
        "stock_scan": [r for r in records if r.job_key == "stock_scan"],
        "full_scan": [r for r in records if r.job_key == "full_scan"],
        "crypto_scan": [r for r in records if r.job_key == "crypto_scan"],
        "risk_check": [r for r in records if r.job_key == "risk_check"],
        "daily_report": [r for r in records if r.job_key == "daily_report"],
        "weekly_report": [r for r in records if r.job_key == "weekly_report"],
    }

    window_start = now - timedelta(hours=lookback_hours)
    health_rows: list[JobHealth] = []
    for key, jobs_for_key in grouped.items():
        all_expected: list[datetime] = []
        for job in jobs_for_key:
            all_expected.extend(_generate_expected_times(job.pattern, window_start, now))
        all_expected = sorted(set(all_expected))
        key_records = sorted(by_key.get(key, []), key=lambda item: item.start_time)
        recent_records = [record for record in key_records if record.start_time >= window_start]
        actual_runs = [r.start_time for r in recent_records]
        tolerance = _tolerance_minutes(jobs_for_key)
        missed, _matched = _match_expected_runs(all_expected, actual_runs, tolerance)
        latest_any = key_records[-1] if key_records else None
        latest_recent = recent_records[-1] if recent_records else None
        latest_visible = latest_recent or latest_any
        last_success = max(
            (record.start_time for record in recent_records if record.success),
            default=None,
        )
        if last_success is None:
            last_success = max(
                (record.start_time for record in key_records if record.success),
                default=None,
            )
        if expected_runs := len(all_expected):
            if latest_recent is None:
                latest_note = f"No run in last {lookback_hours:g} hours"
            else:
                latest_note = _latest_note(latest_visible)
        else:
            latest_note = _latest_note(latest_visible)
        health_rows.append(
            JobHealth(
                key=key,
                label=jobs_for_key[0].label if jobs_for_key else key,
                schedule_lines=[_human_cron_line(job.pattern) for job in jobs_for_key],
                last_run_at=latest_visible.start_time if latest_visible else None,
                last_success_at=last_success,
                last_duration=latest_visible.duration if latest_visible else None,
                missed_runs=missed,
                expected_runs=len(all_expected),
                status=_job_status(latest_recent, missed, len(all_expected)),
                latest_note=latest_note,
            )
        )

    order = {
        "stock_scan": 0,
        "full_scan": 1,
        "crypto_scan": 2,
        "risk_check": 3,
        "daily_report": 4,
        "weekly_report": 5,
    }
    health_rows.sort(key=lambda row: order.get(row.key, 99))
    return health_rows


def _latest_note(record: RunRecord | None) -> str | None:
    if record is None:
        return "No matching run found in logs"
    if not record.success:
        return "Last run did not complete cleanly"
    if record.notes:
        return "; ".join(record.notes)
    return None


def _tolerance_minutes(jobs: list[CronJob]) -> int:
    cadences = [job.pattern.cadence_minutes() for job in jobs]
    cadence = min(cadences) if cadences else 60
    return max(5, min(30, cadence // 4))


def _zero_summary(now: datetime) -> dict:
    return {
        "generated_at": now.isoformat(),
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "avg_win_pct": 0.0,
        "avg_loss_pct": 0.0,
        "total_pnl_pct": 0.0,
        "realized_pnl_pct": 0.0,
        "realized_pnl_dollars": 0.0,
        "open_positions": 0,
        "unrealized_pnl_dollars": 0.0,
        "total_pnl_dollars": 0.0,
        "monthly_pnl": {},
        "by_strategy": {},
    }


def _build_open_positions_rows(alpaca_state: AlpacaState) -> list[dict]:
    if alpaca_state.connected and alpaca_state.broker_positions:
        return alpaca_state.broker_positions

    rows: list[dict] = []
    for row in alpaca_state.trade_log_open_rows:
        rows.append(
            {
                "symbol": row.get("symbol", ""),
                "qty": float(row.get("qty") or 0),
                "avg_entry_price": float(row.get("entry_price") or 0),
                "current_price": None,
                "market_value": None,
                "unrealized_pnl": None,
                "unrealized_pnl_pct": None,
                "source": "trade_log",
            }
        )
    return rows


def build_trade_summary(alpaca_state: AlpacaState, now: datetime) -> dict:
    open_rows = _build_open_positions_rows(alpaca_state)
    open_df_rows = []
    for row in open_rows:
        unrealized = row.get("unrealized_pnl")
        if unrealized is None:
            if row.get("current_price") in (None, "") or row.get("avg_entry_price") in (None, ""):
                unrealized_dollars = 0.0
            else:
                unrealized_dollars = (float(row["current_price"]) - float(row["avg_entry_price"])) * float(row["qty"])
        else:
            unrealized_dollars = float(unrealized)
        open_df_rows.append(
            {
                "symbol": row.get("symbol", ""),
                "qty": row.get("qty", 0),
                "entry_price": row.get("avg_entry_price", 0),
                "current_price": row.get("current_price", row.get("avg_entry_price", 0)),
                "unrealized_pnl_dollars": unrealized_dollars if row.get("current_price") not in (None, "") else 0.0,
            }
        )

    open_df = pd.DataFrame(open_df_rows)
    closed = load_closed_trades()

    if closed.empty and open_df.empty:
        summary = _zero_summary(now)
    else:
        summary = compute_summary(closed, open_positions=open_df)
        if "error" in summary:
            summary = _zero_summary(now)

    summary["open_positions_rows"] = open_rows
    return summary


def fetch_alpaca_state() -> AlpacaState:
    connected = False
    account_error = None
    positions_error = None
    portfolio_value = None
    cash = None
    buying_power = None
    broker_positions: list[dict] = []
    trade_log_open_rows = get_open_trades()

    try:
        account = ac.get_account()
        portfolio_value = float(account.portfolio_value)
        cash = float(account.cash)
        buying_power = float(account.buying_power)
        connected = True
    except Exception as exc:  # pragma: no cover - exercised in live failures
        account_error = str(exc)

    if connected:
        try:
            positions = ac.get_all_positions()
            for pos in positions or []:
                broker_positions.append(
                    {
                        "symbol": getattr(pos, "symbol", ""),
                        "qty": float(getattr(pos, "qty", 0) or 0),
                        "avg_entry_price": float(getattr(pos, "avg_entry_price", 0) or 0),
                        "current_price": float(getattr(pos, "current_price", getattr(pos, "avg_entry_price", 0)) or 0),
                        "market_value": float(getattr(pos, "market_value", 0) or 0),
                        "unrealized_pnl": float(getattr(pos, "unrealized_pl", 0) or 0),
                        "unrealized_pnl_pct": float(getattr(pos, "unrealized_plpc", 0) or 0),
                    }
                )
        except Exception as exc:  # pragma: no cover - exercised in live failures
            positions_error = str(exc)
            connected = False

    return AlpacaState(
        connected=connected,
        account_error=account_error,
        positions_error=positions_error,
        portfolio_value=portfolio_value,
        cash=cash,
        buying_power=buying_power,
        broker_positions=broker_positions,
        trade_log_open_rows=trade_log_open_rows,
    )


def build_health_report(
    *,
    cron_template: str = "auto",
    cron_file: str | Path | None = None,
    log_dir: str | Path = LOG_DIR,
    html_output: str | Path = DEFAULT_HTML_OUTPUT,
    now: datetime | None = None,
    lookback_hours: float = 4.0,
    alpaca_state: AlpacaState | None = None,
    trade_summary: dict | None = None,
) -> HealthReport:
    now = now or datetime.now().astimezone().replace(tzinfo=None)
    template_name, cron_path = resolve_cron_file(cron_template=cron_template, cron_file=cron_file)
    jobs = load_cron_jobs(cron_path)
    runtime = load_runtime_records(Path(log_dir))

    scan_records = runtime["scan"]
    risk_records = runtime["risk_check"]
    report_daily_records = runtime["daily_report"]
    report_weekly_records = runtime["weekly_report"]
    all_records = scan_records + risk_records + report_daily_records + report_weekly_records

    if alpaca_state is None:
        alpaca_state = fetch_alpaca_state()

    if trade_summary is None:
        trade_summary = build_trade_summary(alpaca_state, now)

    job_health = evaluate_job_health(jobs, all_records, now=now, lookback_hours=lookback_hours)

    log_files = runtime["all_files"]
    window_start = now - timedelta(hours=lookback_hours)
    errors, warnings = _find_matching_error_lines(log_files, since=window_start)

    overall = _overall_status(alpaca_state, job_health, errors)

    return HealthReport(
        generated_at=now,
        lookback_hours=lookback_hours,
        cron_template=template_name,
        cron_file=cron_path,
        local_timezone=datetime.now().astimezone().tzname() or "local",
        overall_status=overall,
        alpaca=alpaca_state,
        job_health=job_health,
        trade_summary=trade_summary,
        log_errors=errors,
        log_warnings=warnings,
        html_output=Path(html_output).expanduser().resolve(),
    )


def _overall_status(alpaca_state: AlpacaState, job_health: list[JobHealth], errors: list[LogFinding]) -> str:
    if not alpaca_state.connected:
        return "red"
    if errors:
        return "red"
    if any(job.status == "red" for job in job_health):
        return "red"
    if any(job.status == "yellow" for job in job_health):
        return "yellow"
    return "green"


# ── Formatting helpers ───────────────────────────────────────────────────────


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"${value:,.2f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.2%}"


def _fmt_duration(delta: timedelta | None) -> str:
    if delta is None:
        return "N/A"
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        total_seconds = 0
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    else:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def _fmt_timestamp(ts: datetime | None) -> str:
    if ts is None:
        return "N/A"
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def _severity_label(status: str, enabled: bool = False) -> str:
    mapping = {
        "green": "[OK]",
        "yellow": "[WARN]",
        "red": "[NOK]",
    }
    return mapping.get(status, "[UNKNOWN]")


def _signed_value(value: float | None, *, enabled: bool = False, money: bool = False, pct: bool = False) -> str:
    if value is None:
        return "N/A"
    if pct:
        return f"{value:+.2%}"
    if money:
        return f"${value:+,.2f}"
    return f"{value:+,.2f}"


def _count_value(value: int, *, enabled: bool = False) -> str:
    return f"{value} {'[OK]' if value == 0 else '[NOK]'}"


def _row_status(job: JobHealth, enabled: bool = False) -> str:
    return _severity_label(job.status, enabled)


def _job_source_label(job: JobHealth) -> str:
    return "; ".join(job.schedule_lines)


def _line(text: str = "", width: int = 88) -> str:
    if not text:
        return "-" * width
    return text


def format_terminal_report(report: HealthReport, *, use_color: bool = False) -> str:
    table_width = 128
    generated_tz = report.generated_at.astimezone().tzname() or report.local_timezone
    overall = _severity_label(report.overall_status)
    window_label = f"last {report.lookback_hours:g}h"

    lines: list[str] = []
    lines.append("=" * table_width)
    lines.append("HAWKSTRADE LINUX HEALTH CHECK")
    lines.append(f"Generated : {report.generated_at.strftime('%Y-%m-%d %H:%M:%S')} {generated_tz}")
    lines.append(f"Cron file : {report.cron_file}")
    lines.append(f"Template  : {report.cron_template} | Local TZ: {report.local_timezone}")
    lines.append(f"Window    : {window_label}")
    lines.append(f"Overall   : {overall}")
    lines.append("Legend    : [OK]=healthy | [WARN]=review | [NOK]=attention")
    lines.append("=" * table_width)
    lines.append("")

    lines.append("CRON HEALTH")
    lines.append(
        f"{'Job':<16} {'Schedule':<44} {'Last Run':<19} {'Age':<10} {'Dur':<10} {'Missed':<15} {'Status'}"
    )
    lines.append("-" * table_width)
    for job in report.job_health:
        schedule = " ; ".join(job.schedule_lines)
        last_run = _fmt_timestamp(job.last_run_at)
        age = _fmt_duration(job.age)
        duration = _fmt_duration(job.last_duration)
        status = _row_status(job)
        missed = _count_value(job.missed_runs)
        lines.append(
            f"{job.label:<16} {schedule[:44]:<44} {last_run:<19} {age:<10} {duration:<10} "
            f"{missed:<15} {status}"
        )
    lines.append("")
    lines.append("Missed counts reflect gaps detected between observed runs and the cron template.")
    lines.append("")

    issues = [job for job in report.job_health if job.status != "green"]
    if issues:
        lines.append("ISSUE SUMMARY")
        for job in issues:
            reason_bits = []
            if job.missed_runs:
                reason_bits.append(f"{job.missed_runs} missed run(s)")
            if job.latest_note:
                reason_bits.append(job.latest_note)
            reason = "; ".join(reason_bits) if reason_bits else "status requires attention"
            lines.append(f"  - [NOK] {job.label}: {reason}")
        lines.append("")

    lines.append("PORTFOLIO")
    if report.alpaca.connected:
        lines.append(f"Alpaca connectivity : OK [OK]")
    else:
        lines.append(f"Alpaca connectivity : FAILED [NOK]")
        if report.alpaca.account_error:
            lines.append(f"  Account error    : {report.alpaca.account_error}")
        if report.alpaca.positions_error:
            lines.append(f"  Positions error  : {report.alpaca.positions_error}")
    lines.append(f"Portfolio value     : {_fmt_money(report.alpaca.portfolio_value)}")
    lines.append(f"Cash                : {_fmt_money(report.alpaca.cash)}")
    lines.append(f"Buying power        : {_fmt_money(report.alpaca.buying_power)}")
    open_positions_tag = "[OK]" if report.alpaca.open_position_count > 0 else "[WARN]"
    lines.append(f"Open positions      : {report.alpaca.open_position_count} {open_positions_tag}")
    lines.append(f"Closed trades       : {report.trade_summary.get('total_trades', 0)}")
    lines.append(
        f"Realized P/L        : {_signed_value(report.trade_summary.get('realized_pnl_dollars'), money=True)} "
        f"({_signed_value(report.trade_summary.get('realized_pnl_pct'), pct=True)})"
    )
    unrealized = report.trade_summary.get("unrealized_pnl_dollars")
    lines.append(f"Unrealized P/L      : {_signed_value(unrealized, money=True)}")
    lines.append(f"Total P/L           : {_signed_value(report.trade_summary.get('total_pnl_dollars'), money=True)}")
    lines.append("")

    lines.append("OPEN POSITIONS")
    if report.alpaca.connected and report.alpaca.broker_positions:
        lines.append(f"{'Symbol':<10} {'Qty':>12} {'Entry':>12} {'Now':>12} {'P/L $':>12} {'P/L %':>10} {'State':>8}")
        lines.append("-" * 86)
        for pos in report.alpaca.broker_positions:
            pnl_dollars = float(pos.get("unrealized_pnl") or 0)
            pnl_pct = float(pos.get("unrealized_pnl_pct") or 0)
            state = "[OK]" if pnl_dollars >= 0 else "[NOK]"
            lines.append(
                f"{pos['symbol']:<10} {pos['qty']:>12.4f} {pos['avg_entry_price']:>12.4f} "
                f"{pos['current_price']:>12.4f} "
                f"{_signed_value(pnl_dollars, money=True):>12} "
                f"{_signed_value(pnl_pct, pct=True):>10} "
                f"{state:>8}"
            )
    else:
        lines.append("Broker positions unavailable; showing local open trade rows only.")
        if report.alpaca.trade_log_open_rows:
            lines.append(f"{'Symbol':<10} {'Qty':>12} {'Entry':>12} {'Current':>12} {'Source':>10}")
            lines.append("-" * 62)
            for row in report.alpaca.trade_log_open_rows:
                lines.append(
                    f"{row.get('symbol', ''):<10} {float(row.get('qty') or 0):>12.4f} "
                    f"{float(row.get('entry_price') or 0):>12.4f} {'N/A':>12} {'trade_log':>10}"
                )
        else:
            lines.append("No open positions detected.")
    lines.append("")

    lines.append("LOG HEALTH")
    errors_yesno = "YES [NOK]" if report.log_errors else "NO [OK]"
    warnings_yesno = "YES [WARN]" if report.log_warnings else "NO [OK]"
    lines.append(f"Errors in logs     : {errors_yesno} ({_count_value(len(report.log_errors))})")
    lines.append(f"Warnings in logs   : {warnings_yesno} ({_count_value(len(report.log_warnings))})")
    if report.log_errors:
        lines.append("Most recent errors:")
        for finding in report.log_errors[-5:]:
            ts = _fmt_timestamp(finding.timestamp)
            lines.append(f"  {ts} | {finding.source_file.name} | {finding.message}")
    lines.append("")
    lines.append("=" * table_width)
    return "\n".join(lines)


def render_html_report(report: HealthReport) -> str:
    def badge(status: str) -> str:
        label = {
            "green": "OK",
            "yellow": "WARN",
            "red": "ISSUE",
        }.get(status, "UNKNOWN")
        return f'<span class="badge {status}">{html.escape(label)}</span>'

    def esc(value: object) -> str:
        return html.escape("" if value is None else str(value))

    cards = [
        ("Overall Health", report.overall_status, report.overall_status.upper()),
        ("Alpaca Connectivity", "green" if report.alpaca.connected else "red", "Connected" if report.alpaca.connected else "Disconnected"),
        ("Open Positions", "green" if report.alpaca.open_position_count > 0 else "yellow", str(report.alpaca.open_position_count)),
        ("Closed Trades", "green", str(report.trade_summary.get("total_trades", 0))),
        ("Realized P/L", "green" if float(report.trade_summary.get("realized_pnl_dollars", 0) or 0) >= 0 else "red", f"{_fmt_money(report.trade_summary.get('realized_pnl_dollars'))} ({_fmt_pct(report.trade_summary.get('realized_pnl_pct'))})"),
        ("Unrealized P/L", "green" if float(report.trade_summary.get("unrealized_pnl_dollars", 0) or 0) >= 0 else "red", _fmt_money(report.trade_summary.get("unrealized_pnl_dollars"))),
        ("Log Errors", "red" if report.log_errors else "green", f"{len(report.log_errors)}"),
        ("Missed Runs", "red" if any(job.missed_runs > 0 for job in report.job_health) else "green", str(sum(job.missed_runs for job in report.job_health))),
    ]

    job_rows = []
    for job in report.job_health:
        job_rows.append(
            f"""
            <tr class="{job.status}">
              <td>{esc(job.label)}</td>
              <td><code>{esc(' | '.join(job.schedule_lines))}</code></td>
              <td>{esc(_fmt_timestamp(job.last_run_at))}</td>
              <td>{esc(_fmt_duration(job.age))}</td>
              <td>{esc(_fmt_duration(job.last_duration))}</td>
              <td>{job.missed_runs}</td>
              <td>{badge(job.status)}</td>
            </tr>
            """
        )

    position_rows = []
    if report.alpaca.connected and report.alpaca.broker_positions:
        for pos in report.alpaca.broker_positions:
            pnl_dollars = float(pos.get("unrealized_pnl") or 0)
            pnl_pct = float(pos.get("unrealized_pnl_pct") or 0)
            position_rows.append(
                f"""
                <tr class="{'green' if pnl_dollars >= 0 else 'red'}">
                  <td><code>{esc(pos.get('symbol'))}</code></td>
                  <td>{esc(f"{float(pos.get('qty') or 0):.4f}")}</td>
                  <td>{esc(f"{float(pos.get('avg_entry_price') or 0):.4f}")}</td>
                  <td>{esc(f"{float(pos.get('current_price') or 0):.4f}")}</td>
                  <td>{esc(f"{pnl_dollars:+,.2f}")}</td>
                  <td>{esc(f"{pnl_pct:+.2%}")}</td>
                </tr>
                """
            )
    else:
        for row in report.alpaca.trade_log_open_rows:
            position_rows.append(
                f"""
                <tr class="yellow">
                  <td><code>{esc(row.get('symbol'))}</code></td>
                  <td>{esc(f"{float(row.get('qty') or 0):.4f}")}</td>
                  <td>{esc(f"{float(row.get('entry_price') or 0):.4f}")}</td>
                  <td>N/A</td>
                  <td>N/A</td>
                  <td>N/A</td>
                </tr>
                """
            )

    error_items = []
    for finding in report.log_errors[-10:]:
        error_items.append(
            f"<li><code>{esc(_fmt_timestamp(finding.timestamp))}</code> "
            f"<span>{esc(finding.source_file.name)}</span> "
            f"<span>{esc(finding.message)}</span></li>"
        )

    warning_count = len(report.log_warnings)
    generated_local = esc(report.generated_at.strftime("%Y-%m-%d %H:%M:%S"))
    generated_tz = esc(report.generated_at.astimezone().tzname() or report.local_timezone)
    cron_name = esc(report.cron_template)
    cron_file = esc(report.cron_file)
    window_label = esc(f"last {report.lookback_hours:g}h")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>HawksTrade Linux Health Check</title>
  <style>
    :root {{
      --bg: #08111f;
      --bg2: #0f172a;
      --panel: rgba(15, 23, 42, 0.88);
      --panel-border: rgba(148, 163, 184, 0.18);
      --text: #e5e7eb;
      --muted: #94a3b8;
      --green: #22c55e;
      --green-soft: rgba(34, 197, 94, 0.15);
      --yellow: #f59e0b;
      --yellow-soft: rgba(245, 158, 11, 0.15);
      --red: #ef4444;
      --red-soft: rgba(239, 68, 68, 0.15);
      --blue: #60a5fa;
      --shadow: 0 24px 80px rgba(0, 0, 0, 0.35);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(96, 165, 250, 0.15), transparent 28%),
        radial-gradient(circle at top right, rgba(34, 197, 94, 0.14), transparent 24%),
        linear-gradient(180deg, var(--bg), var(--bg2));
      font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    }}
    .wrap {{
      max-width: 1320px;
      margin: 0 auto;
      padding: 32px 24px 48px;
    }}
    .hero {{
      background: linear-gradient(180deg, rgba(15, 23, 42, 0.95), rgba(15, 23, 42, 0.75));
      border: 1px solid var(--panel-border);
      border-radius: 24px;
      padding: 28px;
      box-shadow: var(--shadow);
    }}
    .eyebrow {{
      text-transform: uppercase;
      letter-spacing: 0.18em;
      color: var(--muted);
      font-size: 0.74rem;
      margin-bottom: 8px;
    }}
    h1 {{
      margin: 0 0 10px;
      font-size: clamp(2rem, 4vw, 3rem);
    }}
    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px 24px;
      color: var(--muted);
      font-size: 0.95rem;
    }}
    .meta strong {{ color: var(--text); font-weight: 600; }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 16px;
      margin-top: 22px;
    }}
    .card {{
      background: rgba(15, 23, 42, 0.82);
      border: 1px solid var(--panel-border);
      border-radius: 18px;
      padding: 16px 18px;
      box-shadow: var(--shadow);
    }}
    .card .label {{
      color: var(--muted);
      font-size: 0.78rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      margin-bottom: 8px;
    }}
    .card .value {{
      font-size: 1.2rem;
      font-weight: 700;
      line-height: 1.3;
    }}
    .card.green {{ border-color: rgba(34, 197, 94, 0.35); background: var(--green-soft); }}
    .card.yellow {{ border-color: rgba(245, 158, 11, 0.35); background: var(--yellow-soft); }}
    .card.red {{ border-color: rgba(239, 68, 68, 0.35); background: var(--red-soft); }}
    section {{
      margin-top: 26px;
      background: var(--panel);
      border: 1px solid var(--panel-border);
      border-radius: 24px;
      padding: 22px;
      box-shadow: var(--shadow);
    }}
    section h2 {{
      margin: 0 0 16px;
      font-size: 1.15rem;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      overflow: hidden;
    }}
    thead th {{
      text-align: left;
      color: var(--muted);
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      border-bottom: 1px solid rgba(148, 163, 184, 0.15);
      padding: 12px 10px;
    }}
    tbody td {{
      padding: 12px 10px;
      border-bottom: 1px solid rgba(148, 163, 184, 0.08);
      vertical-align: top;
    }}
    tbody tr.green {{ background: rgba(34, 197, 94, 0.05); }}
    tbody tr.yellow {{ background: rgba(245, 158, 11, 0.06); }}
    tbody tr.red {{ background: rgba(239, 68, 68, 0.06); }}
    code {{
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
      font-size: 0.88rem;
      color: #dbeafe;
      white-space: pre-wrap;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 0.25rem 0.55rem;
      border-radius: 999px;
      font-size: 0.72rem;
      font-weight: 700;
      letter-spacing: 0.08em;
    }}
    .badge.green {{ background: rgba(34, 197, 94, 0.15); color: #86efac; }}
    .badge.yellow {{ background: rgba(245, 158, 11, 0.18); color: #fcd34d; }}
    .badge.red {{ background: rgba(239, 68, 68, 0.18); color: #fca5a5; }}
    .muted {{ color: var(--muted); }}
    .errors {{
      margin: 0;
      padding-left: 1.2rem;
    }}
    .errors li {{
      margin: 0 0 10px;
      color: var(--text);
    }}
    .errors code {{
      color: #fecaca;
      margin-right: 8px;
    }}
    .footnote {{
      margin-top: 14px;
      color: var(--muted);
      font-size: 0.88rem;
    }}
    @media (max-width: 900px) {{
      table, thead, tbody, th, td, tr {{ font-size: 0.92rem; }}
      .hero {{ padding: 20px; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <div class="eyebrow">HawksTrade system dashboard</div>
      <h1>Linux Health Check</h1>
      <div class="meta">
        <div><strong>Generated</strong>: {generated_local} {generated_tz}</div>
        <div><strong>Template</strong>: {cron_name}</div>
        <div><strong>Cron file</strong>: {cron_file}</div>
        <div><strong>Window</strong>: {window_label}</div>
        <div><strong>Overall</strong>: <span class="badge {report.overall_status}">{html.escape(report.overall_status.upper())}</span></div>
      </div>
      <div class="cards">
        {''.join(f'<div class="card {status}"><div class="label">{html.escape(label)}</div><div class="value">{html.escape(value)}</div></div>' for label, status, value in cards)}
      </div>
    </div>

    <section>
      <h2>Cron Health</h2>
      <table>
        <thead>
          <tr>
            <th>Job</th>
            <th>Schedule</th>
            <th>Last Run</th>
            <th>Age</th>
            <th>Duration</th>
            <th>Missed</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {''.join(job_rows)}
        </tbody>
      </table>
    </section>

    <section>
      <h2>Portfolio Health</h2>
      <p class="muted">Alpaca connectivity: <strong>{'Connected' if report.alpaca.connected else 'Disconnected'}</strong></p>
      <p class="muted">Portfolio value: <strong>{esc(_fmt_money(report.alpaca.portfolio_value))}</strong> | Cash: <strong>{esc(_fmt_money(report.alpaca.cash))}</strong> | Buying power: <strong>{esc(_fmt_money(report.alpaca.buying_power))}</strong></p>
      <p class="muted">Closed trades: <strong>{esc(report.trade_summary.get('total_trades', 0))}</strong> | Realized P/L: <strong>{esc(_fmt_money(report.trade_summary.get('realized_pnl_dollars')))} ({esc(_fmt_pct(report.trade_summary.get('realized_pnl_pct')))})</strong> | Unrealized P/L: <strong>{esc(_fmt_money(report.trade_summary.get('unrealized_pnl_dollars')))}</strong></p>
      <p class="muted">Open positions: <strong>{esc(report.alpaca.open_position_count)}</strong></p>
      <table>
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Qty</th>
            <th>Entry</th>
            <th>Current</th>
            <th>P/L $</th>
            <th>P/L %</th>
          </tr>
        </thead>
        <tbody>
          {''.join(position_rows) if position_rows else '<tr><td colspan="6" class="muted">No open positions detected.</td></tr>'}
        </tbody>
      </table>
    </section>

    <section>
      <h2>Log Health</h2>
      <p class="muted">Errors in logs: <strong>{'YES' if report.log_errors else 'NO'}</strong> | Warnings: <strong>{warning_count}</strong></p>
      <ul class="errors">
        {''.join(error_items) if error_items else '<li class="muted">No error lines found in runtime logs.</li>'}
      </ul>
      <div class="footnote">The dashboard is generated from cron templates, runtime logs, and the current Alpaca snapshot when available.</div>
    </section>
  </div>
</body>
</html>
"""


def write_html_report(report: HealthReport) -> Path:
    report.html_output.parent.mkdir(parents=True, exist_ok=True)
    report.html_output.write_text(render_html_report(report), encoding="utf-8")
    return report.html_output


# ── Command line interface ───────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HawksTrade Linux health check")
    parser.add_argument(
        "--cron-template",
        default="auto",
        choices=["auto", "eastern", "pacific", "utc"],
        help="Cron template to compare against (default: auto-detect from local timezone)",
    )
    parser.add_argument(
        "--cron-file",
        help="Override the cron template file to inspect",
    )
    parser.add_argument(
        "--log-dir",
        default=str(LOG_DIR),
        help="Directory containing runtime logs",
    )
    parser.add_argument(
        "--hours",
        "--lookback-hours",
        dest="lookback_hours",
        type=float,
        default=4.0,
        help="How many hours back to inspect logs and expected cron runs (default: 4)",
    )
    parser.add_argument(
        "--lookback-days",
        dest="lookback_days",
        type=float,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--html-output",
        default=str(DEFAULT_HTML_OUTPUT),
        help="Where to write the HTML dashboard",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Legacy compatibility flag; terminal output now uses plain status tags",
    )
    parser.add_argument(
        "--force-color",
        action="store_true",
        help="Legacy compatibility flag; terminal output now uses plain status tags",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.lookback_days is not None:
        args.lookback_hours = float(args.lookback_days) * 24.0
    report = build_health_report(
        cron_template=args.cron_template,
        cron_file=args.cron_file,
        log_dir=args.log_dir,
        html_output=args.html_output,
        lookback_hours=args.lookback_hours,
    )
    terminal = format_terminal_report(report, use_color=False).replace("\r", "")
    terminal = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", terminal)
    print(terminal)
    output_path = write_html_report(report)
    print(f"\nHTML report written to: {output_path}")

    if report.overall_status == "red":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
