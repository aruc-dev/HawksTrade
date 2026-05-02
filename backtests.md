# HawksTrade Backtest Summary

> **Updated:** May 2, 2026
> **Starting Capital:** $10,000
> **Momentum Exit Policy:** `profit_trailing`
> **Screener:** enabled for the recommended default
> **Simulation Only:** Historical backtest results are not a guarantee of future returns.

---

## Recommended Default Result

The current recommended configuration uses:

- Dynamic screener enabled with tightened liquidity, trend, volatility, and overextension filters
- `momentum` enabled with `top_n: 2`, `min_momentum_pct: 0.08`, `volume_spike_ratio: 2.0`, and `min_breadth_coverage_pct: 0.65`
- `rsi_reversion` enabled with RSI<40 entries, crash, realised-volatility, 5-day drawdown, high-ATR entry, and 6% tail-loss guards
- `gap_up` enabled with true-gap, opening-volume pace, SMA200, and top-1 ranking guards
- `ma_crossover` enabled with 3-day follow-through, price/EMA confirmation, a 2% daily-close max-loss exit, and a 16-day hold cap
- `range_breakout` enabled with 20-day Donchian breakout, trend, volume, RSI, extension, upper-range close, and failed-breakout guards

These costed results enforce `trading.max_position_pct: 0.08` for every entry, including momentum/Kelly sizing, and assume 10 bps adverse slippage plus 5 bps fees per side. The active default enables every configured strategy. The latest tuning hardens RSI tail risk while preserving a positive recent crypto sleeve. Global stop-loss, take-profit, daily-loss halt, max-position cap, and mode remain unchanged.

| Period | Final Value | Return | Trades | Win Rate | Max Drawdown |
|---|---:|---:|---:|---:|---:|
| 1 month | $10,403.21 | +4.03% | 10 | 70.0% | -0.68% |
| 2 months | $10,186.38 | +1.86% | 10 | 40.0% | -1.30% |
| 6 months | $10,503.47 | +5.03% | 37 | 48.6% | -2.04% |
| 12 months | $12,070.22 | +20.70% | 112 | 53.6% | -1.92% |

---

## 12-Month Per-Strategy Stats

| Strategy | Trades | Win Rate | Avg P&L % | Total P&L | Best | Worst |
|---|---:|---:|---:|---:|---:|---:|
| `gap_up` | 12 | 75.0% | +3.74% | $363.73 | +12.00% | -3.50% |
| `ma_crossover` | 20 | 45.0% | +2.12% | $367.42 | +12.00% | -4.13% |
| `momentum` | 44 | 43.2% | +1.32% | $544.00 | +12.00% | -10.11% |
| `range_breakout` | 7 | 71.4% | +6.12% | $350.64 | +12.00% | -4.44% |
| `rsi_reversion` | 29 | 62.1% | +1.74% | $444.42 | +12.00% | -3.95% |

## 12-Month Quarterly Breakdown

| Quarter | Start Value | End Value | Return | Trades | Win Rate |
|---|---:|---:|---:|---:|---:|
| Q2 2025 | $10,000.00 | $10,486.19 | +4.86% | 14 | 71.4% |
| Q3 2025 | $10,408.02 | $11,026.44 | +5.94% | 45 | 44.4% |
| Q4 2025 | $11,092.81 | $11,442.38 | +3.15% | 21 | 61.9% |
| Q1 2026 | $11,442.38 | $11,602.40 | +1.40% | 22 | 45.5% |
| Q2 2026 | $11,602.40 | $12,070.22 | +4.03% | 10 | 70.0% |

---

## Strategy and Screener Comparison

| Scenario | Screener | Strategies | Return | Trades | Win Rate | Max Drawdown |
|---|---|---|---:|---:|---:|---:|
| Current tail-risk-hardened all-enabled strategy set | On | `momentum`, `rsi_reversion`, `gap_up`, `ma_crossover`, `range_breakout` | +20.70% | 112 | 53.6% | -1.92% |
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

- The current all-enabled profile returns +20.70% over 12 months with a -1.92% max drawdown under the costed model. It improves RSI contribution while preserving the MA Crossover tail controls added under the 2% crypto max-loss setting.
- Range Breakout and MA Crossover remain positive crypto contributors. Range Breakout still has only 7 closed trades, so its edge needs continued forward validation before scaling allocation.
- Gap-Up was profitable over 12 months and improved after requiring the prior completed close to already be above SMA200, which removed recent gap-through-trend weakness. Keep its dedicated gate in the monitoring loop before scaling allocation.
- RSI Reversion contributed $444.42 with 29 trades, 62.1% win rate, and a -3.95% worst trade after moving to RSI<40, a 5% ATR/price ceiling, and a 10% recent-drawdown guard. Keep monitoring the dedicated RSI gate before scaling allocation.
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
  --strategies momentum,rsi_reversion,gap_up,ma_crossover,range_breakout \
  --set strategies.momentum.top_n=2 \
  --set strategies.momentum.min_momentum_pct=0.08 \
  --set strategies.momentum.volume_spike_ratio=2.0 \
  --set strategies.momentum.min_breadth_coverage_pct=0.65 \
  --set strategies.ma_crossover.max_loss_exit_pct=0.02 \
  --slippage-bps 10 --fee-bps 5 --min-fee 0
```

---

## Validation

The production validation gate now validates the current costed production gate,
including `gap_up` and `range_breakout` in the configured strategy lists. The
requested all-enabled profile is benchmarked above; use the production gate
thresholds in `config/config.yaml` before scaling live allocation.

The latest implementation was also checked with:

```bash
python3 -m unittest discover -v
python3 -W error::DeprecationWarning -m unittest discover
python3 -m compileall core strategies scheduler tracking tests
python3 scheduler/run_scan.py --dry-run
python3 scheduler/run_risk_check.py --dry-run
python3 scheduler/run_report.py
python3 scheduler/run_backtest.py --days 30 --fund 10000
python3 scheduler/run_backtest.py --days 365 --fund 10000 --end-date 04/29/2026 --exit-policy profit_trailing --screener --slippage-bps 10 --fee-bps 5 --min-fee 0
python3 scheduler/run_validation_gate.py --profile production
python3 scheduler/run_validation_gate.py --profile rsi
python3 scheduler/run_validation_gate.py --profile gap
```

All checks passed at the time this document was updated.

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
| `default_12m_costed` | PASS | +20.70% | -1.92% | 112 | 53.6% | 2.29 | 3.31 |
| `default_6m_costed` | PASS | +5.03% | -2.04% | 37 | 48.6% | 2.00 | 2.29 |
| `crypto_12m_costed` | PASS | +6.48% | -1.61% | 27 | 48.1% | 2.84 | 2.23 |
| `crypto_recent_30d_watch` | PASS | +0.77% | -0.69% | 3 | 66.7% | 4.20 | 2.46 |

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

Gap-Up is enabled in the active profile. Its dedicated gate validates the
opening-minute implementation with the dynamic screener enabled before scaling
allocation:

| Gate | Result | Return | Max Drawdown | Trades | Win Rate | Profit Factor | Daily Sharpe |
|---|---|---:|---:|---:|---:|---:|---:|
| `gap_up_12m_costed` | PASS | +1.45% | -0.99% | 11 | 72.7% | 2.86 | 0.54 |
| `gap_up_recent_30d_watch` | PASS | +0.52% | -0.01% | 1 | 100.0% | inf | 6.11 |

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
