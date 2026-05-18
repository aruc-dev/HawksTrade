"""
Bootstrap confidence intervals for backtest results.

Two distributions are supported:
- trade resampling, which randomises the order and composition of closed trades
- block bootstrap on daily returns, which preserves short-run autocorrelation
"""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import pandas as pd


TRADE_METRICS = ("return_pct", "max_drawdown", "profit_factor", "win_rate", "trade_sharpe")
BLOCK_METRICS = ("return_pct", "max_drawdown", "daily_sharpe")
METRICS = tuple(dict.fromkeys((*TRADE_METRICS, *BLOCK_METRICS)))


def _profit_factor(values: np.ndarray) -> float:
    gains = float(values[values > 0].sum())
    losses = abs(float(values[values < 0].sum()))
    if losses == 0:
        return math.inf if gains > 0 else 0.0
    return gains / losses


def _max_drawdown_from_returns(returns: np.ndarray) -> float:
    if returns.size == 0:
        return 0.0
    equity = np.cumprod(1.0 + returns)
    peaks = np.maximum.accumulate(equity)
    drawdowns = (equity / peaks) - 1.0
    return float(drawdowns.min()) if drawdowns.size else 0.0


def _sharpe(returns: np.ndarray, periods_per_year: float = 365.0) -> float:
    if returns.size < 2:
        return 0.0
    std = float(np.std(returns, ddof=0))
    if std <= 0 or not math.isfinite(std):
        return 0.0
    return float((np.mean(returns) / std) * math.sqrt(periods_per_year))


def trade_resample(
    trades_df: pd.DataFrame,
    n_iter: int = 10_000,
    seed: int = 42,
    *,
    initial_fund: float = 10_000.0,
) -> pd.DataFrame:
    if trades_df is None or trades_df.empty:
        return pd.DataFrame(columns=("iter", *TRADE_METRICS))

    if "pnl" in trades_df:
        pnl = trades_df["pnl"].astype(float).to_numpy()
        trade_returns = pnl / float(initial_fund or 1.0)
    elif "pnl_pct" in trades_df:
        trade_returns = trades_df["pnl_pct"].astype(float).to_numpy()
        pnl = trade_returns * float(initial_fund or 1.0)
    else:
        return pd.DataFrame(columns=("iter", *TRADE_METRICS))

    if trade_returns.size == 0:
        return pd.DataFrame(columns=("iter", *TRADE_METRICS))

    rng = np.random.default_rng(seed)
    rows = []
    sample_size = trade_returns.size
    for idx in range(int(n_iter)):
        picks = rng.integers(0, sample_size, size=sample_size)
        sampled_returns = trade_returns[picks]
        sampled_pnl = pnl[picks]
        rows.append({
            "iter": idx,
            "return_pct": float(sampled_pnl.sum() / float(initial_fund or 1.0)),
            "max_drawdown": _max_drawdown_from_returns(sampled_returns),
            "profit_factor": _profit_factor(sampled_pnl),
            "win_rate": float((sampled_pnl > 0).mean()),
            "trade_sharpe": _sharpe(sampled_returns, periods_per_year=1.0),
        })
    return pd.DataFrame(rows)


def _sample_blocks(values: np.ndarray, block_size: int, rng: np.random.Generator) -> np.ndarray:
    if values.size == 0:
        return values
    block_size = max(1, int(block_size))
    starts = rng.integers(0, values.size, size=math.ceil(values.size / block_size))
    chunks = []
    for start in starts:
        indexes = (np.arange(start, start + block_size) % values.size).astype(int)
        chunks.append(values[indexes])
    return np.concatenate(chunks)[: values.size]


def block_bootstrap_returns(
    daily_returns: Iterable[float],
    block_size: int = 5,
    n_iter: int = 10_000,
    seed: int = 42,
) -> pd.DataFrame:
    values = pd.Series(list(daily_returns), dtype="float64").dropna().to_numpy()
    if values.size == 0:
        return pd.DataFrame(columns=("iter", *BLOCK_METRICS))

    rng = np.random.default_rng(seed)
    rows = []
    for idx in range(int(n_iter)):
        sample = _sample_blocks(values, block_size, rng)
        rows.append({
            "iter": idx,
            "return_pct": float(np.prod(1.0 + sample) - 1.0),
            "max_drawdown": _max_drawdown_from_returns(sample),
            "daily_sharpe": _sharpe(sample),
        })
    return pd.DataFrame(rows)


def summarise(distribution_df: pd.DataFrame, *, drawdown_threshold: float = 0.10) -> dict:
    if distribution_df is None or distribution_df.empty:
        return {}
    summary: dict[str, dict] = {}
    for metric in METRICS:
        if metric not in distribution_df:
            continue
        values = pd.to_numeric(distribution_df[metric], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        if values.empty:
            continue
        summary[metric] = {
            "median": float(values.quantile(0.50)),
            "p05": float(values.quantile(0.05)),
            "p95": float(values.quantile(0.95)),
        }
    if "return_pct" in distribution_df:
        returns = pd.to_numeric(distribution_df["return_pct"], errors="coerce").dropna()
        summary["prob_loss"] = float((returns < 0).mean()) if not returns.empty else 0.0
    if "max_drawdown" in distribution_df:
        drawdowns = pd.to_numeric(distribution_df["max_drawdown"], errors="coerce").dropna()
        summary["prob_drawdown_gt_threshold"] = (
            float((drawdowns < -abs(drawdown_threshold)).mean()) if not drawdowns.empty else 0.0
        )
        summary["drawdown_threshold"] = abs(float(drawdown_threshold))
    return summary


def bootstrap_backtest(
    trades_df: pd.DataFrame,
    equity_curve: pd.DataFrame,
    *,
    initial_fund: float,
    n_iter: int = 10_000,
    block_size: int = 5,
    seed: int = 42,
    drawdown_threshold: float = 0.10,
) -> dict:
    trade_dist = trade_resample(trades_df, n_iter=n_iter, seed=seed, initial_fund=initial_fund)
    daily_returns = []
    if equity_curve is not None and not equity_curve.empty and "value" in equity_curve:
        daily_returns = equity_curve["value"].astype(float).pct_change().dropna().tolist()
    block_dist = block_bootstrap_returns(daily_returns, block_size=block_size, n_iter=n_iter, seed=seed)
    return {
        "iterations": int(n_iter),
        "block_size": int(block_size),
        "trade": summarise(trade_dist, drawdown_threshold=drawdown_threshold),
        "block": summarise(block_dist, drawdown_threshold=drawdown_threshold),
    }


def gate_bounds(stats: dict) -> dict:
    """Return conservative gate metrics when bootstrap summaries are present."""
    bounds = {
        "return_pct": stats.get("return_pct", 0.0),
        "max_drawdown": stats.get("max_drawdown", 0.0),
        "profit_factor": stats.get("profit_factor", 0.0),
        "daily_sharpe": stats.get("daily_sharpe", 0.0),
    }
    bootstrap = stats.get("bootstrap") or {}
    block = bootstrap.get("block") or {}
    trade = bootstrap.get("trade") or {}
    if block.get("return_pct"):
        bounds["return_pct"] = block["return_pct"]["p05"]
    if block.get("max_drawdown"):
        bounds["max_drawdown"] = block["max_drawdown"]["p05"]
    if trade.get("profit_factor"):
        bounds["profit_factor"] = trade["profit_factor"]["p05"]
    if block.get("daily_sharpe"):
        bounds["daily_sharpe"] = block["daily_sharpe"]["p05"]
    return bounds
