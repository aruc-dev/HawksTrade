#!/usr/bin/env python3
"""
Offline factor research harness for HawksTrade.

This script fetches historical daily bars, builds a factor dataset using the
current HawksTrade stock universe inputs, and writes reproducible research
artifacts. It never places orders and does not change live strategy defaults.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
import pandas as pd
from pandas.tseries.holiday import AbstractHolidayCalendar, GoodFriday, Holiday, MO, TH, nearest_workday
from pandas.tseries.offsets import DateOffset

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import alpaca_client as ac  # noqa: E402
from core.config_loader import get_config  # noqa: E402
from core.sector_lookup import get_sector  # noqa: E402
from screener.universe_builder import UniverseBuilder  # noqa: E402


DEFAULT_HORIZONS = (1, 2, 5, 10, 20)
DEFAULT_FACTORS = (
    "return_5d",
    "volume_ratio",
    "rsi_14",
    "bb_pct_b",
    "atr_pct",
    "sma50_distance",
    "sma200_distance",
    "gap_pct",
    "breadth_pct",
)


class USEquityHolidayCalendar(AbstractHolidayCalendar):
    rules = [
        Holiday("New Year's Day", month=1, day=1, observance=nearest_workday),
        Holiday("Martin Luther King Jr. Day", month=1, day=1, offset=DateOffset(weekday=MO(3))),
        Holiday("Presidents Day", month=2, day=1, offset=DateOffset(weekday=MO(3))),
        GoodFriday,
        Holiday("Memorial Day", month=5, day=31, offset=DateOffset(weekday=MO(-1))),
        Holiday("Juneteenth", month=6, day=19, observance=nearest_workday, start_date="2022-01-01"),
        Holiday("Independence Day", month=7, day=4, observance=nearest_workday),
        Holiday("Labor Day", month=9, day=1, offset=DateOffset(weekday=MO(1))),
        Holiday("Thanksgiving Day", month=11, day=1, offset=DateOffset(weekday=TH(4))),
        Holiday("Christmas Day", month=12, day=25, observance=nearest_workday),
    ]


US_EQUITY_BUSINESS_DAY = pd.offsets.CustomBusinessDay(calendar=USEquityHolidayCalendar())


def _expected_single_symbol_dates(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    return pd.date_range(start, end, freq=US_EQUITY_BUSINESS_DAY)


def _bar_value(bar, field: str, default=None):
    if isinstance(bar, Mapping):
        return bar.get(field, default)
    return getattr(bar, field, default)


def _bar_timestamp(bar):
    return (
        _bar_value(bar, "timestamp")
        or _bar_value(bar, "t")
        or _bar_value(bar, "date")
    )


def _parse_timestamp(value) -> pd.Timestamp | None:
    if value in (None, ""):
        return None
    try:
        ts = pd.Timestamp(value)
    except Exception:
        return None
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts.normalize()


def _finite_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _bars_to_frame(bars_by_symbol: Mapping[str, Iterable]) -> tuple[pd.DataFrame, list[dict]]:
    records = []
    issues = []
    for symbol, bars in (bars_by_symbol or {}).items():
        if not bars:
            issues.append({"symbol": symbol, "issue": "missing_bars"})
            continue
        symbol_records = []
        for bar in bars:
            ts = _parse_timestamp(_bar_timestamp(bar))
            row = {
                "date": ts,
                "symbol": symbol,
                "open": _finite_float(_bar_value(bar, "open")),
                "high": _finite_float(_bar_value(bar, "high")),
                "low": _finite_float(_bar_value(bar, "low")),
                "close": _finite_float(_bar_value(bar, "close")),
                "volume": _finite_float(_bar_value(bar, "volume")),
            }
            if row["date"] is None or any(row[field] is None for field in ("open", "high", "low", "close", "volume")):
                issues.append({"symbol": symbol, "issue": "invalid_bar"})
                continue
            prices = [row[field] for field in ("open", "high", "low", "close")]
            if (
                any(value <= 0 for value in prices)
                or row["high"] < row["low"]
                or row["open"] < row["low"]
                or row["open"] > row["high"]
                or row["close"] < row["low"]
                or row["close"] > row["high"]
                or row["volume"] <= 0
            ):
                issues.append({"symbol": symbol, "issue": "invalid_ohlcv"})
                continue
            symbol_records.append(row)
        if not symbol_records:
            issues.append({"symbol": symbol, "issue": "no_valid_bars"})
            continue
        records.extend(symbol_records)

    if not records:
        return pd.DataFrame(columns=["date", "symbol", "open", "high", "low", "close", "volume"]), issues

    df = pd.DataFrame.from_records(records)
    df = df.sort_values(["symbol", "date"]).drop_duplicates(["symbol", "date"], keep="last")
    all_observed_dates = pd.DatetimeIndex(df["date"].dropna().sort_values().unique())
    symbol_count = int(df["symbol"].nunique())
    for symbol, group in df.groupby("symbol", sort=True):
        dates = pd.DatetimeIndex(group["date"].dropna().sort_values().unique())
        if len(dates) < 2:
            continue
        if symbol_count > 1:
            expected_dates = all_observed_dates[
                (all_observed_dates >= dates.min()) & (all_observed_dates <= dates.max())
            ]
        else:
            expected_dates = _expected_single_symbol_dates(dates.min(), dates.max())
        missing_dates = expected_dates.difference(dates)
        if len(missing_dates) > 0:
            issues.append(
                {
                    "symbol": symbol,
                    "issue": "date_gap",
                    "missing_count": int(len(missing_dates)),
                    "first_missing": str(missing_dates[0].date()),
                }
            )
    return df.reset_index(drop=True), issues


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        values = 100 - (100 / (1 + rs))
    return values.replace([np.inf, -np.inf], np.nan).fillna(50.0)


def _add_symbol_factors(group: pd.DataFrame, horizons: tuple[int, ...]) -> pd.DataFrame:
    group = group.sort_values("date").copy()
    close = group["close"]
    high = group["high"]
    low = group["low"]
    open_ = group["open"]
    volume = group["volume"]
    prev_close = close.shift(1)

    true_range = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    std20 = close.rolling(20).std()
    lower = sma20 - 2.0 * std20
    upper = sma20 + 2.0 * std20
    bandwidth = (upper - lower).replace(0, np.nan)

    group["return_5d"] = close.pct_change(5)
    group["volume_ratio"] = volume / volume.shift(1).rolling(20).mean()
    group["rsi_14"] = _rsi(close, 14)
    group["bb_pct_b"] = ((close - lower) / bandwidth).fillna(0.5)
    group["atr_pct"] = true_range.rolling(14).mean() / close
    group["sma50_distance"] = (close / sma50) - 1.0
    group["sma200_distance"] = (close / sma200) - 1.0
    group["gap_pct"] = (open_ / prev_close) - 1.0
    for horizon in horizons:
        group[f"forward_return_{horizon}d"] = close.shift(-horizon) / close - 1.0
    return group


def _add_breadth_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    breadth = (
        df.dropna(subset=["sma50_distance"])
        .assign(above_sma50=lambda frame: frame["sma50_distance"] > 0)
        .groupby("date")["above_sma50"]
        .mean()
        .rename("breadth_pct")
    )
    df = df.merge(breadth, how="left", on="date")
    df["breadth_pct"] = df["breadth_pct"].fillna(0.5)
    df["breadth_regime"] = np.select(
        [df["breadth_pct"] >= 0.50, df["breadth_pct"] < 0.25],
        ["green", "red"],
        default="yellow",
    )
    return df


def build_factor_dataset(
    bars_by_symbol: Mapping[str, Iterable],
    *,
    start: str | None = None,
    end: str | None = None,
    horizons: Iterable[int] = DEFAULT_HORIZONS,
) -> tuple[pd.DataFrame, list[dict]]:
    horizons = tuple(int(h) for h in horizons)
    if any(horizon <= 0 for horizon in horizons):
        raise ValueError("Forward-return horizons must be positive integers")
    raw, issues = _bars_to_frame(bars_by_symbol)
    if raw.empty:
        return raw, issues

    pieces = [
        _add_symbol_factors(group, horizons)
        for _symbol, group in raw.groupby("symbol", group_keys=False)
    ]
    featured = pd.concat(pieces, ignore_index=True) if pieces else raw.copy()
    featured["sector"] = featured["symbol"].map(lambda symbol: get_sector(str(symbol)))
    featured = _add_breadth_features(featured)

    if start:
        start_ts = _parse_timestamp(start)
        if start_ts is not None:
            featured = featured[featured["date"] >= start_ts]
    if end:
        end_ts = _parse_timestamp(end)
        if end_ts is not None:
            featured = featured[featured["date"] <= end_ts]

    featured = featured.sort_values(["date", "symbol"]).reset_index(drop=True)
    return featured, issues


def _max_drawdown(returns: pd.Series) -> float:
    returns = pd.to_numeric(returns, errors="coerce").dropna()
    if returns.empty:
        return 0.0
    equity = (1.0 + returns).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    return float(drawdown.min())


def _factor_quantiles(valid: pd.DataFrame, factor: str, quantiles: int) -> pd.Series:
    unique = valid[factor].nunique(dropna=True)
    bins = min(quantiles, unique)
    if bins < 2:
        return pd.Series(index=valid.index, dtype="float64")
    try:
        return pd.qcut(valid[factor], q=bins, labels=False, duplicates="drop") + 1
    except ValueError:
        return pd.Series(index=valid.index, dtype="float64")


def _turnover(valid: pd.DataFrame, quantile_col: str) -> float | None:
    top_sets = []
    max_q = valid[quantile_col].max()
    if pd.isna(max_q):
        return None
    for _date, day in valid.groupby("date"):
        symbols = set(day.loc[day[quantile_col] == max_q, "symbol"])
        if symbols:
            top_sets.append(symbols)
    if len(top_sets) < 2:
        return None
    turnovers = []
    for previous, current in zip(top_sets, top_sets[1:]):
        denominator = max(len(previous | current), 1)
        turnovers.append(len(previous ^ current) / denominator)
    return float(np.mean(turnovers)) if turnovers else None


def _spearman_corr(left: pd.Series, right: pd.Series) -> float | None:
    ranks = pd.concat([
        pd.to_numeric(left, errors="coerce").rank(method="average"),
        pd.to_numeric(right, errors="coerce").rank(method="average"),
    ], axis=1).dropna()
    if len(ranks) < 2:
        return None
    if ranks.iloc[:, 0].nunique() < 2 or ranks.iloc[:, 1].nunique() < 2:
        return None
    return _json_float(ranks.iloc[:, 0].corr(ranks.iloc[:, 1]))


def _split_name(row_number: int, total: int) -> str:
    if total <= 0:
        return "train"
    pct = row_number / total
    if pct < 0.60:
        return "train"
    if pct < 0.80:
        return "validation"
    return "test"


def _split_metrics(valid: pd.DataFrame, factor: str, forward_col: str) -> dict:
    dates = sorted(valid["date"].dropna().unique())
    date_to_split = {
        date: _split_name(index, len(dates))
        for index, date in enumerate(dates)
    }
    split_rows = {}
    for name, split in valid.assign(split=valid["date"].map(date_to_split)).groupby("split"):
        split_rows[name] = {
            "observations": int(len(split)),
            "mean_forward_return": _json_float(split[forward_col].mean()),
            "information_coefficient": _spearman_corr(split[factor], split[forward_col]),
        }
    return {name: split_rows.get(name, {"observations": 0}) for name in ("train", "validation", "test")}


def _json_float(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def compute_factor_report(
    dataset: pd.DataFrame,
    *,
    factors: Iterable[str] = DEFAULT_FACTORS,
    horizons: Iterable[int] = DEFAULT_HORIZONS,
    quantiles: int = 5,
    data_quality_issues: list[dict] | None = None,
    generated_at: str | None = "not_recorded",
) -> dict:
    summary = {
        "generated_at": "not_recorded" if generated_at in (None, "") else str(generated_at),
        "rows": int(len(dataset)),
        "symbols": sorted(dataset["symbol"].dropna().unique().tolist()) if "symbol" in dataset else [],
        "date_range": {
            "start": str(dataset["date"].min().date()) if len(dataset) else None,
            "end": str(dataset["date"].max().date()) if len(dataset) else None,
        },
        "data_quality": {
            "issues": data_quality_issues or [],
            "issue_count": len(data_quality_issues or []),
        },
        "factors": {},
    }
    if dataset.empty:
        return summary

    for factor in factors:
        if factor not in dataset.columns:
            continue
        factor_summary = {}
        for horizon in horizons:
            forward_col = f"forward_return_{int(horizon)}d"
            if forward_col not in dataset.columns:
                continue
            valid = dataset[["date", "symbol", factor, forward_col]].dropna().copy()
            if valid.empty:
                factor_summary[str(horizon)] = {
                    "observations": 0,
                    "coverage": 0.0,
                }
                continue
            valid["quantile"] = _factor_quantiles(valid, factor, quantiles)
            quantile_returns = {
                str(int(q)): _json_float(value)
                for q, value in valid.dropna(subset=["quantile"]).groupby("quantile")[forward_col].mean().items()
            }
            top_q = valid["quantile"].max()
            top_returns = valid.loc[valid["quantile"] == top_q].groupby("date")[forward_col].mean()
            factor_summary[str(horizon)] = {
                "observations": int(len(valid)),
                "coverage": _json_float(len(valid) / max(len(dataset), 1)),
                "information_coefficient": _spearman_corr(valid[factor], valid[forward_col]),
                "mean_forward_return": _json_float(valid[forward_col].mean()),
                "hit_rate": _json_float((valid[forward_col] > 0).mean()),
                "quantile_mean_returns": quantile_returns,
                "top_quantile_max_drawdown": _json_float(_max_drawdown(top_returns)),
                "top_quantile_turnover": _json_float(_turnover(valid, "quantile")),
                "splits": _split_metrics(valid, factor, forward_col),
            }
        summary["factors"][factor] = factor_summary
    return summary


def _markdown_summary(summary: dict) -> str:
    lines = [
        "# HawksTrade Factor Research",
        "",
        f"Generated: {summary['generated_at']}",
        f"Rows: {summary['rows']}",
        f"Symbols: {len(summary['symbols'])}",
        f"Date range: {summary['date_range']['start']} to {summary['date_range']['end']}",
        f"Data quality issues: {summary['data_quality']['issue_count']}",
        "",
        "No live strategy defaults were changed by this research output.",
        "",
    ]
    for factor, horizons in summary.get("factors", {}).items():
        lines.append(f"## {factor}")
        lines.append("")
        lines.append("| Horizon | Obs | IC | Mean Fwd Ret | Hit Rate | Top-Q DD | Turnover |")
        lines.append("|---:|---:|---:|---:|---:|---:|---:|")
        for horizon, metrics in horizons.items():
            lines.append(
                "| {h}d | {obs} | {ic} | {mean} | {hit} | {dd} | {turnover} |".format(
                    h=horizon,
                    obs=metrics.get("observations", 0),
                    ic=_fmt_pct(metrics.get("information_coefficient"), signed=True, pct=False),
                    mean=_fmt_pct(metrics.get("mean_forward_return"), signed=True),
                    hit=_fmt_pct(metrics.get("hit_rate")),
                    dd=_fmt_pct(metrics.get("top_quantile_max_drawdown"), signed=True),
                    turnover=_fmt_pct(metrics.get("top_quantile_turnover")),
                )
            )
        lines.append("")
    return "\n".join(lines)


def _fmt_pct(value, *, signed: bool = False, pct: bool = True) -> str:
    if value is None:
        return "-"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "-"
    if not math.isfinite(value):
        return "-"
    if pct:
        return f"{value:+.2%}" if signed else f"{value:.2%}"
    return f"{value:+.3f}" if signed else f"{value:.3f}"


def write_research_outputs(dataset: pd.DataFrame, summary: dict, output_dir: Path) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "factor_dataset.csv"
    json_path = output_dir / "factor_summary.json"
    md_path = output_dir / "factor_summary.md"
    dataset.to_csv(csv_path, index=False)
    json_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(_markdown_summary(summary), encoding="utf-8")
    return {"csv": csv_path, "json": json_path, "markdown": md_path}


def _parse_symbols(values: list[str] | None) -> list[str]:
    symbols = []
    for value in values or []:
        symbols.extend(part.strip().upper() for part in value.split(",") if part.strip())
    return list(dict.fromkeys(symbols))


def resolve_symbols(cfg: dict, *, symbols: list[str] | None, use_screener: bool, max_symbols: int | None) -> list[str]:
    if symbols:
        resolved = symbols
    elif use_screener:
        resolved = UniverseBuilder(cfg, alpaca_client=ac).get_universe()
    else:
        resolved = cfg.get("stocks", {}).get("scan_universe", [])
    resolved = list(dict.fromkeys(str(symbol).upper() for symbol in resolved if str(symbol or "").strip()))
    if max_symbols:
        resolved = resolved[:max_symbols]
    return resolved


def run_research(args) -> dict:
    cfg = get_config()
    symbols = resolve_symbols(
        cfg,
        symbols=_parse_symbols(args.symbols),
        use_screener=args.use_screener,
        max_symbols=args.max_symbols,
    )
    if not symbols:
        raise SystemExit("No symbols resolved for research.")
    bars = ac.get_stock_bars(
        symbols,
        timeframe="1Day",
        limit=args.days,
        start=args.start,
        end=args.end,
    )
    bars = {symbol: (bars or {}).get(symbol, []) for symbol in symbols}
    dataset, issues = build_factor_dataset(
        bars,
        start=args.start,
        end=args.end,
        horizons=args.horizons,
    )
    summary = compute_factor_report(
        dataset,
        horizons=args.horizons,
        data_quality_issues=issues,
        generated_at=args.generated_at,
    )
    outputs = write_research_outputs(dataset, summary, Path(args.output_dir))
    return {"summary": summary, "outputs": outputs}


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Run offline HawksTrade factor research.")
    parser.add_argument("--start", help="Inclusive YYYY-MM-DD start date for dataset rows.")
    parser.add_argument("--end", help="Inclusive YYYY-MM-DD end date for dataset rows.")
    parser.add_argument(
        "--generated-at",
        default="not_recorded",
        help="Stable timestamp label for output metadata; defaults to deterministic 'not_recorded'.",
    )
    parser.add_argument("--days", type=int, default=260, help="Daily bars to fetch per symbol.")
    parser.add_argument("--symbols", nargs="*", help="Symbols or comma-separated symbol lists.")
    parser.add_argument("--use-screener", action="store_true", help="Resolve today's universe through UniverseBuilder.")
    parser.add_argument("--max-symbols", type=int, help="Cap resolved symbols for quick research runs.")
    parser.add_argument(
        "--horizons",
        nargs="*",
        type=int,
        default=list(DEFAULT_HORIZONS),
        help="Forward-return horizons in trading days.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "reports" / "factor_research"),
        help="Directory for factor_dataset.csv, factor_summary.json, and factor_summary.md.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_research(args)
    outputs = result["outputs"]
    print(f"Factor research rows: {result['summary']['rows']}")
    print(f"CSV: {outputs['csv']}")
    print(f"JSON: {outputs['json']}")
    print(f"Markdown: {outputs['markdown']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
