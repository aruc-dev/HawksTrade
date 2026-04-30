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
- `gap_up` enabled with true-gap, opening-volume pace, SMA200, and top-1 ranking guards
- `ma_crossover` enabled with a 1% daily-close max-loss exit
- `range_breakout` enabled with 20-day Donchian breakout, trend, volume, RSI, extension, and failed-breakout guards

These results enforce `trading.max_position_pct: 0.08` for every entry, including momentum/Kelly sizing. The active default now enables every configured strategy. The latest tuning raised the max-position cap from 7% to 8%; stop-loss, take-profit, daily-loss halt, and mode remain unchanged.

| Period | Final Value | Return | Trades | Win Rate | Max Drawdown |
|---|---:|---:|---:|---:|---:|
| 1 month | $9,846.53 | -1.53% | 9 | 11.1% | -2.97% |
| 2 months | $9,785.03 | -2.15% | 12 | 8.3% | -2.97% |
| 6 months | $9,664.13 | -3.36% | 30 | 20.0% | -3.74% |
| 12 months | $10,942.77 | +9.43% | 91 | 40.7% | -5.11% |

---

## 12-Month Per-Strategy Stats

| Strategy | Trades | Win Rate | Avg P&L % | Total P&L | Best | Worst |
|---|---:|---:|---:|---:|---:|---:|
| `gap_up` | 29 | 48.3% | +0.50% | $108.17 | +12.53% | -8.04% |
| `ma_crossover` | 20 | 35.0% | +3.24% | $547.83 | +18.74% | -6.32% |
| `momentum` | 33 | 33.3% | +0.03% | $51.62 | +16.20% | -15.40% |
| `range_breakout` | 7 | 71.4% | +8.49% | $481.77 | +26.50% | -4.44% |
| `rsi_reversion` | 2 | 0.0% | -11.69% | -$210.98 | -11.37% | -12.01% |

## 12-Month Quarterly Breakdown

| Quarter | Start Value | End Value | Return | Trades | Win Rate |
|---|---:|---:|---:|---:|---:|
| Q2 2025 | $10,000.00 | $10,508.39 | +5.08% | 15 | 60.0% |
| Q3 2025 | $10,500.19 | $11,428.31 | +8.84% | 36 | 50.0% |
| Q4 2025 | $11,426.38 | $11,204.59 | -1.94% | 16 | 31.2% |
| Q1 2026 | $11,204.59 | $11,113.33 | -0.81% | 15 | 26.7% |
| Q2 2026 | $11,113.33 | $10,942.77 | -1.53% | 9 | 11.1% |

---

## Strategy and Screener Comparison

| Scenario | Screener | Strategies | Return | Trades | Win Rate | Max Drawdown |
|---|---|---|---:|---:|---:|---:|
| Current all-enabled strategy set | On | `momentum`, `rsi_reversion`, `gap_up`, `ma_crossover`, `range_breakout` | +9.43% | 91 | 40.7% | -5.11% |
| Previous default strategy set | On | `momentum`, `rsi_reversion`, `ma_crossover` | +12.12% | 56 | 42.9% | -2.09% |
| Previous range-breakout default strategy set | On | `momentum`, `ma_crossover`, `range_breakout` | +11.99% | 72 | 44.4% | -2.47% |
| Previous 7% cap profit-tuned default strategy set | On | `momentum`, `ma_crossover`, `range_breakout` | +10.66% | 72 | 44.4% | -2.27% |
| Previous 5% cap capital-preserving default strategy set | On | `momentum`, `ma_crossover`, `range_breakout` | +7.52% | 72 | 44.4% | -1.73% |
| Previous tight screener, hardened default strategy set | On | `momentum`, `ma_crossover`, `range_breakout` | +1.26% | 133 | 32.3% | -7.23% |
| Historical fixed-universe run before Range Breakout hardening | Off | `momentum`, `ma_crossover`, `range_breakout` | +14.50% | 172 | 39.0% | -3.06% |
| Historical pre-cap recommended run | On | `momentum`, `ma_crossover`, `range_breakout` | +26.53% | 274 | 34.7% | -9.34% |
| Historical pre-cap fixed-universe run | Off | `momentum`, `ma_crossover`, `range_breakout` | +20.16% | 172 | 39.0% | -4.64% |

Interpretation:

- The all-enabled profile is positive over 12 months, but it is weaker than the previous default on return and drawdown. Treat this as a broader alpha-exposure profile, not a lower-risk profile.
- Range Breakout and MA Crossover generated most of the 12-month profit. Range Breakout has only 7 closed trades, so its apparent edge needs continued forward validation.
- Gap-Up was modestly profitable over 12 months but negative in the 6-month all-enabled window. Keep its dedicated gate in the monitoring loop before scaling allocation.
- RSI Reversion remains weak on its own: 2 trades, 0% win rate, -$210.98 total P&L in the 12-month run. Keep monitoring the dedicated RSI gate before scaling allocation.
- Use the current row above for live/paper expectations and treat older rows as historical baselines only.
- Raising the configured position cap from 7% to 8% increased return while keeping the costed production-gate drawdown below 2.7%. With `max_positions: 10`, this caps fully deployed gross long exposure at roughly 80% before cash, position, and asset-class constraints.

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
  --strategies momentum,rsi_reversion,gap_up,ma_crossover,range_breakout \
  --set strategies.momentum.top_n=1 \
  --set strategies.momentum.min_momentum_pct=0.10 \
  --set strategies.momentum.volume_spike_ratio=1.8 \
  --set strategies.momentum.min_breadth_coverage_pct=0.75 \
  --set strategies.ma_crossover.max_loss_exit_pct=0.01
```

---

## Validation

The production validation gate currently remains the conservative core-gate
profile from before all strategies were enabled. The requested all-enabled
profile is benchmarked above; do not assume the production gate fully validates
the all-enabled book until the gate strategy lists and thresholds are revisited.

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

Range Breakout is enabled in the active profile. Its dedicated gate validates
the hardened Donchian-style implementation before scaling allocation:

| Gate | Result | Return | Max Drawdown | Trades | Win Rate | Profit Factor | Daily Sharpe |
|---|---|---:|---:|---:|---:|---:|---:|
| `range_breakout_12m_costed` | PASS | +6.52% | -1.18% | 14 | 64.3% | 4.44 | 1.93 |
| `range_breakout_recent_30d_watch` | WARN | -0.36% | -0.36% | 1 | 0.0% | 0.00 | -5.17 |

Gap-Up is enabled in the active profile. Its dedicated gate validates the
opening-minute implementation before scaling allocation:

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
