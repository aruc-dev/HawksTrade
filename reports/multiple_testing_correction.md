# Multiple-Testing Correction

Phase 1 adds a Hansen SPA-style correction for strategy and parameter-grid
selection bias.

## Implemented Workflow

1. Generate a daily returns matrix for a strategy grid:

   ```bash
   python3 scheduler/run_backtest.py --grid momentum --grid-output reports/spa/momentum_returns.csv
   ```

2. Add a benchmark return column named `benchmark` to the matrix. Use `SPY` for
   stock strategies and `BTC/USD` for crypto strategies.

3. Run the SPA analysis:

   ```bash
   python3 scheduler/run_spa_analysis.py --returns-csv reports/spa/momentum_returns.csv --output reports/spa/momentum_spa.md
   ```

## Search-Space Catalog

| Strategy | Grid Size | Primary Parameters |
|---|---:|---|
| `momentum` | 27 | `top_n`, `min_momentum_pct`, `atr_multiplier` |
| `gap_up` | 27 | `min_gap_pct`, `volume_multiplier`, `hold_days` |
| `ma_crossover` | 27 | `fast_ema`, `slow_ema`, `hold_days` |
| `range_breakout` | 27 | `breakout_pct`, `volume_multiplier`, `hold_days` |
| `rsi_reversion` | 27 | `oversold_threshold`, `vix_multiplier`, `hold_days` |

## Decision Rule

| SPA p-value | Interpretation | Allocation Rule |
|---:|---|---|
| `< 0.05` | Strong evidence after correction | Eligible for full sample-size-allowed risk |
| `0.05 - 0.20` | Weak evidence | Keep capped and collect more OOS evidence |
| `>= 0.20` | Not distinguishable from selection luck | Pause scaling; do not increase allocation |

The initial committed report documents the workflow and catalog. Strategy-level
p-values should be refreshed after the grid jobs complete and before any
allocation increase.
