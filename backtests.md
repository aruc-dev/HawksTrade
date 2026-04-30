# HawksTrade Backtest Summary

> **Updated:** April 30, 2026
> **Starting Capital:** $10,000
> **Momentum Exit Policy:** `profit_trailing`
> **Screener:** enabled for the recommended default
> **Simulation Only:** Historical backtest results are not a guarantee of future returns.

---

## Recommended Default Result

The current recommended configuration uses:

- Dynamic screener enabled with tightened liquidity, trend, volatility, and overextension filters
- `momentum` enabled with `top_n: 1`, `min_momentum_pct: 0.10`, `volume_spike_ratio: 1.8`, and `min_breadth_coverage_pct: 0.75`
- `rsi_reversion` enabled with crash and realised-volatility guards
- `ma_crossover` enabled with a 1% daily-close max-loss exit
- `range_breakout` disabled
- `gap_up` disabled after standalone hardening

These results enforce `trading.max_position_pct: 0.08` for every entry, including momentum/Kelly sizing. The active default now enables RSI Reversion as the stock mean-reversion sleeve and disables Range Breakout, leaving MA Crossover as the active crypto sleeve. The latest tuning raised the max-position cap from 7% to 8%; stop-loss, take-profit, daily-loss halt, and mode remain unchanged.

| Period | Final Value | Return | Trades | Win Rate | Max Drawdown |
|---|---:|---:|---:|---:|---:|
| 12 months | $11,211.57 | +12.12% | 56 | 42.9% | -2.09% |
| 6 months | $10,118.17 | +1.18% | 20 | 35.0% | -2.03% |

---

## 12-Month Per-Strategy Stats

| Strategy | Trades | Win Rate | Avg P&L % | Total P&L | Best | Worst |
|---|---:|---:|---:|---:|---:|---:|
| `ma_crossover` | 20 | 40.0% | +4.04% | $629.13 | +18.74% | -6.32% |
| `momentum` | 34 | 47.1% | +2.52% | $751.74 | +17.34% | -9.79% |
| `rsi_reversion` | 2 | 0.0% | -11.69% | -$211.30 | -11.37% | -12.01% |

## 12-Month Quarterly Breakdown

| Quarter | Start Value | End Value | Return | Trades | Win Rate |
|---|---:|---:|---:|---:|---:|
| Q2 2025 | $10,000.00 | $10,050.45 | +0.50% | 11 | 36.4% |
| Q3 2025 | $10,032.66 | $11,158.59 | +11.22% | 22 | 59.1% |
| Q4 2025 | $11,177.20 | $11,061.49 | -1.04% | 9 | 22.2% |
| Q1 2026 | $11,061.49 | $11,179.95 | +1.07% | 13 | 38.5% |
| Q2 2026 | $11,179.95 | $11,211.57 | +0.28% | 1 | 0.0% |

---

## Strategy and Screener Comparison

| Scenario | Screener | Strategies | Return | Trades | Win Rate | Max Drawdown |
|---|---|---|---:|---:|---:|---:|
| Current default strategy set | On | `momentum`, `rsi_reversion`, `ma_crossover` | +12.12% | 56 | 42.9% | -2.09% |
| Previous range-breakout default strategy set | On | `momentum`, `ma_crossover`, `range_breakout` | +11.99% | 72 | 44.4% | -2.47% |
| Previous 7% cap profit-tuned default strategy set | On | `momentum`, `ma_crossover`, `range_breakout` | +10.66% | 72 | 44.4% | -2.27% |
| Previous 5% cap capital-preserving default strategy set | On | `momentum`, `ma_crossover`, `range_breakout` | +7.52% | 72 | 44.4% | -1.73% |
| Previous tight screener, hardened default strategy set | On | `momentum`, `ma_crossover`, `range_breakout` | +1.26% | 133 | 32.3% | -7.23% |
| Historical fixed-universe run before Range Breakout hardening | Off | `momentum`, `ma_crossover`, `range_breakout` | +14.50% | 172 | 39.0% | -3.06% |
| Historical pre-cap recommended run | On | `momentum`, `ma_crossover`, `range_breakout` | +26.53% | 274 | 34.7% | -9.34% |
| Historical pre-cap fixed-universe run | Off | `momentum`, `ma_crossover`, `range_breakout` | +20.16% | 172 | 39.0% | -4.64% |

Interpretation:

- The stricter Momentum profile cut churn materially: 98 previous trades fell to 36, win rate improved from 26.5% to 44.4%, and the sleeve remained profitable under the new 8% max-position cap.
- The MA Crossover 1% daily-close max-loss exit reduced its worst observed 12-month loss from -19.25% to -6.32% while improving total contribution.
- Disabling Range Breakout reduced total trade count from 72 to 56 while the 12-month return and drawdown improved slightly in this reproduction.
- RSI Reversion is enabled in the active profile but remains weak on its own: 2 trades, 0% win rate, -$211.30 total P&L in the 12-month run. Keep monitoring the dedicated RSI gate before scaling allocation.
- Use the current row above for live/paper expectations and treat older rows as historical baselines only.
- Raising the configured position cap from 7% to 8% increased return while keeping the costed production-gate drawdown below 2.7%. With `max_positions: 10`, this caps fully deployed gross long exposure at roughly 80% before cash, position, and asset-class constraints.
- `gap_up` remains disabled in the default profile, but its standalone hardened
  sleeve now passes the dedicated cost-aware enablement gate.

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
  --strategies momentum,rsi_reversion,ma_crossover \
  --set strategies.momentum.top_n=1 \
  --set strategies.momentum.min_momentum_pct=0.10 \
  --set strategies.momentum.volume_spike_ratio=1.8 \
  --set strategies.momentum.min_breadth_coverage_pct=0.75 \
  --set strategies.ma_crossover.max_loss_exit_pct=0.01
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
python3 scheduler/run_validation_gate.py --profile production
python3 scheduler/run_validation_gate.py --profile gap
```

All checks passed at the time this document was updated.

The production validation gate is cost-aware. Its default model assumes 10 bps
adverse slippage and 5 bps fees per side. Required gates cover the default
12-month and 6-month windows plus a 12-month crypto-sleeve window. The latest
30-day crypto-sleeve window is tracked as a watch-only gate because the current
365-day MA Crossover crypto sleeve remains profitable but recent crypto trades
were weak.

Latest production-gate result:

| Gate | Result | Return | Max Drawdown | Trades | Win Rate | Profit Factor | Daily Sharpe |
|---|---|---:|---:|---:|---:|---:|---:|
| `default_12m_costed` | PASS | +11.20% | -2.22% | 56 | 42.9% | 2.00 | 2.05 |
| `default_6m_costed` | PASS | +1.21% | -1.98% | 20 | 35.0% | 1.20 | 0.69 |
| `crypto_12m_costed` | PASS | +6.21% | -1.41% | 20 | 40.0% | 3.63 | 1.96 |
| `crypto_recent_30d_watch` | WARN | -0.78% | -0.78% | 4 | 0.0% | 0.00 | -6.32 |

RSI Reversion is enabled in the active profile by configuration, but the dedicated
`--profile rsi` gate should still be used before scaling its allocation. The
latest default 12-month run had only 2 RSI trades and both lost money.

Range Breakout remains disabled in the active profile. Its dedicated enablement
gate now validates the hardened Donchian-style implementation before any live
allocation:

| Gate | Result | Return | Max Drawdown | Trades | Win Rate | Profit Factor | Daily Sharpe |
|---|---|---:|---:|---:|---:|---:|---:|
| `range_breakout_12m_costed` | PASS | +6.52% | -1.18% | 14 | 64.3% | 4.44 | 1.93 |
| `range_breakout_recent_30d_watch` | WARN | -0.36% | -0.36% | 1 | 0.0% | 0.00 | -5.17 |

Gap-Up remains disabled in the active profile. Its dedicated enablement gate
validates the opening-minute implementation before any live allocation:

| Gate | Result | Return | Max Drawdown | Trades | Win Rate | Profit Factor | Daily Sharpe |
|---|---|---:|---:|---:|---:|---:|---:|
| `gap_up_12m_costed` | PASS | +1.57% | -1.02% | 13 | 76.9% | 2.73 | 0.55 |
| `gap_up_recent_30d_watch` | PASS | +0.12% | -1.31% | 2 | 50.0% | 1.13 | 0.35 |

---

## Historical Momentum Adaptive v2.0 — A/B Comparison (90 days, April 27 2026)

The historical Momentum Adaptive v2.0 work introduced ATR-based stops, 1% risk sizing,
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
# Historical Adaptive v2.0 settings
python3 scheduler/run_backtest.py --days 90 --fund 10000 --strategies momentum \
  --set strategies.momentum.top_n=3 \
  --set strategies.momentum.min_momentum_pct=0.06 \
  --set strategies.momentum.volume_spike_ratio=1.2 \
  --set strategies.momentum.min_breadth_coverage_pct=0.0

# Pure momentum baseline (no sector/breadth filters)
python3 scheduler/run_backtest.py --days 90 --fund 10000 --strategies momentum \
  --set strategies.momentum.max_positions_per_sector=10 \
  --set strategies.momentum.breadth_red_threshold=0.0 \
  --set strategies.momentum.breadth_green_threshold=0.0 \
  --set strategies.momentum.min_breadth_coverage_pct=0.0
```
