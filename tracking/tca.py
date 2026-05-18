"""Transaction cost analysis over the HawksTrade trade log."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

import pandas as pd

from tracking.trade_log import read_trade_rows


ET = ZoneInfo("America/New_York")
TCA_COLUMNS = [
    "timestamp",
    "symbol",
    "strategy",
    "asset_class",
    "side",
    "qty",
    "fill_price",
    "decision_price",
    "arrival_price",
    "order_size_usd",
    "implementation_shortfall_bps",
    "slippage_bps",
    "timing_bps",
    "fees_bps",
    "total_bps",
    "expected_slippage_bps",
    "realised_slippage_bps",
    "residual_bps",
    "order_type",
    "execution_policy",
    "hour_et",
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _parse_timestamp(value) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fill_price(row: Mapping) -> float | None:
    side = str(row.get("side", "") or "").lower()
    if side == "buy":
        return _to_float(row.get("entry_price"))
    if side == "sell":
        return _to_float(row.get("exit_price"))
    return None


def _signed_bps(side: str, later_price: float, earlier_price: float) -> float:
    if earlier_price <= 0:
        return 0.0
    if side == "sell":
        return (earlier_price - later_price) / earlier_price * 10000.0
    return (later_price - earlier_price) / earlier_price * 10000.0


def _numeric_or_zero(value) -> float:
    parsed = _to_float(value)
    return parsed if parsed is not None else 0.0


def _fees_bps(row: Mapping, fill_price: float, qty: float) -> float:
    notional = abs(fill_price * qty)
    if notional <= 0:
        return 0.0
    fees = _numeric_or_zero(row.get("fees")) + _numeric_or_zero(row.get("commission"))
    return fees / notional * 10000.0


def _hour_et(ts: datetime | None) -> str:
    if ts is None:
        return ""
    return ts.astimezone(ET).strftime("%H:00")


def compute_implementation_shortfall(trade: Mapping) -> dict:
    """Return per-fill implementation-shortfall metrics for one trade row.

    Positive bps are adverse execution cost. Rows without a decision price or
    fill price are marked ineligible instead of being backfilled silently.
    """

    side = str(trade.get("side", "") or "").lower()
    if side not in {"buy", "sell"}:
        return {"eligible": False, "reason": "unsupported_side"}

    fill_price = _fill_price(trade)
    decision_price = _to_float(trade.get("decision_price"))
    if fill_price is None or fill_price <= 0:
        return {"eligible": False, "reason": "missing_fill_price"}
    if decision_price is None or decision_price <= 0:
        return {"eligible": False, "reason": "missing_decision_price"}

    arrival_price = _to_float(trade.get("arrival_price")) or decision_price
    qty = abs(_to_float(trade.get("qty")) or 0.0)
    expected = _to_float(trade.get("expected_slippage_bps"))
    realised = _to_float(trade.get("realised_slippage_bps"))
    if realised is None:
        realised = _signed_bps(side, fill_price, decision_price)

    timing_bps = _signed_bps(side, arrival_price, decision_price)
    slippage_bps = _signed_bps(side, fill_price, arrival_price)
    is_bps = _signed_bps(side, fill_price, decision_price)
    fees_bps = _fees_bps(trade, fill_price, qty)
    ts = _parse_timestamp(trade.get("timestamp"))

    return {
        "eligible": True,
        "timestamp": ts,
        "symbol": str(trade.get("symbol", "") or ""),
        "strategy": str(trade.get("strategy", "") or "unknown"),
        "asset_class": str(trade.get("asset_class", "") or "stock"),
        "side": side,
        "qty": qty,
        "fill_price": fill_price,
        "decision_price": decision_price,
        "arrival_price": arrival_price,
        "order_size_usd": abs(qty * fill_price),
        "implementation_shortfall_bps": is_bps,
        "slippage_bps": slippage_bps,
        "timing_bps": timing_bps,
        "fees_bps": fees_bps,
        "total_bps": is_bps + fees_bps,
        "expected_slippage_bps": expected,
        "realised_slippage_bps": realised,
        "residual_bps": None if expected is None else realised - expected,
        "order_type": str(trade.get("order_type", "") or ""),
        "execution_policy": str(trade.get("execution_policy", "") or "single_leg_marketable_limit"),
        "hour_et": _hour_et(ts),
        "latency_ms": trade.get("latency_ms", ""),
    }


def prepare_tca_frame(
    rows: Iterable[Mapping] | pd.DataFrame | None = None,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> pd.DataFrame:
    """Return a normalized TCA DataFrame for eligible filled rows."""

    if rows is None:
        rows = read_trade_rows()
    if isinstance(rows, pd.DataFrame):
        records = rows.to_dict("records")
    else:
        records = list(rows)

    metrics = []
    for row in records:
        status = str(row.get("status", "") or "").lower()
        if status in {"dry_run", "submitted", "entry_failed", "exit_failed", "order_governor_blocked"}:
            continue
        result = compute_implementation_shortfall(row)
        if not result.get("eligible"):
            continue
        ts = result.get("timestamp")
        if start is not None and (ts is None or ts < _as_utc(start)):
            continue
        if end is not None and (ts is None or ts >= _as_utc(end)):
            continue
        metrics.append({key: result.get(key, "") for key in [*TCA_COLUMNS, "latency_ms"]})

    if not metrics:
        return pd.DataFrame(columns=[*TCA_COLUMNS, "latency_ms"])
    frame = pd.DataFrame(metrics)
    for col in (
        "qty",
        "fill_price",
        "decision_price",
        "arrival_price",
        "order_size_usd",
        "implementation_shortfall_bps",
        "slippage_bps",
        "timing_bps",
        "fees_bps",
        "total_bps",
        "expected_slippage_bps",
        "realised_slippage_bps",
        "residual_bps",
    ):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def aggregate_by(df: pd.DataFrame | Iterable[Mapping], dimensions: list[str]) -> pd.DataFrame:
    """Aggregate fill-quality metrics by the requested dimensions."""

    frame = prepare_tca_frame(df) if not isinstance(df, pd.DataFrame) else df.copy()
    dims = [dim for dim in dimensions if dim in frame.columns]
    if not dims:
        dims = ["strategy"] if "strategy" in frame.columns else []
    columns = [
        *dims,
        "fills",
        "notional_usd",
        "median_is_bps",
        "p95_is_bps",
        "median_realised_slippage_bps",
        "median_expected_slippage_bps",
        "median_residual_bps",
    ]
    if frame.empty or not dims:
        return pd.DataFrame(columns=columns)
    grouped = frame.groupby(dims, dropna=False)
    result = grouped.agg(
        fills=("symbol", "count"),
        notional_usd=("order_size_usd", "sum"),
        median_is_bps=("implementation_shortfall_bps", "median"),
        p95_is_bps=("implementation_shortfall_bps", lambda values: values.quantile(0.95)),
        median_realised_slippage_bps=("realised_slippage_bps", "median"),
        median_expected_slippage_bps=("expected_slippage_bps", "median"),
        median_residual_bps=("residual_bps", "median"),
    ).reset_index()
    return result.sort_values(["fills", "notional_usd"], ascending=[False, False]).reset_index(drop=True)


def compute_model_fit(df: pd.DataFrame | Iterable[Mapping]) -> dict:
    """Compare expected and realised slippage for eligible rows."""

    frame = prepare_tca_frame(df) if not isinstance(df, pd.DataFrame) else df.copy()
    if frame.empty:
        return {"n": 0, "median_residual_bps": 0.0, "rmse_bps": 0.0, "r2": 0.0}
    paired = frame[["expected_slippage_bps", "realised_slippage_bps", "residual_bps"]].dropna()
    if paired.empty:
        return {"n": 0, "median_residual_bps": 0.0, "rmse_bps": 0.0, "r2": 0.0}
    residual = paired["realised_slippage_bps"] - paired["expected_slippage_bps"]
    rmse = float(math.sqrt((residual.pow(2)).mean()))
    realised = paired["realised_slippage_bps"]
    sst = float(((realised - realised.mean()).pow(2)).sum())
    sse = float((residual.pow(2)).sum())
    r2 = 0.0 if sst <= 0 else 1.0 - (sse / sst)
    return {
        "n": int(len(paired)),
        "median_residual_bps": float(residual.median()),
        "rmse_bps": rmse,
        "r2": float(r2),
    }


def detect_slippage_anomalies(
    rows: Iterable[Mapping] | pd.DataFrame,
    *,
    sigma: float = 3.0,
    trailing_days: int = 30,
    min_history: int = 5,
) -> list[dict]:
    """Return fills whose realised-vs-expected residual breaches trailing sigma."""

    frame = prepare_tca_frame(rows) if not isinstance(rows, pd.DataFrame) else rows.copy()
    if frame.empty or "residual_bps" not in frame:
        return []
    frame = frame.dropna(subset=["timestamp", "residual_bps"]).sort_values("timestamp")
    anomalies = []
    for _, row in frame.iterrows():
        ts = row["timestamp"]
        prior = frame[
            (frame["timestamp"] < ts)
            & (frame["timestamp"] >= ts - timedelta(days=trailing_days))
        ]["residual_bps"].dropna()
        if len(prior) < min_history:
            continue
        std = float(prior.std(ddof=0))
        if std <= 0:
            continue
        threshold = float(prior.mean()) + sigma * std
        residual = float(row["residual_bps"])
        if residual > threshold:
            anomalies.append({
                "timestamp": ts,
                "symbol": row.get("symbol", ""),
                "strategy": row.get("strategy", ""),
                "side": row.get("side", ""),
                "residual_bps": residual,
                "threshold_bps": threshold,
                "sigma": sigma,
            })
    return anomalies


def _markdown_table(df: pd.DataFrame, columns: list[str], *, max_rows: int | None = None) -> str:
    if df.empty:
        return "_No rows._"
    display = df[columns].head(max_rows).copy()
    for col in display.columns:
        if pd.api.types.is_float_dtype(display[col]):
            display[col] = display[col].map(lambda value: "" if pd.isna(value) else f"{value:.2f}")
    return display.to_markdown(index=False)


def _plain_table(df: pd.DataFrame, columns: list[str], *, max_rows: int | None = None) -> str:
    if df.empty:
        return "  No rows."
    display = df[columns].head(max_rows).copy()
    for col in display.columns:
        if pd.api.types.is_float_dtype(display[col]):
            display[col] = display[col].map(lambda value: "" if pd.isna(value) else f"{value:.2f}")
    return display.to_string(index=False)


def render_tca_section(
    rows: Iterable[Mapping] | pd.DataFrame | None = None,
    *,
    title: str = "Transaction Cost Analysis",
    start: datetime | None = None,
    end: datetime | None = None,
) -> str:
    """Render a compact plain-text TCA section for daily/weekly reports."""

    frame = prepare_tca_frame(rows, start=start, end=end)
    lines = [title, "=" * len(title)]
    if start or end:
        start_text = _as_utc(start).isoformat() if start else "beginning"
        end_text = _as_utc(end).isoformat() if end else "now"
        lines.append(f"Window: {start_text} to {end_text}")
    if frame.empty:
        lines.append("No TCA-eligible fills in this window.")
        return "\n".join(lines)

    fit = compute_model_fit(frame)
    lines.append(
        "Summary: "
        f"fills={len(frame)} "
        f"notional=${frame['order_size_usd'].sum():,.2f} "
        f"median_IS={frame['implementation_shortfall_bps'].median():.2f}bps "
        f"p95_IS={frame['implementation_shortfall_bps'].quantile(0.95):.2f}bps "
        f"median_residual={fit['median_residual_bps']:.2f}bps"
    )
    by_strategy = aggregate_by(frame, ["strategy"])
    lines.append("\nBy strategy:")
    lines.append(_plain_table(
        by_strategy,
        [
            "strategy",
            "fills",
            "median_is_bps",
            "median_realised_slippage_bps",
            "median_expected_slippage_bps",
            "median_residual_bps",
        ],
    ))
    worst = frame.sort_values("implementation_shortfall_bps", ascending=False)
    lines.append("\nWorst fills:")
    lines.append(_plain_table(
        worst,
        [
            "timestamp",
            "symbol",
            "side",
            "strategy",
            "implementation_shortfall_bps",
            "expected_slippage_bps",
            "realised_slippage_bps",
            "residual_bps",
        ],
        max_rows=10,
    ))
    return "\n".join(lines)


def _latency_stage_rows(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if frame.empty or "latency_ms" not in frame:
        return pd.DataFrame(columns=["asset_class", "stage", "p50_ms", "p95_ms", "p99_ms", "samples"])
    for _, row in frame.iterrows():
        raw = row.get("latency_ms")
        if raw in (None, ""):
            continue
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, Mapping):
            continue
        asset_class = row.get("asset_class") or "stock"
        for stage, value in payload.items():
            parsed = _to_float(value)
            if parsed is None:
                continue
            rows.append({"asset_class": asset_class, "stage": stage, "latency_ms": parsed})
    if not rows:
        return pd.DataFrame(columns=["asset_class", "stage", "p50_ms", "p95_ms", "p99_ms", "samples"])
    raw = pd.DataFrame(rows)
    return raw.groupby(["asset_class", "stage"]).agg(
        samples=("latency_ms", "count"),
        p50_ms=("latency_ms", "median"),
        p95_ms=("latency_ms", lambda values: values.quantile(0.95)),
        p99_ms=("latency_ms", lambda values: values.quantile(0.99)),
    ).reset_index()


def render_weekly_tca_report(
    rows: Iterable[Mapping] | pd.DataFrame | None = None,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    generated_at: datetime | None = None,
) -> str:
    """Render the standalone weekly TCA markdown report."""

    generated_at = _as_utc(generated_at or _utc_now())
    end = _as_utc(end or generated_at)
    start = _as_utc(start or (end - timedelta(days=7)))
    frame = prepare_tca_frame(rows, start=start, end=end)

    lines = [
        f"# Weekly TCA Report - {generated_at.date().isoformat()}",
        "",
        f"- Window: `{start.isoformat()}` to `{end.isoformat()}`",
        f"- Eligible fills: `{len(frame)}`",
        "",
    ]
    if frame.empty:
        lines.append("No TCA-eligible fills were recorded in this window.")
        return "\n".join(lines)

    fit = compute_model_fit(frame)
    lines.extend([
        "## Headline",
        "",
        f"- Notional measured: `${frame['order_size_usd'].sum():,.2f}`",
        f"- Median implementation shortfall: `{frame['implementation_shortfall_bps'].median():.2f} bps`",
        f"- 95th percentile implementation shortfall: `{frame['implementation_shortfall_bps'].quantile(0.95):.2f} bps`",
        f"- Model median residual: `{fit['median_residual_bps']:.2f} bps`",
        f"- Model RMSE: `{fit['rmse_bps']:.2f} bps`",
        f"- Model R2: `{fit['r2']:.3f}`",
        "",
        "## By Strategy",
        "",
        _markdown_table(aggregate_by(frame, ["strategy"]), [
            "strategy",
            "fills",
            "notional_usd",
            "median_is_bps",
            "p95_is_bps",
            "median_realised_slippage_bps",
            "median_expected_slippage_bps",
            "median_residual_bps",
        ]),
        "",
        "## By Hour ET",
        "",
        _markdown_table(aggregate_by(frame, ["hour_et"]), [
            "hour_et",
            "fills",
            "median_is_bps",
            "p95_is_bps",
            "median_residual_bps",
        ]),
        "",
        "## Worst Fills",
        "",
        _markdown_table(
            frame.sort_values("implementation_shortfall_bps", ascending=False),
            [
                "timestamp",
                "symbol",
                "side",
                "strategy",
                "asset_class",
                "order_size_usd",
                "implementation_shortfall_bps",
                "expected_slippage_bps",
                "realised_slippage_bps",
                "residual_bps",
            ],
            max_rows=10,
        ),
        "",
        "## Latency",
        "",
        _markdown_table(_latency_stage_rows(frame), [
            "asset_class",
            "stage",
            "samples",
            "p50_ms",
            "p95_ms",
            "p99_ms",
        ]),
    ])
    return "\n".join(lines)


def write_weekly_tca_report(
    output_dir: str | Path,
    rows: Iterable[Mapping] | pd.DataFrame | None = None,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    generated_at: datetime | None = None,
) -> Path:
    generated_at = _as_utc(generated_at or _utc_now())
    output_path = Path(output_dir) / f"tca_weekly_{generated_at.date().isoformat()}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_weekly_tca_report(rows, start=start, end=end, generated_at=generated_at),
        encoding="utf-8",
    )
    return output_path
