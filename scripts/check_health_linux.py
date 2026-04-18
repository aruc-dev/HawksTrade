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
import json
import os
import re
import shlex
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

from tracking.performance import compute_summary, load_closed_trades
from tracking.trade_log import get_open_trades

CONFIG_PATH = BASE_DIR / "config" / "config.yaml"
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CFG = yaml.safe_load(f)

LOG_DIR = BASE_DIR / CFG["reporting"]["logs_dir"]
REPORTS_DIR = BASE_DIR / CFG["reporting"]["reports_dir"]
DEFAULT_HTML_OUTPUT = REPORTS_DIR / "health_check_linux.html"
DEFAULT_PRICE_FAILURE_STATE_FILE = BASE_DIR / "data" / "price_fetch_failures.json"
DEFAULT_PRICE_FAILURE_ALERT_THRESHOLD = 3
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
    run_id: str | None = None
    script: str | None = None
    start_fields: dict[str, str] = field(default_factory=dict)
    end_fields: dict[str, str] = field(default_factory=dict)
    source_files: list[Path] = field(default_factory=list)

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
    evaluated_at: datetime | None = None
    latest_note: str | None = None

    @property
    def age(self) -> timedelta | None:
        if self.last_run_at is None:
            return None
        reference_time = self.evaluated_at
        if reference_time is None:
            reference_time = datetime.now().astimezone().replace(tzinfo=None)
        return reference_time - self.last_run_at


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


@dataclass(frozen=True)
class PriceFailureState:
    symbol: str
    price_symbol: str
    asset_class: str
    count: int
    threshold: int
    last_failed_at: str | None
    reason: str
    error_category: str
    retryable: bool
    status_code: int | None
    last_error: str

    @property
    def status(self) -> str:
        return "red" if self.count >= self.threshold else "yellow"


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
    price_failures: list[PriceFailureState]
    html_output: Path


# ── Cron parsing ─────────────────────────────────────────────────────────────


CRON_LINE_RE = re.compile(
    r"^(?P<minute>\S+)\s+(?P<hour>\S+)\s+(?P<dom>\S+)\s+(?P<month>\S+)\s+(?P<dow>\S+)\s+(?P<command>.+)$"
)

LOG_LINE_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) "
    r"\[(?P<level>[A-Z]+)\] (?P<logger>[^:]+): (?P<message>.*)$"
)

RUN_MARKER_RE = re.compile(r"^(?P<event>RUN_START|RUN_END)\s+(?P<fields>.*)$")
TRUTHY_VALUES = {"1", "true", "yes", "on", "y"}


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
    if "scheduler/run_scan.py" in normalized and "--stocks-only" in normalized:
        return "stock_scan", "Stock scan"
    if "scheduler/run_scan.py" in normalized and "--crypto-only" in normalized:
        return "crypto_scan", "Crypto scan"
    if "scheduler/run_scan.py" in normalized and "--stocks-only" not in normalized and "--crypto-only" not in normalized:
        return "full_scan", "Full scan"
    if "scheduler/run_risk_check.py" in normalized:
        return "risk_check", "Risk check"
    if "scheduler/run_report.py" in normalized and "--weekly" in normalized:
        return "weekly_report", "Weekly report"
    if "scheduler/run_report.py" in normalized and "--weekly" not in normalized:
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


def _parse_marker_fields(message: str) -> tuple[str, dict[str, str]] | None:
    match = RUN_MARKER_RE.match(message.strip())
    if not match:
        return None

    fields: dict[str, str] = {}
    raw_fields = match.group("fields").strip()
    if raw_fields:
        for token in shlex.split(raw_fields):
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            fields[key] = value
    return match.group("event"), fields


def _field_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in TRUTHY_VALUES


def _marker_job_info(script: str | None, fields: dict[str, str]) -> tuple[str, str]:
    script_name = (script or "").strip().lower()
    if script_name == "run_scan":
        scan_kind = (fields.get("scan_kind") or "").strip().lower()
        if scan_kind in {"stock", "stocks"}:
            return "stock_scan", "Stock scan"
        if scan_kind in {"crypto", "crypto_only"}:
            return "crypto_scan", "Crypto scan"
        if scan_kind in {"full", "full_scan", "combined"}:
            return "full_scan", "Full scan"

        run_stocks = _field_truthy(fields.get("run_stocks"))
        run_crypto = _field_truthy(fields.get("run_crypto"))
        if run_stocks and run_crypto:
            return "full_scan", "Full scan"
        if run_crypto and not run_stocks:
            return "crypto_scan", "Crypto scan"
        if run_stocks and not run_crypto:
            return "stock_scan", "Stock scan"
        return "scan_unknown", "Scan"

    if script_name == "run_risk_check":
        return "risk_check", "Risk check"

    if script_name == "run_report":
        report_kind = (fields.get("report_kind") or "").strip().lower()
        if report_kind == "weekly" or _field_truthy(fields.get("weekly")):
            return "weekly_report", "Weekly report"
        return "daily_report", "Daily report"

    if script_name:
        return script_name, script_name.replace("_", " ").title()
    return "unknown_run", "Run"


def _prefer_source_file(current: Path | None, candidate: Path) -> Path:
    if current is None:
        return candidate
    if current.name == "cron.log" and candidate.name != "cron.log":
        return candidate
    return current


def _append_unique_path(items: list[Path], path: Path) -> None:
    if path not in items:
        items.append(path)


def _append_unique_text(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def _merge_marker_record(
    records_by_id: dict[str, RunRecord],
    *,
    event: str,
    finding: LogFinding,
    fields: dict[str, str],
) -> None:
    run_id = fields.get("run_id")
    if not run_id:
        run_id = f"{fields.get('script', 'run')}-{finding.timestamp.strftime('%Y%m%d%H%M%S') if finding.timestamp else 'unknown'}-{finding.source_file.name}"

    script = fields.get("script")
    record = records_by_id.get(run_id)
    if record is None:
        job_key, label = _marker_job_info(script, fields)
        record = RunRecord(
            job_key=job_key,
            label=label,
            start_time=finding.timestamp or datetime.now(),
            end_time=None,
            success=False,
            source_file=finding.source_file,
            lines=[],
            notes=[],
            run_id=run_id,
            script=script,
        )
        records_by_id[run_id] = record
    else:
        if script and not record.script:
            record.script = script
        record.source_file = _prefer_source_file(record.source_file, finding.source_file)

    _append_unique_path(record.source_files, finding.source_file)
    record.lines.append(finding.raw)

    effective_fields = dict(record.start_fields)
    effective_fields.update(fields)
    job_key, label = _marker_job_info(record.script or script, effective_fields)
    record.job_key = job_key
    record.label = label

    if event == "RUN_START":
        if finding.timestamp is not None and finding.timestamp < record.start_time:
            record.start_time = finding.timestamp
        record.start_fields.update(fields)
        if record.script is None and script:
            record.script = script
        return

    if finding.timestamp is not None:
        if record.end_time is None or finding.timestamp > record.end_time:
            record.end_time = finding.timestamp
    record.end_fields.update(fields)
    status = (fields.get("status") or "").strip().lower()
    if status:
        record.success = status not in {"error", "failed", "failure"}
        if status != "ok":
            _append_unique_text(record.notes, f"marker status={status}")
    elif record.end_time is not None:
        record.success = True

    outcome = fields.get("outcome")
    if outcome and outcome != "completed":
        _append_unique_text(record.notes, f"outcome={outcome}")
    error_type = fields.get("error_type")
    if error_type:
        _append_unique_text(record.notes, f"error_type={error_type}")


def _split_structured_records(findings_by_file: dict[Path, list[LogFinding]]) -> list[RunRecord]:
    records_by_id: dict[str, RunRecord] = {}
    for path in sorted(findings_by_file):
        for finding in findings_by_file[path]:
            if finding.logger not in {"run_scan", "run_risk_check", "run_report"}:
                continue
            parsed = _parse_marker_fields(finding.message)
            if parsed is None:
                continue
            event, fields = parsed
            if event not in {"RUN_START", "RUN_END"}:
                continue
            _merge_marker_record(records_by_id, event=event, finding=finding, fields=fields)

    records = sorted(
        records_by_id.values(),
        key=lambda record: (record.start_time, record.run_id or "", record.label),
    )
    for record in records:
        if any("[ERROR]" in line or "Traceback" in line for line in record.lines):
            _append_unique_text(record.notes, "error in run")
    return records


def _read_log_lines(path: Path) -> list[LogFinding]:
    findings: list[LogFinding] = []
    if not path.exists():
        return findings
    in_traceback = False
    last_timestamp: datetime | None = None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            finding = _parse_log_line(raw, path)
            if finding is not None:
                findings.append(finding)
                last_timestamp = finding.timestamp
                in_traceback = False
                continue

            raw_line = raw.rstrip("\n")
            stripped = raw_line.strip()
            if not stripped:
                in_traceback = False
                continue

            lower = stripped.lower()
            if "traceback (most recent call last):" in lower:
                in_traceback = True
            if not in_traceback and not any(token in lower for token in ("exception", "error", "traceback")):
                continue

            findings.append(
                LogFinding(
                    timestamp=last_timestamp,
                    level="ERROR",
                    logger="traceback",
                    message=raw_line,
                    source_file=path,
                    raw=raw_line,
                )
            )
    return findings


def _find_matching_error_lines(
    findings_by_file: dict[Path, list[LogFinding]],
    since: datetime | None = None,
) -> tuple[list[LogFinding], list[LogFinding]]:
    errors_by_sig: dict[tuple[datetime | None, str, str, str], LogFinding] = {}
    warnings_by_sig: dict[tuple[datetime | None, str, str, str], LogFinding] = {}

    def _prefer_candidate(existing: LogFinding, candidate: LogFinding) -> bool:
        if existing.source_file.name == "cron.log" and candidate.source_file.name != "cron.log":
            return True
        return False

    for path in findings_by_file:
        for finding in findings_by_file[path]:
            if since is not None:
                if finding.timestamp is None or finding.timestamp < since:
                    continue
            msg = finding.message.lower()
            signature = (finding.timestamp, finding.level, finding.logger, finding.message)
            if finding.level == "ERROR" or "traceback" in msg:
                existing = errors_by_sig.get(signature)
                if existing is None or _prefer_candidate(existing, finding):
                    errors_by_sig[signature] = finding
            elif finding.level == "WARNING":
                existing = warnings_by_sig.get(signature)
                if existing is None or _prefer_candidate(existing, finding):
                    warnings_by_sig[signature] = finding
    errors = list(errors_by_sig.values())
    warnings = list(warnings_by_sig.values())
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


def _split_scan_records(findings_by_file: dict[Path, list[LogFinding]]) -> list[RunRecord]:
    records: list[RunRecord] = []
    for path in sorted(findings_by_file):
        if not path.name.startswith("scan_"):
            continue
        current: RunRecord | None = None
        for finding in findings_by_file[path]:
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


def _split_risk_records(findings_by_file: dict[Path, list[LogFinding]]) -> list[RunRecord]:
    records: list[RunRecord] = []
    for path in sorted(findings_by_file):
        if not path.name.startswith("risk_"):
            continue
        current: RunRecord | None = None
        for finding in findings_by_file[path]:
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


def _split_report_records(findings_by_file: dict[Path, list[LogFinding]]) -> list[RunRecord]:
    records: list[RunRecord] = []
    for path in sorted(findings_by_file):
        if not path.name.startswith("report_"):
            continue
        current: RunRecord | None = None
        for finding in findings_by_file[path]:
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


def load_runtime_records(log_dir: Path) -> dict[str, object]:
    files = _log_files(log_dir)
    findings_by_file = {path: _read_log_lines(path) for path in files}
    structured_records = _split_structured_records(findings_by_file)
    structured_files = {
        path
        for path, findings in findings_by_file.items()
        if any(_parse_marker_fields(finding.message) is not None for finding in findings)
    }
    legacy_findings = {path: findings for path, findings in findings_by_file.items() if path not in structured_files}
    legacy_scan_records = _split_scan_records(legacy_findings)
    legacy_risk_records = _split_risk_records(legacy_findings)
    legacy_report_records = _split_report_records(legacy_findings)

    structured_scan_records = [r for r in structured_records if r.job_key in {"stock_scan", "full_scan", "crypto_scan", "scan_unknown"}]
    structured_risk_records = [r for r in structured_records if r.job_key == "risk_check"]
    structured_daily_report_records = [r for r in structured_records if r.job_key == "daily_report"]
    structured_weekly_report_records = [r for r in structured_records if r.job_key == "weekly_report"]
    def sort_key(record: RunRecord) -> tuple[datetime, str, str]:
        return record.start_time, record.run_id or "", record.source_file.name

    return {
        "scan": sorted(structured_scan_records + legacy_scan_records, key=sort_key),
        "risk_check": sorted(structured_risk_records + legacy_risk_records, key=sort_key),
        "daily_report": sorted(structured_daily_report_records + [r for r in legacy_report_records if r.job_key == "daily_report"], key=sort_key),
        "weekly_report": sorted(structured_weekly_report_records + [r for r in legacy_report_records if r.job_key == "weekly_report"], key=sort_key),
        "all_files": files,
        "findings_by_file": findings_by_file,
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


def _expected_runs_for_jobs(jobs: list[CronJob], start: datetime, end: datetime) -> list[datetime]:
    expected: list[datetime] = []
    for job in jobs:
        expected.extend(_generate_expected_times(job.pattern, start, end))
    return sorted(set(expected))


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


def _build_job_health(
    *,
    key: str,
    label: str,
    schedule_lines: list[str],
    jobs_for_key: list[CronJob],
    recent_records: list[RunRecord],
    all_records: list[RunRecord],
    expected_runs: list[datetime],
    lookback_hours: float,
    evaluated_at: datetime,
) -> JobHealth:
    latest_recent = recent_records[-1] if recent_records else None
    latest_any = all_records[-1] if all_records else None
    latest_visible = latest_recent or latest_any
    last_success = max((record.start_time for record in recent_records if record.success), default=None)
    if last_success is None:
        last_success = max((record.start_time for record in all_records if record.success), default=None)
    if expected_runs:
        if latest_recent is None:
            latest_note = f"No run in last {lookback_hours:g} hours"
        else:
            latest_note = _latest_note(latest_visible)
    else:
        latest_note = _latest_note(latest_visible)
    missed, _ = _match_expected_runs(expected_runs, [r.start_time for r in recent_records], _tolerance_minutes(jobs_for_key))
    return JobHealth(
        key=key,
        label=label,
        schedule_lines=schedule_lines,
        last_run_at=latest_visible.start_time if latest_visible else None,
        last_success_at=last_success,
        last_duration=latest_visible.duration if latest_visible else None,
        missed_runs=missed,
        expected_runs=len(expected_runs),
        status=_job_status(latest_recent, missed, len(expected_runs)),
        evaluated_at=evaluated_at,
        latest_note=latest_note,
    )


def _combined_scan_health(
    *,
    full_jobs: list[CronJob],
    crypto_jobs: list[CronJob],
    scan_records: list[RunRecord],
    window_start: datetime,
    now: datetime,
    lookback_hours: float,
) -> dict[str, JobHealth] | None:
    full_expected = _expected_runs_for_jobs(full_jobs, window_start, now)
    crypto_expected = _expected_runs_for_jobs(crypto_jobs, window_start, now)
    if not full_expected or not crypto_expected:
        return None

    combined_jobs = [*full_jobs, *crypto_jobs]
    combined_expected = sorted(set(full_expected).union(crypto_expected))
    all_combined_records = sorted(
        [record for record in scan_records if record.start_time >= window_start],
        key=lambda item: item.start_time,
    )
    recent_combined_records = [record for record in all_combined_records if record.start_time >= window_start]

    return {
        "full_scan": _build_job_health(
            key="full_scan",
            label=full_jobs[0].label if full_jobs else "Full scan",
            schedule_lines=[_human_cron_line(job.pattern) for job in full_jobs],
            jobs_for_key=combined_jobs,
            recent_records=recent_combined_records,
            all_records=all_combined_records,
            expected_runs=combined_expected,
            lookback_hours=lookback_hours,
            evaluated_at=now,
        ),
        "crypto_scan": _build_job_health(
            key="crypto_scan",
            label=crypto_jobs[0].label if crypto_jobs else "Crypto scan",
            schedule_lines=[_human_cron_line(job.pattern) for job in crypto_jobs],
            jobs_for_key=combined_jobs,
            recent_records=recent_combined_records,
            all_records=all_combined_records,
            expected_runs=combined_expected,
            lookback_hours=lookback_hours,
            evaluated_at=now,
        ),
    }


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

    combined_scan_health = None
    full_jobs = grouped.get("full_scan", [])
    crypto_jobs = grouped.get("crypto_scan", [])
    if full_jobs and crypto_jobs:
        scan_records = [record for record in records if record.job_key in {"stock_scan", "full_scan", "crypto_scan", "scan_unknown"}]
        combined_scan_health = _combined_scan_health(
            full_jobs=full_jobs,
            crypto_jobs=crypto_jobs,
            scan_records=scan_records,
            window_start=window_start,
            now=now,
            lookback_hours=lookback_hours,
        )

    for key, jobs_for_key in grouped.items():
        if combined_scan_health and key in combined_scan_health:
            health_rows.append(combined_scan_health[key])
            continue

        all_expected: list[datetime] = []
        for job in jobs_for_key:
            all_expected.extend(_generate_expected_times(job.pattern, window_start, now))
        all_expected = sorted(set(all_expected))
        key_records = sorted(by_key.get(key, []), key=lambda item: item.start_time)
        recent_records = [record for record in key_records if record.start_time >= window_start]
        health_rows.append(
            _build_job_health(
                key=key,
                label=jobs_for_key[0].label if jobs_for_key else key,
                schedule_lines=[_human_cron_line(job.pattern) for job in jobs_for_key],
                jobs_for_key=jobs_for_key,
                recent_records=recent_records,
                all_records=key_records,
                expected_runs=all_expected,
                lookback_hours=lookback_hours,
                evaluated_at=now,
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
        from core import alpaca_client as ac
    except Exception as exc:  # pragma: no cover - exercised in broken deployments
        return AlpacaState(
            connected=False,
            account_error=str(exc),
            positions_error=None,
            portfolio_value=None,
            cash=None,
            buying_power=None,
            broker_positions=[],
            trade_log_open_rows=trade_log_open_rows,
        )

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


def _price_failure_default_threshold() -> int:
    raw = os.getenv("HAWKSTRADE_PRICE_FAILURE_ALERT_THRESHOLD")
    if raw is None:
        return DEFAULT_PRICE_FAILURE_ALERT_THRESHOLD
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_PRICE_FAILURE_ALERT_THRESHOLD


def _int_or_none(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def load_price_failure_state(path: str | Path = DEFAULT_PRICE_FAILURE_STATE_FILE) -> list[PriceFailureState]:
    state_path = Path(path)
    if not state_path.exists():
        return []
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        return [
            PriceFailureState(
                symbol=str(state_path),
                price_symbol="",
                asset_class="state_file",
                count=1,
                threshold=1,
                last_failed_at=None,
                reason="state_file_unreadable",
                error_category="configuration",
                retryable=False,
                status_code=None,
                last_error=f"Could not read price failure state file: {state_path}",
            )
        ]

    if not isinstance(state, dict):
        return []

    default_threshold = _int_or_none(state.get("threshold")) or _price_failure_default_threshold()
    symbols = state.get("symbols")
    if not isinstance(symbols, dict):
        return []

    failures: list[PriceFailureState] = []
    for key, raw_entry in sorted(symbols.items()):
        if not isinstance(raw_entry, dict):
            continue
        count = _int_or_none(raw_entry.get("count")) or 0
        if count <= 0:
            continue
        threshold = _int_or_none(raw_entry.get("threshold")) or default_threshold
        failures.append(
            PriceFailureState(
                symbol=str(raw_entry.get("symbol") or key),
                price_symbol=str(raw_entry.get("price_symbol") or ""),
                asset_class=str(raw_entry.get("asset_class") or ""),
                count=count,
                threshold=max(1, threshold),
                last_failed_at=raw_entry.get("last_failed_at"),
                reason=str(raw_entry.get("reason") or ""),
                error_category=str(raw_entry.get("error_category") or ""),
                retryable=bool(raw_entry.get("retryable")),
                status_code=_int_or_none(raw_entry.get("status_code")),
                last_error=str(raw_entry.get("last_error") or ""),
            )
        )
    return failures


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
    price_failure_state_file: str | Path = DEFAULT_PRICE_FAILURE_STATE_FILE,
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

    findings_by_file = runtime["findings_by_file"]
    window_start = now - timedelta(hours=lookback_hours)
    errors, warnings = _find_matching_error_lines(findings_by_file, since=window_start)
    price_failures = load_price_failure_state(price_failure_state_file)

    overall = _overall_status(alpaca_state, job_health, errors, price_failures)

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
        price_failures=price_failures,
        html_output=Path(html_output).expanduser().resolve(),
    )


def _overall_status(
    alpaca_state: AlpacaState,
    job_health: list[JobHealth],
    errors: list[LogFinding],
    price_failures: list[PriceFailureState],
) -> str:
    if not alpaca_state.connected:
        return "red"
    if errors:
        return "red"
    if any(failure.status == "red" for failure in price_failures):
        return "red"
    if any(job.status == "red" for job in job_health):
        return "red"
    if any(failure.status == "yellow" for failure in price_failures):
        return "yellow"
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


def _count_value(value: int, *, enabled: bool = False, nonzero_status: str = "red") -> str:
    status = "green" if value == 0 else nonzero_status
    return f"{value} {_severity_label(status, enabled)}"


def _display_missed_runs(job_health: list[JobHealth]) -> int:
    combined_keys = {"full_scan", "crypto_scan"}
    scan_jobs = [job for job in job_health if job.key in combined_keys]
    other_jobs = [job for job in job_health if job.key not in combined_keys]
    return max((job.missed_runs for job in scan_jobs), default=0) + sum(job.missed_runs for job in other_jobs)


def _recent_findings(findings: list[LogFinding], limit: int = 10) -> list[LogFinding]:
    ordered = sorted(findings, key=lambda item: (item.timestamp or datetime.min, item.source_file.name, item.logger))
    if limit <= 0:
        return ordered[::-1]
    return ordered[-limit:][::-1]


def _format_finding_entry(finding: LogFinding) -> str:
    timestamp = _fmt_timestamp(finding.timestamp)
    return f"{timestamp} | {finding.source_file.name} | {finding.level} | {finding.logger} | {finding.message}"


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
    lines.append("Full scan and Crypto scan are evaluated together as one hourly cycle when both are scheduled.")
    lines.append("")

    issues = [job for job in report.job_health if job.status != "green"]
    price_failure_issues = [failure for failure in report.price_failures if failure.status != "green"]
    if issues or price_failure_issues:
        lines.append("ISSUE SUMMARY")
        for job in issues:
            reason_bits = []
            if job.missed_runs:
                reason_bits.append(f"{job.missed_runs} missed run(s)")
            if job.latest_note:
                reason_bits.append(job.latest_note)
            reason = "; ".join(reason_bits) if reason_bits else "status requires attention"
            lines.append(f"  - [NOK] {job.label}: {reason}")
        for failure in price_failure_issues:
            label = _severity_label(failure.status)
            lines.append(
                f"  - {label} Price fetch {failure.symbol}: "
                f"{failure.count}/{failure.threshold} consecutive failure(s)"
            )
        lines.append("")

    lines.append("PORTFOLIO")
    if report.alpaca.connected:
        lines.append("Alpaca connectivity : OK [OK]")
    else:
        lines.append("Alpaca connectivity : FAILED [NOK]")
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

    lines.append("PRICE FETCH HEALTH")
    red_price_failures = [failure for failure in report.price_failures if failure.status == "red"]
    yellow_price_failures = [failure for failure in report.price_failures if failure.status == "yellow"]
    if red_price_failures:
        price_failure_status = "YES [NOK]"
    elif yellow_price_failures:
        price_failure_status = "YES [WARN]"
    else:
        price_failure_status = "NO [OK]"
    count_status = "red" if red_price_failures else "yellow" if yellow_price_failures else "green"
    lines.append(
        f"Repeated price failures : {price_failure_status} "
        f"({len(report.price_failures)} {_severity_label(count_status)})"
    )
    if report.price_failures:
        lines.append(
            f"{'Symbol':<12} {'Price Source':<14} {'Asset':<8} {'Count':>7} "
            f"{'Last Failure':<22} {'Category':<16} {'State':>8}"
        )
        lines.append("-" * 96)
        for failure in report.price_failures:
            source = failure.price_symbol or failure.symbol
            category = failure.error_category or failure.reason or "unknown"
            lines.append(
                f"{failure.symbol:<12} {source:<14} {failure.asset_class:<8} "
                f"{failure.count:>3}/{failure.threshold:<3} "
                f"{(failure.last_failed_at or 'N/A')[:22]:<22} {category[:16]:<16} "
                f"{_severity_label(failure.status):>8}"
            )
    else:
        lines.append("No consecutive price-fetch failures tracked.")
    lines.append("")

    lines.append("LOG HEALTH")
    errors_yesno = "YES [NOK]" if report.log_errors else "NO [OK]"
    warnings_yesno = "YES [WARN]" if report.log_warnings else "NO [OK]"
    error_count_display = _count_value(len(report.log_errors))
    warning_count_display = _count_value(len(report.log_warnings), nonzero_status="yellow")
    lines.append(f"Errors in logs     : {errors_yesno} ({error_count_display})")
    lines.append(f"Warnings in logs   : {warnings_yesno} ({warning_count_display})")
    lines.append("")
    lines.append("TROUBLESHOOTING")
    lines.append("Latest errors:")
    for finding in _recent_findings(report.log_errors, limit=10):
        lines.append(f"  {_format_finding_entry(finding)}")
    if not report.log_errors:
        lines.append("  None in window.")
    lines.append("Latest warnings:")
    for finding in _recent_findings(report.log_warnings, limit=10):
        lines.append(f"  {_format_finding_entry(finding)}")
    if not report.log_warnings:
        lines.append("  None in window.")
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
        ("Log Warnings", "yellow" if report.log_warnings else "green", f"{len(report.log_warnings)}"),
        (
            "Price Failures",
            "red" if any(item.status == "red" for item in report.price_failures)
            else "yellow" if report.price_failures else "green",
            str(len(report.price_failures)),
        ),
        ("Missed Runs", "red" if any(job.missed_runs > 0 for job in report.job_health) else "green", str(_display_missed_runs(report.job_health))),
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

    price_failure_rows = []
    for failure in report.price_failures:
        source = failure.price_symbol or failure.symbol
        category = failure.error_category or failure.reason or "unknown"
        price_failure_rows.append(
            f"""
            <tr class="{failure.status}">
              <td><code>{esc(failure.symbol)}</code></td>
              <td><code>{esc(source)}</code></td>
              <td>{esc(failure.asset_class)}</td>
              <td>{failure.count}/{failure.threshold}</td>
              <td><code>{esc(failure.last_failed_at or 'N/A')}</code></td>
              <td>{esc(category)}</td>
              <td>{badge(failure.status)}</td>
            </tr>
            """
        )

    def finding_rows(findings: list[LogFinding], *, row_class: str, empty_message: str) -> str:
        recent = _recent_findings(findings, limit=10)
        if not recent:
            return f'<tr><td colspan="4" class="muted">{esc(empty_message)}</td></tr>'
        rows = []
        for finding in recent:
            rows.append(
                f"""
                <tr class="{row_class}">
                  <td><code>{esc(_fmt_timestamp(finding.timestamp))}</code></td>
                  <td><code>{esc(finding.source_file.name)}</code></td>
                  <td><code>{esc(finding.logger)}</code></td>
                  <td>{esc(finding.message)}</td>
                </tr>
                """
            )
        return "".join(rows)

    error_rows = finding_rows(report.log_errors, row_class="red", empty_message="No error lines found in runtime logs.")
    warning_rows = finding_rows(report.log_warnings, row_class="yellow", empty_message="No warning lines found in runtime logs.")
    price_failure_status = (
        "NOK"
        if any(failure.status == "red" for failure in report.price_failures)
        else "WARN" if report.price_failures else "OK"
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
    .trouble-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 16px;
    }}
    .finding-panel {{
      background: rgba(15, 23, 42, 0.72);
      border: 1px solid var(--panel-border);
      border-radius: 18px;
      padding: 16px;
    }}
    .finding-panel h3 {{
      margin: 0 0 12px;
      font-size: 1rem;
    }}
    .finding-panel.red {{
      border-color: rgba(239, 68, 68, 0.32);
      background: rgba(239, 68, 68, 0.08);
    }}
    .finding-panel.yellow {{
      border-color: rgba(245, 158, 11, 0.32);
      background: rgba(245, 158, 11, 0.08);
    }}
    .finding-table thead th {{
      font-size: 0.72rem;
    }}
    .finding-table td {{
      font-size: 0.9rem;
    }}
    .finding-table td:last-child {{
      word-break: break-word;
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
      <h2>Price Fetch Health</h2>
      <p class="muted">Consecutive latest-price failures: <strong>{esc(price_failure_status)}</strong></p>
      <table>
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Price Source</th>
            <th>Asset</th>
            <th>Count</th>
            <th>Last Failure</th>
            <th>Category</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {''.join(price_failure_rows) if price_failure_rows else '<tr><td colspan="7" class="muted">No consecutive price-fetch failures tracked.</td></tr>'}
        </tbody>
      </table>
    </section>

    <section>
      <h2>Troubleshooting</h2>
      <p class="muted">Errors in logs: <strong>{'YES' if report.log_errors else 'NO'}</strong> | Warnings: <strong>{warning_count}</strong></p>
      <div class="trouble-grid">
        <div class="finding-panel red">
          <h3>Errors</h3>
          <table class="finding-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>File</th>
                <th>Logger</th>
                <th>Message</th>
              </tr>
            </thead>
            <tbody>
              {error_rows}
            </tbody>
          </table>
        </div>
        <div class="finding-panel yellow">
          <h3>Warnings</h3>
          <table class="finding-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>File</th>
                <th>Logger</th>
                <th>Message</th>
              </tr>
            </thead>
            <tbody>
              {warning_rows}
            </tbody>
          </table>
        </div>
      </div>
      <div class="footnote">The dashboard is generated from cron templates, runtime logs, and the current Alpaca snapshot when available. Full scan and Crypto scan are evaluated together as one hourly cycle when both are scheduled.</div>
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
