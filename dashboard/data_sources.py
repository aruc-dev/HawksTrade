"""Data source readers for the dashboard.

Reads local files produced by the bot — trades.csv, daily_loss_baseline.json,
logs/, and health snapshots in reports/health_snapshots. All file reads are
read-only by OS permissions (the hawkstrade-dash user has r-only on data/).
"""
from __future__ import annotations

import csv
import json
import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from dashboard.config import cfg

log = logging.getLogger("dashboard.data_sources")

NY_TZ = ZoneInfo("America/New_York")
ACTIVE_ENTRY_STATUSES = {"open", "partially_filled"}
LOG_ISSUE_RE = re.compile(r"\b(CRITICAL|ERROR|WARNING|WARN)\b")
MAX_LOG_ISSUES = 20
HEALTH_SNAPSHOT_NAME_RE = re.compile(r"^health_\d{8}T\d{6}\.json$")


def _parse_iso(ts: str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp string; return None on failure."""
    if not ts:
        return None
    ts = str(ts).strip()
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        # Python's fromisoformat handles the standard format used by trade_log.
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _ny_date(dt: datetime) -> str:
    """Return the America/New_York date string for a given timestamp."""
    return _to_utc(dt).astimezone(NY_TZ).date().isoformat()


def _symbol_key(symbol: Any) -> str:
    """Normalize stock and crypto symbols for joining broker rows to trade-log rows."""
    return re.sub(r"[^A-Z0-9]", "", str(symbol or "").upper())


def enrich_positions_with_trade_metadata(
    positions: Iterable[Dict[str, Any]],
    trade_rows: Iterable[Dict[str, Any]],
    now_utc: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Add strategy and hold-days fields to broker positions from open trade rows.

    Alpaca is the source of truth for live quantity and P&L. The trade log is
    only used as read-only metadata for the strategy name and entry timestamp.
    """
    now_utc = _to_utc(now_utc or datetime.now(timezone.utc))
    latest_open_by_symbol: Dict[str, tuple[datetime, Dict[str, Any]]] = {}
    fallback_dt = datetime.min.replace(tzinfo=timezone.utc)

    for row in trade_rows:
        status = (row.get("status") or "").strip().lower()
        side = (row.get("side") or "").strip().lower()
        if status not in ACTIVE_ENTRY_STATUSES or side != "buy":
            continue
        key = _symbol_key(row.get("symbol"))
        if not key:
            continue
        entry_dt = _parse_iso(str(row.get("timestamp") or "")) or fallback_dt
        entry_dt = _to_utc(entry_dt)
        current = latest_open_by_symbol.get(key)
        if current is None or entry_dt > current[0]:
            latest_open_by_symbol[key] = (entry_dt, row)

    enriched: List[Dict[str, Any]] = []
    for position in positions:
        item = dict(position)
        row_info = latest_open_by_symbol.get(_symbol_key(item.get("symbol")))
        item.setdefault("strategy", "unknown")
        item.setdefault("hold_days", None)
        item.setdefault("entry_timestamp", None)
        item.setdefault("trade_log_symbol", None)
        if row_info is not None:
            entry_dt, row = row_info
            strategy = (row.get("strategy") or "unknown").strip() or "unknown"
            hold_days = max(0.0, (now_utc - entry_dt).total_seconds() / 86400)
            item["strategy"] = strategy
            item["hold_days"] = round(hold_days, 2)
            item["entry_timestamp"] = entry_dt.isoformat(timespec="seconds")
            item["trade_log_symbol"] = row.get("symbol")
        enriched.append(item)
    return enriched


def read_trades(trade_log_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Read all rows from trades.csv. Returns [] if file is missing or unreadable."""
    path = trade_log_path or cfg().trade_log_path
    if not path.exists():
        return []
    try:
        with open(path, "r", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except Exception as e:
        log.warning("Could not read trade log at %s: %s", path, e)
        return []
    return rows


def split_trades_by_status(rows: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Partition rows into {'open': [...], 'closed': [...], 'other': [...]}."""
    buckets: Dict[str, List[Dict[str, Any]]] = {"open": [], "closed": [], "other": []}
    for r in rows:
        status = (r.get("status") or "").strip().lower()
        if status in ("open", "partially_filled"):
            buckets["open"].append(r)
        elif status == "closed":
            buckets["closed"].append(r)
        else:
            buckets["other"].append(r)
    return buckets


def trades_closed_on_ny_date(
    rows: Iterable[Dict[str, Any]],
    ny_date_str: str,
) -> List[Dict[str, Any]]:
    """Filter closed trades whose timestamp falls on the given NY-session date.

    The bot writes the closing-side row with the exit timestamp in
    trade_log.py (see mark_trade_closed). So for closed rows where side='sell',
    the 'timestamp' column IS the exit time.
    """
    out: List[Dict[str, Any]] = []
    for r in rows:
        status = (r.get("status") or "").strip().lower()
        side = (r.get("side") or "").strip().lower()
        if status != "closed" or side != "sell":
            continue
        dt = _parse_iso(r.get("timestamp", ""))
        if dt is None:
            continue
        if _ny_date(dt) == ny_date_str:
            out.append(r)
    return out


def read_daily_baseline(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Read the daily loss baseline JSON. Returns None if missing/invalid."""
    p = path or cfg().daily_baseline_path
    if not p.exists():
        return None
    try:
        with open(p, "r") as f:
            data = json.load(f)
        # Minimal validation: require the keys used elsewhere.
        if not isinstance(data, dict) or "portfolio_value" not in data or "date" not in data:
            return None
        return data
    except Exception as e:
        log.warning("Could not read daily baseline at %s: %s", p, e)
        return None


def read_recent_log_lines(log_file: Path, max_lines: int = 50) -> List[str]:
    """Return the last N lines of a log file (not streaming — acceptable for
    periodic polling of 50-line tails)."""
    if not log_file.exists():
        return []
    try:
        with open(log_file, "r", errors="replace") as f:
            lines = f.readlines()
        return [ln.rstrip("\n") for ln in lines[-max_lines:]]
    except Exception as e:
        log.warning("Could not read log %s: %s", log_file, e)
        return []


def read_recent_log_issues(
    logs_dir: Optional[Path] = None,
    max_lines_per_file: int = 80,
    max_issues: int = MAX_LOG_ISSUES,
) -> List[Dict[str, str]]:
    """Return recent WARNING/ERROR/CRITICAL lines from runtime logs.

    The dashboard excludes its own access logs so normal 401/redirect traffic
    does not mark trading health as degraded.
    """
    root = logs_dir or cfg().logs_dir
    if not root.exists() or not root.is_dir():
        return []

    candidates = [
        path for path in root.glob("*.log")
        if path.is_file() and not path.name.startswith("dashboard_access_")
    ]
    candidates.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)

    issues: List[Dict[str, str]] = []
    for path in candidates[:10]:
        for line in read_recent_log_lines(path, max_lines=max_lines_per_file):
            match = LOG_ISSUE_RE.search(line)
            if not match:
                continue
            level = match.group(1)
            if level == "WARN":
                level = "WARNING"
            issues.append({
                "file": path.name,
                "level": level,
                "line": line,
            })
            if len(issues) >= max_issues:
                return issues
    return issues


def latest_health_snapshot_path(snapshot_dir: Optional[Path] = None) -> Optional[Path]:
    """Return the newest health snapshot JSON path, or None if unavailable."""
    root = snapshot_dir or cfg().health_snapshot_dir
    if not root.exists() or not root.is_dir():
        return None
    candidates = [
        path for path in root.iterdir()
        if path.is_file() and HEALTH_SNAPSHOT_NAME_RE.match(path.name)
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: path.name)[-1]


def read_latest_health_snapshot(snapshot_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Read the newest JSON health snapshot produced by check_health_linux.py."""
    result: Dict[str, Any] = {
        "ok": False,
        "path": None,
        "data": None,
        "error": None,
    }
    path = latest_health_snapshot_path(snapshot_dir)
    if path is None:
        root = snapshot_dir or cfg().health_snapshot_dir
        result["error"] = f"No health snapshot JSON found in {root}"
        return result

    result["path"] = str(path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception as exc:
        result["error"] = f"Could not read health snapshot at {path}: {exc}"
        return result

    if not isinstance(payload, dict):
        result["error"] = f"Health snapshot at {path} does not contain a JSON object"
        return result

    result["ok"] = True
    result["data"] = payload
    return result


def run_check_systemd(timeout_sec: int = 10) -> Dict[str, Any]:
    """Invoke scripts/check_systemd.sh and return parsed output.

    Safe wrapper: never raises, always returns a dict. If the script is missing
    or non-executable, returns a graceful error shape the UI can render.
    """
    script = cfg().check_systemd_script
    result: Dict[str, Any] = {
        "ok": False,
        "stdout": "",
        "stderr": "",
        "returncode": None,
        "error": None,
    }
    if not script.exists():
        result["error"] = f"check_systemd.sh not found at {script}"
        return result
    if not script.is_file():
        result["error"] = f"{script} is not a regular file"
        return result
    try:
        proc = subprocess.run(
            ["/usr/bin/env", "bash", str(script)],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        result["ok"] = proc.returncode == 0
        result["stdout"] = proc.stdout
        result["stderr"] = proc.stderr
        result["returncode"] = proc.returncode
    except subprocess.TimeoutExpired as e:
        result["error"] = f"check_systemd.sh timed out after {timeout_sec}s"
        result["stdout"] = e.stdout or ""
        result["stderr"] = e.stderr or ""
    except Exception as e:
        result["error"] = f"Could not run check_systemd.sh: {e}"
    return result
