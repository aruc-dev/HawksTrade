"""HawksTrade dashboard FastAPI application.

READ-ONLY. No endpoint in this file mutates trading state. Do not add one.

Endpoints:
  GET /               — HTML shell that polls /api/state
  GET /api/state      — full JSON snapshot (everything below, one round trip)
  GET /api/health     — systemd status + recent errors
  GET /api/positions  — open positions with live P&L
  GET /api/pnl/today  — realized today + unrealized now + headroom
  GET /api/trades/recent — last 30 closed trades
  GET /api/strategies/summary — per-strategy summary (30d)
  GET /healthz        — liveness only, NO auth (safe for tunnel)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dashboard import __version__
from dashboard.alpaca_readonly import (
    alpaca_reachable,
    get_account_summary,
    get_positions_as_dicts,
)
from dashboard.config import cfg
from dashboard.data_sources import (
    enrich_positions_with_trade_metadata,
    read_daily_baseline,
    read_latest_health_snapshot,
    read_recent_log_issues,
    read_trades,
)
from dashboard.pnl import (
    current_ny_date,
    daily_loss_headroom,
    realized_pnl_window,
    realized_pnl_today,
    strategy_summary,
    unrealized_pnl_summary,
)
from dashboard.security import (
    AccessLogMiddleware,
    assert_production_auth_safe,
    require_auth,
)

log = logging.getLogger("dashboard.app")
HEALTH_SNAPSHOT_WARN_AGE = timedelta(minutes=20)
HEALTH_SNAPSHOT_FAIL_AGE = timedelta(minutes=40)
STATUS_RANK = {"green": 0, "yellow": 1, "red": 2}
REALIZED_WINDOW_DAYS = 7


HERE = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(HERE / "templates"))


def create_app() -> FastAPI:
    # Fail fast if auth configuration is unsafe for production.
    assert_production_auth_safe()

    app = FastAPI(
        title="HawksTrade Dashboard",
        version=__version__,
        docs_url=None,  # No /docs — it leaks the endpoint list publicly.
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(AccessLogMiddleware)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    # ── HTML shell ──────────────────────────────────────────────────────────
    @app.get("/", include_in_schema=False)
    async def index(request: Request, _: str = Depends(require_auth)) -> Any:
        return TEMPLATES.TemplateResponse(
            request,
            "dashboard.html",
            {
                "version": __version__,
                "mode": cfg().mode,
            },
        )

    # ── Unauthenticated liveness (for the tunnel's own health check) ────────
    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> JSONResponse:
        ok = alpaca_reachable()
        status = 200 if ok else 503
        return JSONResponse({"status": "ok" if ok else "degraded"}, status_code=status)

    # ── Full state snapshot — one round trip for the polling client ─────────
    @app.get("/api/state")
    async def api_state(_: str = Depends(require_auth)) -> Dict[str, Any]:
        return _build_state_snapshot()

    # ── Individual endpoints for clients that want them ─────────────────────
    @app.get("/api/health")
    async def api_health(_: str = Depends(require_auth)) -> Dict[str, Any]:
        return _build_health()

    @app.get("/api/positions")
    async def api_positions(_: str = Depends(require_auth)) -> Dict[str, Any]:
        positions = _get_enriched_positions(read_trades())
        return {"positions": positions, "summary": unrealized_pnl_summary(positions)}

    @app.get("/api/pnl/today")
    async def api_pnl_today(_: str = Depends(require_auth)) -> Dict[str, Any]:
        return _build_pnl_today()

    @app.get("/api/trades/recent")
    async def api_trades_recent(
        limit: int = 30,
        _: str = Depends(require_auth),
    ) -> Dict[str, Any]:
        rows = read_trades()
        closed = [r for r in rows if (r.get("status") or "").lower() == "closed"
                  and (r.get("side") or "").lower() == "sell"]
        # Most recent first by timestamp.
        closed.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        limit = max(1, min(int(limit or 30), 200))
        return {"trades": closed[:limit]}

    @app.get("/api/strategies/summary")
    async def api_strategies(_: str = Depends(require_auth)) -> Dict[str, Any]:
        rows = read_trades()
        return {"strategies": strategy_summary(rows, lookback_days=30)}

    return app


# ── Snapshot builders (pure enough to unit-test) ─────────────────────────────

def _build_state_snapshot() -> Dict[str, Any]:
    """Assemble the full state dict for /api/state."""
    now = datetime.now(timezone.utc)
    rows = read_trades()
    positions = _get_enriched_positions(rows)
    account = get_account_summary()

    pnl_today = realized_pnl_today(rows, ny_date_str=current_ny_date())
    pnl_7d = realized_pnl_window(rows, lookback_days=REALIZED_WINDOW_DAYS, now_utc=now)
    unrealized = unrealized_pnl_summary(positions)
    baseline = read_daily_baseline()
    headroom = daily_loss_headroom(
        baseline=baseline,
        current_portfolio_value=account.get("portfolio_value", 0.0),
        daily_loss_limit_pct=cfg().daily_loss_limit_pct,
    )
    strategies = strategy_summary(rows, lookback_days=30)
    health = _build_health()

    # Recent trades (last 10 on the main view; the dedicated endpoint returns more).
    closed = [r for r in rows if (r.get("status") or "").lower() == "closed"
              and (r.get("side") or "").lower() == "sell"]
    closed.sort(key=lambda r: r.get("timestamp", ""), reverse=True)

    return {
        "version": __version__,
        "mode": cfg().mode,
        "server_time_utc": now.isoformat(timespec="seconds"),
        "ny_date": current_ny_date(),
        "account": account,
        "positions": positions,
        "position_summary": unrealized,
        "realized_today": pnl_today,
        "realized_7d": pnl_7d,
        "daily_loss_headroom": headroom,
        "strategies": strategies,
        "recent_trades": closed[:10],
        "health": health,
        "alpaca_reachable": alpaca_reachable(),
    }


def _build_pnl_today() -> Dict[str, Any]:
    rows = read_trades()
    positions = _get_enriched_positions(rows)
    account = get_account_summary()
    return {
        "realized": realized_pnl_today(rows, ny_date_str=current_ny_date()),
        "realized_7d": realized_pnl_window(
            rows,
            lookback_days=REALIZED_WINDOW_DAYS,
        ),
        "unrealized": unrealized_pnl_summary(positions),
        "headroom": daily_loss_headroom(
            baseline=read_daily_baseline(),
            current_portfolio_value=account.get("portfolio_value", 0.0),
            daily_loss_limit_pct=cfg().daily_loss_limit_pct,
        ),
    }


def _get_enriched_positions(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return enrich_positions_with_trade_metadata(get_positions_as_dicts(), rows)


def _parse_iso_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def _format_age(delta: timedelta) -> str:
    total_seconds = max(0, int(delta.total_seconds()))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _status_label(status: str) -> str:
    return {"green": "[OK]", "yellow": "[WARN]", "red": "[NOK]"}.get(status, f"[{status.upper()}]")


def _merge_status(*statuses: str) -> str:
    return max(statuses, key=lambda value: STATUS_RANK.get(value, 0))


def _format_snapshot_log_issues(snapshot: Dict[str, Any]) -> List[Dict[str, str]]:
    issues: List[Dict[str, str]] = []
    for key, level in (("log_errors", "ERROR"), ("log_warnings", "WARNING")):
        for item in snapshot.get(key, []) or []:
            source_path = str(item.get("source_file") or "")
            source_name = Path(source_path).name if source_path else "health_snapshot"
            issues.append({
                "file": source_name,
                "level": level,
                "line": item.get("raw") or item.get("message") or "",
            })
    issues.sort(key=lambda item: item.get("line", ""), reverse=True)
    return issues[:20]


def _snapshot_stdout_lines(snapshot: Dict[str, Any], *, age: timedelta, stale_status: str | None) -> List[str]:
    generated_at = str(snapshot.get("generated_at") or "unknown")
    overall = str(snapshot.get("overall_status") or "red")
    alpaca = snapshot.get("alpaca") or {}
    lines = [
        "Health source : latest health snapshot",
        f"Generated     : {generated_at} ({_format_age(age)} ago)",
        f"Template      : {snapshot.get('cron_template') or 'unknown'} | Window: last {snapshot.get('lookback_hours') or '?'}h",
        f"Overall       : {_status_label(overall)}",
        (
            f"Alpaca        : {'Connected' if alpaca.get('connected') else 'Unavailable'}"
            f" | Portfolio: ${float(alpaca.get('portfolio_value') or 0.0):,.2f}"
            f" | Open positions: {int(alpaca.get('open_position_count') or 0)}"
        ),
    ]
    if stale_status:
        lines.append(
            f"Snapshot age  : {_status_label(stale_status)} older than expected; check hawkstrade-health-check.timer"
        )
    lines.append("Jobs:")
    for job in snapshot.get("job_health", []) or []:
        last_run = str(job.get("last_run_at") or "never")
        missed = int(job.get("missed_runs") or 0)
        lines.append(
            f"  {_status_label(str(job.get('status') or 'red'))} {job.get('label') or job.get('key') or 'job'}"
            f" | missed={missed} | last={last_run}"
        )
    error_count = len(snapshot.get("log_errors", []) or [])
    warning_count = len(snapshot.get("log_warnings", []) or [])
    lines.append(f"Log findings   : errors={error_count} warnings={warning_count}")
    return lines[-40:]


def _build_health() -> Dict[str, Any]:
    """Compose the health panel payload from health snapshots and recent logs."""
    snapshot_state = read_latest_health_snapshot()
    live_log_issues = read_recent_log_issues()
    snapshot_log_issues: List[Dict[str, str]] = []
    live_log_issues: List[Dict[str, str]] = []
    stdout_tail: List[str] = []
    stderr_tail: List[str] = []
    system_error = snapshot_state.get("error")
    system_ok = bool(snapshot_state.get("ok"))
    snapshot_status = "red"
    stale_status: str | None = None

    if system_ok and isinstance(snapshot_state.get("data"), dict):
        snapshot = snapshot_state["data"]
        snapshot_status = str(snapshot.get("overall_status") or "red")
        generated_at = _parse_iso_timestamp(snapshot.get("generated_at"))
        if generated_at is not None:
            if generated_at.tzinfo is None:
                generated_at = generated_at.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - generated_at.astimezone(timezone.utc)
            if age > HEALTH_SNAPSHOT_FAIL_AGE:
                stale_status = "red"
            elif age > HEALTH_SNAPSHOT_WARN_AGE:
                stale_status = "yellow"
        else:
            age = timedelta.max
            stale_status = "red"
        if stale_status:
            snapshot_status = _merge_status(snapshot_status, stale_status)
        snapshot_log_issues = _format_snapshot_log_issues(snapshot)
        stdout_tail = _snapshot_stdout_lines(snapshot, age=age, stale_status=stale_status)
    else:
        live_log_issues = read_recent_log_issues()
        stdout_tail = [
            "Health source : snapshot unavailable",
            str(system_error or "No health snapshot available."),
            "Check hawkstrade-health-check.service and hawkstrade-health-check.timer.",
        ]

    log_issues = snapshot_log_issues if system_ok else live_log_issues
    has_log_errors = any(i.get("level") in {"CRITICAL", "ERROR"} for i in log_issues)
    has_log_warnings = any(i.get("level") == "WARNING" for i in log_issues)
    status = snapshot_status
    if has_log_errors:
        status = _merge_status(status, "red")
    elif has_log_warnings:
        status = _merge_status(status, "yellow")

    return {
        "status": status,
        "log_issue_count": len(log_issues),
        "log_issues": log_issues,
        "systemd": {
            "ok": system_ok,
            "source": "health_snapshot",
            "path": snapshot_state.get("path"),
            "returncode": 0 if system_ok else None,
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
            "error": system_error,
        },
    }


# Module-level app for uvicorn: `uvicorn dashboard.app:app`
app = create_app()
