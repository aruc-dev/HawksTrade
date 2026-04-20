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
from datetime import datetime, timezone
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
    read_recent_log_issues,
    read_daily_baseline,
    read_trades,
    run_check_systemd,
)
from dashboard.pnl import (
    current_ny_date,
    daily_loss_headroom,
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
        "unrealized": unrealized_pnl_summary(positions),
        "headroom": daily_loss_headroom(
            baseline=read_daily_baseline(),
            current_portfolio_value=account.get("portfolio_value", 0.0),
            daily_loss_limit_pct=cfg().daily_loss_limit_pct,
        ),
    }


def _get_enriched_positions(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return enrich_positions_with_trade_metadata(get_positions_as_dicts(), rows)


def _build_health() -> Dict[str, Any]:
    """Compose the health panel payload from check_systemd output and log tails."""
    systemd = run_check_systemd()
    log_issues = read_recent_log_issues()
    has_log_errors = any(i.get("level") in {"CRITICAL", "ERROR"} for i in log_issues)
    has_log_warnings = any(i.get("level") == "WARNING" for i in log_issues)
    # Traffic-light derivation:
    #   green  — systemd script returned 0
    #   yellow — systemd script returned 1 (warnings)
    #   red    — anything else (failures or error fetching)
    rc = systemd.get("returncode")
    if systemd.get("error") or has_log_errors:
        status = "red"
    elif rc == 0:
        status = "yellow" if has_log_warnings else "green"
    elif rc == 1:
        status = "yellow"
    else:
        status = "red"
    return {
        "status": status,
        "log_issue_count": len(log_issues),
        "log_issues": log_issues,
        "systemd": {
            "ok": systemd.get("ok"),
            "returncode": rc,
            "stdout_tail": (systemd.get("stdout") or "").splitlines()[-40:],
            "stderr_tail": (systemd.get("stderr") or "").splitlines()[-10:],
            "error": systemd.get("error"),
        },
    }


# Module-level app for uvicorn: `uvicorn dashboard.app:app`
app = create_app()
