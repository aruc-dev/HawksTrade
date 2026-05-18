# HawksTrade Backtest Summary

> **Updated:** May 17, 2026
> **Starting Capital:** $10,000
> **Momentum Exit Policy:** `profit_trailing`
> **Screener:** enabled for the guarded default
> **Simulation Only:** Historical backtest results are not a guarantee of future returns.

---

## Canonical Evidence

The single-window 12-month results below are useful context, but they are not the
capital-scaling gate. The canonical evidence source is the committed master
walk-forward report:

- `reports/walkforward_master.md`
- Regenerate with: `python3 scheduler/run_walkforward.py --profile master`
- Run the held-out OOS check only when needed: `python3 scheduler/run_walkforward.py --profile master --oos-only`

Capital does not scale unless the master walk-forward passes at the configured
stressed cost level in `config/config.yaml`. Baseline and severe walk-forward
rows are diagnostic unless they are listed under a profile's `blocking_levels`.
The locked 60-day OOS check scales the minimum trade count from the 180-day
master cadence.

Phase 1 statistical reports now also require point-in-time universe membership,
bootstrap confidence intervals with lower-bound gate metrics, sample-size risk
tier visibility, and SPA-style multiple-testing evidence before allocation
increases.

---

## Current Guarded Default Result

The current guarded configuration uses:

- Dynamic screener enabled with tightened liquidity, trend, volatility, and overextension filters
- `momentum` enabled with `top_n: 2`, `min_momentum_pct: 0.04`, `volume_confirmation_mode: pace`, `volume_pace_ratio: 1.8`, and `min_breadth_coverage_pct: 0.65`
- `relative_strength` enabled with `top_n: 1`, 20-day excess return versus SPY, 5% minimum RS, 8% minimum absolute return, 3-day blow-off cap, 1.8x volume pace, and 7-day hold
- `rsi_reversion` enabled with RSI<40 entries, crash, realised-volatility, 5-day drawdown, high-ATR entry, and 6% tail-loss guards
- `gap_up` disabled in the default profile until its standalone bootstrap and minute-fill validation improve
- `ma_crossover` enabled with 3-day follow-through, price/EMA confirmation, BTC EMA20 slope gating, a 2% daily-close max-loss exit, and a 16-day hold cap
- `range_breakout` enabled with 20-day Donchian breakout, trend, volume, RSI, extension, upper-10% close, BTC EMA20 slope gating, and failed-breakout guards

These costed results enforce `trading.max_position_pct: 0.08` for every entry, including momentum/Kelly sizing, and assume 10 bps adverse slippage plus 5 bps fees per side. The active default excludes Gap-Up until its standalone gate passes. The latest tuning adds the Relative Strength stock sleeve, BTC EMA20 slope gating, tightens crypto breakout quality, and raises the live momentum volume-pace requirement. Global stop-loss, take-profit, daily-loss halt, max-position cap, and mode remain unchanged.

| Period | Final Value | Return | Trades | Win Rate | Max Drawdown | Profit Factor | Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|
| 12 months | $10,955.01 | +9.55% | 147 | 51.0% | -1.04% | 2.45 | 3.52 |

---

## 12-Month Per-Strategy Stats

| Strategy | Trades | Win Rate | Avg P&L % | Total P&L | Best | Worst |
|---|---:|---:|---:|---:|---:|---:|
| `gap_up` | 0 | n/a | n/a | $0.00 | n/a | n/a |
| `ma_crossover` | 22 | 40.9% | +2.31% | $104.53 | +11.78% | -5.89% |
| `momentum` | 74 | 54.1% | +2.24% | $722.46 | +11.78% | -5.28% |
| `range_breakout` | 3 | 100.0% | +6.13% | $36.51 | +11.78% | +3.28% |
| `relative_strength` | 30 | 50.0% | +0.94% | $57.28 | +11.78% | -5.28% |
| `rsi_reversion` | 18 | 44.4% | -0.14% | $-3.30 | +7.81% | -4.24% |

## 12-Month Quarterly Breakdown

| Quarter | Start Value | End Value | Return | Trades | Win Rate |
|---|---:|---:|---:|---:|---:|
| Q1 2025 | $9,999.40 | $9,899.99 | -0.99% | 12 | 0.0% |
| Q2 2025 | $9,899.99 | $9,967.72 | +0.68% | 27 | 48.1% |
| Q3 2025 | $9,951.30 | $10,247.80 | +2.98% | 43 | 53.5% |
| Q4 2025 | $10,247.04 | $10,557.24 | +3.03% | 43 | 55.8% |
| Q1 2026 | $10,556.92 | $10,955.01 | +3.77% | 22 | 68.2% |

---

## Strategy and Screener Comparison

| Scenario | Screener | Strategies | Return | Trades | Win Rate | Max Drawdown |
|---|---|---|---:|---:|---:|---:|
| Current guarded default strategy set | On | `momentum`, `relative_strength`, `rsi_reversion`, `ma_crossover`, `range_breakout` | +9.55% | 147 | 51.0% | -1.04% |
| Previous tail-risk-hardened all-enabled strategy set | On | `momentum`, `rsi_reversion`, `gap_up`, `ma_crossover`, `range_breakout` | +20.70% | 112 | 53.6% | -1.92% |
| Previous moderate-risk all-enabled strategy set | On | `momentum`, `rsi_reversion`, `gap_up`, `ma_crossover`, `range_breakout` | +22.87% | 111 | 48.6% | -4.12% |
| Previous all-enabled strategy set | On | `momentum`, `rsi_reversion`, `gap_up`, `ma_crossover`, `range_breakout` | +9.43% | 91 | 40.7% | -5.11% |
| Previous default strategy set | On | `momentum`, `rsi_reversion`, `ma_crossover` | +12.12% | 56 | 42.9% | -2.09% |
| Previous range-breakout default strategy set | On | `momentum`, `ma_crossover`, `range_breakout` | +11.99% | 72 | 44.4% | -2.47% |
| Previous 7% cap profit-tuned default strategy set | On | `momentum`, `ma_crossover`, `range_breakout` | +10.66% | 72 | 44.4% | -2.27% |
| Previous 5% cap capital-preserving default strategy set | On | `momentum`, `ma_crossover`, `range_breakout` | +7.52% | 72 | 44.4% | -1.73% |
| Previous tight screener, hardened default strategy set | On | `momentum`, `ma_crossover`, `range_breakout` | +1.26% | 133 | 32.3% | -7.23% |
| Historical fixed-universe run before Range Breakout hardening | Off | `momentum`, `ma_crossover`, `range_breakout` | +14.50% | 172 | 39.0% | -3.06% |
| Historical pre-cap recommended run | On | `momentum`, `ma_crossover`, `range_breakout` | +26.53% | 274 | 34.7% | -9.34% |
| Historical pre-cap fixed-universe run | Off | `momentum`, `ma_crossover`, `range_breakout` | +20.16% | 172 | 39.0% | -4.64% |

Interpretation:

- The current default profile returns +9.55% over 12 months with a -1.04% max drawdown under the costed model, but production validation still blocks because the 12-month return lower bound does not clear the required production floor.
- The Relative Strength sleeve improved the default 12-month lower-bound return from +3.32% to +4.98%, but it is not enough by itself to clear the +10% conservative production floor.
- Range Breakout and MA Crossover remain positive crypto contributors. Range Breakout has only 3 closed trades in the latest 12-month run, so its edge needs continued forward validation before scaling allocation.
- Gap-Up has a positive point estimate but failed its standalone bootstrap gate, so it is disabled in the default profile until the dedicated gate and minute-fill validation improve.
- Conditional RSI Reversion remains in the default profile as a bear/chop sleeve, but its standalone paper/readiness gate is still blocking. Keep monitoring the dedicated RSI gate before scaling allocation.
- Use the current row above for live/paper expectations and treat older rows as historical baselines only.
- The configured position cap remains 8%. With `max_positions: 10`, this caps fully deployed gross long exposure at roughly 80% before cash, position, and asset-class constraints.

---

## Reproduction Commands

Recommended default:

```bash
python3 scheduler/run_backtest.py --days 365 --fund 10000 --end-date 04/29/2026 --exit-policy profit_trailing --screener \
  --slippage-bps 10 --fee-bps 5 --min-fee 0
```

Fixed-universe comparison:

```bash
python3 scheduler/run_backtest.py --days 365 --fund 10000 --end-date 04/29/2026 --exit-policy profit_trailing --no-screener
```

Experiment-only overrides without editing `config/config.yaml`:

```bash
python3 scheduler/run_backtest.py --days 365 --fund 10000 --end-date 04/29/2026 --screener \
  --strategies momentum,relative_strength,rsi_reversion,ma_crossover,range_breakout \
  --set strategies.momentum.top_n=2 \
  --set strategies.momentum.min_momentum_pct=0.04 \
  --set strategies.momentum.volume_pace_ratio=1.8 \
  --set strategies.momentum.min_breadth_coverage_pct=0.65 \
  --set strategies.ma_crossover.max_loss_exit_pct=0.02 \
  --slippage-bps 10 --fee-bps 5 --min-fee 0
```

---

## Validation

The production validation gate now validates the current costed production gate,
including `range_breakout` and excluding `gap_up` from the default strategy
lists until its standalone gate passes. Use the production gate thresholds in
`config/config.yaml` before scaling live allocation.

The latest implementation was also checked with:

```bash
python3 -m unittest discover -v
python3 -W error::DeprecationWarning -m unittest discover
python3 -m compileall analysis core strategies scheduler tracking tests
python3 scheduler/run_scan.py --dry-run
python3 scheduler/run_risk_check.py --dry-run
python3 scheduler/run_report.py
python3 scheduler/run_backtest.py --days 30 --fund 10000
python3 scheduler/run_walkforward.py --quick --no-write-report --no-artifacts
python3 scheduler/run_backtest.py --days 365 --fund 10000 --end-date 04/29/2026 --exit-policy profit_trailing --screener --slippage-bps 10 --fee-bps 5 --min-fee 0
python3 scheduler/run_validation_gate.py --profile production
python3 scheduler/run_validation_gate.py --profile rsi
python3 scheduler/run_validation_gate.py --profile gap
```

The quick walk-forward now passes at the stressed capital-scaling level (2/3
windows). Production validation remains blocking: the 12-month default gate
fails the required return lower bound, and the crypto 12-month gate fails return
and profit-factor lower bounds. RSI and Gap-Up standalone gates also remain
blocking until forward-paper/bootstrap evidence improves.

The production validation gate is cost-aware. Its default model assumes 10 bps
adverse slippage and 5 bps fees per side. Required gates cover the default
12-month and 6-month windows plus a 12-month crypto-sleeve window. The approved
moderate-risk profile allows up to 6% costed drawdown on the 12-month default
gate and 4% on the 6-month default gate. The latest 30-day crypto-sleeve window
is tracked as a watch-only gate because recent crypto opportunity can be sparse
or choppy even when the 12-month sleeve remains profitable.

Latest production-gate result:

| Gate | Result | Return | Max Drawdown | Trades | Win Rate | Profit Factor | Daily Sharpe |
|---|---|---:|---:|---:|---:|---:|---:|
| `default_12m_costed` | FAIL | +9.55% | -1.04% | 147 | 51.0% | 2.45 | 3.52 |
| `default_6m_costed` | PASS | +6.45% | -0.74% | 84 | 58.3% | 3.41 | 4.84 |
| `crypto_12m_costed` | FAIL | +1.41% | -0.35% | 25 | 48.0% | 2.72 | 1.93 |
| `crypto_recent_30d_watch` | SKIP | n/a | n/a | 0 | n/a | n/a | n/a |

Bootstrap gate bounds for the failing required windows were: default 12-month
return lower bound +4.98% versus +10.00% required; crypto 12-month return lower
bound +0.06% versus +5.00% required and profit-factor lower bound 1.28 versus
2.00 required.

RSI Reversion is enabled in the active profile by configuration, but the dedicated
`--profile rsi` gate should still be used before scaling its allocation. The
latest costed RSI-only 12-month backtest gate passed at +3.44% return, -0.86%
max drawdown, 29 trades, 62.1% win rate, 2.02 profit factor, and 1.55 daily
Sharpe. The forward paper-trading gate remains pending until it accumulates
60 paper days and 20 closed RSI trades.

Range Breakout is enabled in the active profile. Its dedicated gate validates
the hardened Donchian-style implementation before scaling allocation:

| Gate | Result | Return | Max Drawdown | Trades | Win Rate | Profit Factor | Daily Sharpe |
|---|---|---:|---:|---:|---:|---:|---:|
| `range_breakout_12m_costed` | PASS | +5.70% | -0.92% | 10 | 70.0% | 5.91 | 1.84 |
| `range_breakout_recent_30d_watch` | WARN | +0.00% | +0.00% | 0 | 0.0% | 0.00 | 0.00 |

Gap-Up is disabled in the active default profile. Its dedicated gate validates
the opening-minute implementation with the dynamic screener enabled before
re-enabling or scaling allocation:

| Gate | Result | Return | Max Drawdown | Trades | Win Rate | Profit Factor | Daily Sharpe |
|---|---|---:|---:|---:|---:|---:|---:|
| `gap_up_12m_costed` | FAIL | +0.22% | -0.22% | 11 | 72.7% | 2.30 | 0.33 |
| `gap_up_recent_30d_watch` | SKIP | n/a | n/a | 0 | n/a | n/a | n/a |

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
