"""
Multiple-testing correction using a Hansen SPA-style bootstrap.

If ``arch.bootstrap.SPA`` is installed this module can be extended to delegate
to it. The committed implementation uses the same core idea for this codebase:
studentised strategy-vs-benchmark return differentials and a block bootstrap of
the maximum statistic across the searched variants.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SPAResult:
    p_value: float
    statistic: float
    best_strategy: str
    variants: int
    observations: int
    method: str = "centered_block_bootstrap_spa"

    def as_dict(self) -> dict:
        return {
            "p_value": self.p_value,
            "statistic": self.statistic,
            "best_strategy": self.best_strategy,
            "variants": self.variants,
            "observations": self.observations,
            "method": self.method,
        }


def _as_frame(strategy_returns) -> pd.DataFrame:
    frame = pd.DataFrame(strategy_returns).astype(float)
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna(axis=0, how="any")
    if frame.empty:
        raise ValueError("strategy_returns must contain at least one complete observation")
    return frame


def _as_series(benchmark_returns, index) -> pd.Series:
    series = pd.Series(benchmark_returns, index=index, dtype="float64")
    series = series.replace([np.inf, -np.inf], np.nan)
    if series.isna().any():
        valid = ~series.isna()
        series = series.loc[valid]
    if series.empty:
        raise ValueError("benchmark_returns must contain observations")
    return series


def _sample_block_rows(values: np.ndarray, block_size: int, rng: np.random.Generator) -> np.ndarray:
    n_obs = values.shape[0]
    starts = rng.integers(0, n_obs, size=math.ceil(n_obs / block_size))
    pieces = []
    for start in starts:
        indexes = (np.arange(start, start + block_size) % n_obs).astype(int)
        pieces.append(values[indexes, :])
    return np.concatenate(pieces, axis=0)[:n_obs, :]


def spa_test(
    strategy_returns,
    benchmark_returns,
    *,
    n_boot: int = 5_000,
    block_size: int = 5,
    seed: int = 42,
) -> SPAResult:
    strategies = _as_frame(strategy_returns)
    benchmark = _as_series(benchmark_returns, strategies.index)
    aligned = strategies.loc[benchmark.index]
    if aligned.empty:
        raise ValueError("strategy_returns and benchmark_returns have no overlapping observations")

    differentials = aligned.subtract(benchmark, axis=0)
    values = differentials.to_numpy(dtype=float)
    n_obs, n_variants = values.shape
    if n_obs < 3:
        raise ValueError("SPA requires at least three observations")

    means = values.mean(axis=0)
    std = values.std(axis=0, ddof=0)
    std = np.where(std <= 1e-12, 1e-12, std)
    observed_stats = np.sqrt(n_obs) * means / std
    best_idx = int(np.argmax(observed_stats))
    observed = float(observed_stats[best_idx])

    centered = values - means
    rng = np.random.default_rng(seed)
    boot_stats = np.empty(int(n_boot), dtype=float)
    for idx in range(int(n_boot)):
        sample = _sample_block_rows(centered, max(1, int(block_size)), rng)
        sample_means = sample.mean(axis=0)
        sample_std = sample.std(axis=0, ddof=0)
        sample_std = np.where(sample_std <= 1e-12, 1e-12, sample_std)
        boot_stats[idx] = float(np.max(np.sqrt(n_obs) * sample_means / sample_std))

    p_value = float((np.sum(boot_stats >= observed) + 1.0) / (len(boot_stats) + 1.0))
    return SPAResult(
        p_value=p_value,
        statistic=observed,
        best_strategy=str(aligned.columns[best_idx]),
        variants=n_variants,
        observations=n_obs,
    )


def strategy_search_space_catalog() -> dict[str, list[dict]]:
    """Return the Phase 1 default parameter grids for enabled strategy audits."""
    grids = {
        "momentum": {
            "strategies.momentum.top_n": [2, 3, 5],
            "strategies.momentum.min_momentum_pct": [0.04, 0.06, 0.08],
            "strategies.momentum.min_momentum_atr_mult": [0.0, 1.5, 2.0],
            "strategies.momentum.atr_multiplier": [1.0, 1.2, 1.5],
        },
        "relative_strength": {
            "strategies.relative_strength.top_n": [1, 2, 3],
            "strategies.relative_strength.min_rs_pct": [0.01, 0.02, 0.03],
            "strategies.relative_strength.min_abs_return_pct": [0.00, 0.03, 0.05],
            "strategies.relative_strength.lookback_days": [15, 20, 30],
        },
        "gap_up": {
            "strategies.gap_up.min_gap_pct": [0.04, 0.05, 0.06],
            "strategies.gap_up.volume_multiplier": [1.1, 1.3, 1.5],
            "strategies.gap_up.hold_days": [1, 2, 3],
        },
        "ma_crossover": {
            "strategies.ma_crossover.fast_ema": [5, 6, 8],
            "strategies.ma_crossover.slow_ema": [18, 21, 26],
            "strategies.ma_crossover.hold_days": [10, 16, 20],
        },
        "range_breakout": {
            "strategies.range_breakout.breakout_pct": [0.004, 0.006, 0.008],
            "strategies.range_breakout.volume_multiplier": [2.0, 2.5, 3.0],
            "strategies.range_breakout.hold_days": [10, 14, 20],
        },
        "rsi_reversion": {
            "strategies.rsi_reversion.oversold_threshold": [35, 40, 45],
            "strategies.rsi_reversion.vix_multiplier": [0.85, 0.95, 1.05],
            "strategies.rsi_reversion.hold_days": [8, 10, 12],
        },
    }
    catalog: dict[str, list[dict]] = {}
    for strategy, params in grids.items():
        keys = list(params.keys())
        variants = []
        for values in itertools.product(*(params[key] for key in keys)):
            variants.append(dict(zip(keys, values)))
        catalog[strategy] = variants
    return catalog


def returns_matrix_from_csv(path: str, *, benchmark_column: str = "benchmark") -> tuple[pd.DataFrame, pd.Series]:
    frame = pd.read_csv(path)
    if "date" in frame:
        frame["date"] = pd.to_datetime(frame["date"])
        frame = frame.set_index("date")
    if benchmark_column not in frame:
        raise ValueError(f"Missing benchmark column: {benchmark_column}")
    benchmark = frame[benchmark_column].astype(float)
    strategies = frame.drop(columns=[benchmark_column]).astype(float)
    return strategies, benchmark
