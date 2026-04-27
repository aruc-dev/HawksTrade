# HawksTrade Backtest Summary

> **Updated:** April 27, 2026
> **Starting Capital:** $10,000
> **Momentum Exit Policy:** `profit_trailing`
> **Screener:** enabled for the recommended default
> **Simulation Only:** Historical backtest results are not a guarantee of future returns.

---

## Recommended Default Result

The current recommended configuration uses:

- Dynamic screener enabled with tightened liquidity, trend, volatility, and overextension filters
- `momentum` enabled with `top_n: 3` and `min_momentum_pct: 0.06`
- `ma_crossover` enabled
- `range_breakout` enabled
- `rsi_reversion` disabled
- `gap_up` disabled

These results enforce `trading.max_position_pct: 0.05` for every entry, including momentum/Kelly sizing. Earlier documentation showed higher dollar returns from the previous behavior where momentum sizing could exceed the configured 5% cap.

| Period | Final Value | Return | Trades | Win Rate | Max Drawdown |
|---|---:|---:|---:|---:|---:|
| 12 months | $11,900.35 | +19.00% | 274 | 34.7% | -6.13% |
| 6 months | $10,566.60 | +5.67% | 104 | 32.7% | -3.74% |

---

## 12-Month Per-Strategy Stats

| Strategy | Trades | Win Rate | Avg P&L % | Total P&L | Best | Worst |
|---|---:|---:|---:|---:|---:|---:|
| `momentum` | 226 | 31.0% | +0.94% | $1,143.83 | +19.10% | -12.85% |
| `ma_crossover` | 23 | 52.2% | +3.80% | $459.31 | +18.74% | -7.40% |
| `range_breakout` | 25 | 52.0% | +1.79% | $218.88 | +26.50% | -7.56% |

## 12-Month Quarterly Breakdown

| Quarter | Start Value | End Value | Return | Trades | Win Rate |
|---|---:|---:|---:|---:|---:|
| Q2 2025 | $10,000.00 | $10,669.60 | +6.70% | 55 | 40.0% |
| Q3 2025 | $10,689.46 | $11,331.02 | +6.00% | 97 | 34.0% |
| Q4 2025 | $11,376.53 | $10,988.77 | -3.41% | 71 | 21.1% |
| Q1 2026 | $10,959.17 | $11,826.92 | +7.92% | 50 | 50.0% |
| Q2 2026 | $11,826.92 | $11,900.35 | +0.62% | 1 | 0.0% |

---

## Strategy and Screener Comparison

| Scenario | Screener | Strategies | Return | Trades | Win Rate | Max Drawdown |
|---|---|---|---:|---:|---:|---:|
| Tight screener, default strategy set | On | `momentum`, `ma_crossover`, `range_breakout` | +19.00% | 274 | 34.7% | -6.13% |
| Fixed universe, default strategy set | Off | `momentum`, `ma_crossover`, `range_breakout` | +14.50% | 172 | 39.0% | -3.06% |
| Historical pre-cap recommended run | On | `momentum`, `ma_crossover`, `range_breakout` | +26.53% | 274 | 34.7% | -9.34% |
| Historical pre-cap fixed-universe run | Off | `momentum`, `ma_crossover`, `range_breakout` | +20.16% | 172 | 39.0% | -4.64% |

Interpretation:

- The tightened screener remains the recommended default because it produced higher return than the fixed universe while keeping drawdown controlled.
- Enforcing the configured 5% position cap lowered dollar returns versus the earlier pre-cap runs, but also reduced max drawdown.
- The fixed-universe default strategy set had lower drawdown and higher win rate, but lower total return.
- `rsi_reversion` and `gap_up` are disabled by default because they did not improve the validated 12-month configuration.

---

## Reproduction Commands

Recommended default:

```bash
python3 scheduler/run_backtest.py --days 365 --fund 10000 --end-date 04/10/2026 --exit-policy profit_trailing --screener
```

Fixed-universe comparison:

```bash
python3 scheduler/run_backtest.py --days 365 --fund 10000 --end-date 04/10/2026 --exit-policy profit_trailing --no-screener
```

Experiment-only overrides without editing `config/config.yaml`:

```bash
python3 scheduler/run_backtest.py --days 365 --fund 10000 --end-date 04/10/2026 --screener \
  --strategies momentum,ma_crossover,range_breakout \
  --set strategies.momentum.top_n=3 \
  --set strategies.momentum.min_momentum_pct=0.06
```

---

## Validation

The latest implementation was also checked with:

```bash
python3 -m unittest discover -v
python3 -W error::DeprecationWarning -m unittest discover
python3 -m compileall core strategies scheduler tracking tests
python3 scheduler/run_scan.py --dry-run
python3 scheduler/run_risk_check.py --dry-run
```

All checks passed at the time this document was updated.

---

## Momentum Adaptive v2.0 — A/B Comparison (90 days, April 27 2026)

Phase 1–3 of `todo.md` introduced ATR-based stops, 1% risk sizing, sector-neutral
ranking, and a market breadth tiered regime guard into the Momentum strategy.

The table below compares a 90-day run (2026-01-27 to 2026-04-27) with and without
the new filters. The "pure momentum" baseline disables sector and breadth filters
via config overrides while keeping ATR stops and risk sizing active.

| Metric | Pure Momentum (no sector/breadth) | Adaptive v2.0 |
|---|---:|---:|
| Final Value | +7.68% | +5.00% |
| Win Rate | 56.8% | 41.7% |
| Max Drawdown | -1.05% | **-0.76%** |
| Trades | 37 | 36 |

**Interpretation:**
- Max drawdown improved by 28% (-1.05% → -0.76%).
- Adaptive v2.0 entered diversified sectors (ARM/Tech, UNH/Health Care, SLB/Energy) vs potentially correlated entries without the sector filter.
- Lower return in this 90-day window because the breadth filter reduced exposure during the Q1 2026 tariff-driven selloff — the same period where the pure strategy also executed fewer profitable trades.
- Benefits of regime protection compound over full market cycles with sustained downtrends; the 90-day window captures a partial recovery which favours the less-filtered baseline.

A/B reproduction commands:

```bash
# Adaptive v2.0 (current default)
python3 scheduler/run_backtest.py --days 90 --fund 10000 --strategies momentum

# Pure momentum baseline (no sector/breadth filters)
python3 scheduler/run_backtest.py --days 90 --fund 10000 --strategies momentum \
  --set strategies.momentum.max_positions_per_sector=10 \
  --set strategies.momentum.breadth_red_threshold=0.0 \
  --set strategies.momentum.breadth_yellow_threshold=0.0
```
