# HawksTrade Backtest Summary

> **Updated:** April 29, 2026
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

These results enforce `trading.max_position_pct: 0.05` for every entry, including momentum/Kelly sizing, and include the hardened Range Breakout implementation with ranked signals, extension/RSI guards, and failed-breakout exits.

| Period | Final Value | Return | Trades | Win Rate | Max Drawdown |
|---|---:|---:|---:|---:|---:|
| 12 months | $10,125.60 | +1.26% | 133 | 32.3% | -7.23% |
| 6 months | $9,808.66 | -1.91% | 49 | 26.5% | -4.62% |

---

## 12-Month Per-Strategy Stats

| Strategy | Trades | Win Rate | Avg P&L % | Total P&L | Best | Worst |
|---|---:|---:|---:|---:|---:|---:|
| `ma_crossover` | 18 | 50.0% | +1.46% | $130.63 | +18.74% | -19.25% |
| `momentum` | 98 | 26.5% | -0.13% | -$74.79 | +16.77% | -17.03% |
| `range_breakout` | 17 | 47.1% | +0.05% | $0.29 | +13.47% | -6.74% |

## 12-Month Quarterly Breakdown

| Quarter | Start Value | End Value | Return | Trades | Win Rate |
|---|---:|---:|---:|---:|---:|
| Q2 2025 | $10,000.00 | $10,144.11 | +1.44% | 27 | 37.0% |
| Q3 2025 | $10,141.28 | $10,590.34 | +4.43% | 47 | 42.6% |
| Q4 2025 | $10,582.64 | $9,959.54 | -5.89% | 31 | 6.5% |
| Q1 2026 | $9,959.54 | $10,056.13 | +0.97% | 28 | 39.3% |
| Q2 2026 | $10,056.13 | $10,125.60 | +0.69% | 0 | 0.0% |

---

## Strategy and Screener Comparison

| Scenario | Screener | Strategies | Return | Trades | Win Rate | Max Drawdown |
|---|---|---|---:|---:|---:|---:|
| Current tight screener, hardened default strategy set | On | `momentum`, `ma_crossover`, `range_breakout` | +1.26% | 133 | 32.3% | -7.23% |
| Historical fixed-universe run before Range Breakout hardening | Off | `momentum`, `ma_crossover`, `range_breakout` | +14.50% | 172 | 39.0% | -3.06% |
| Historical pre-cap recommended run | On | `momentum`, `ma_crossover`, `range_breakout` | +26.53% | 274 | 34.7% | -9.34% |
| Historical pre-cap fixed-universe run | Off | `momentum`, `ma_crossover`, `range_breakout` | +20.16% | 172 | 39.0% | -4.64% |

Interpretation:

- The hardened Range Breakout sleeve was close to flat over the 12-month reproduction: 17 trades, 47.1% win rate, $0.29 total P&L. Its production-readiness value is in stricter selection, ranked crypto entries, and explicit failed-breakout exits rather than a materially larger historical contribution in this window.
- The current default reproduction is lower than the historical April 27 snapshot; use the current row above for live/paper expectations and treat older rows as historical baselines only.
- Enforcing the configured 5% position cap lowered dollar returns versus the earlier pre-cap runs, but also reduced position-size risk.
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
python3 scheduler/run_report.py
python3 scheduler/run_backtest.py --days 30 --fund 10000
python3 scheduler/run_backtest.py --days 365 --fund 10000 --end-date 04/10/2026 --exit-policy profit_trailing --screener
```

All checks passed at the time this document was updated.

---

## Momentum Adaptive v2.0 — A/B Comparison (90 days, April 27 2026)

The Momentum Adaptive v2.0 work introduced ATR-based stops, 1% risk sizing,
sector-neutral ranking, and a market breadth tiered regime guard into the
Momentum strategy.

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
