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
- `momentum` enabled with `top_n: 2`, `min_momentum_pct: 0.08`, `volume_spike_ratio: 2.0`, and `min_breadth_coverage_pct: 0.65`
- `rsi_reversion` enabled with crash and realised-volatility guards
- `gap_up` enabled with true-gap, opening-volume pace, SMA200, and top-1 ranking guards
- `ma_crossover` enabled with a 1% daily-close max-loss exit
- `range_breakout` enabled with 20-day Donchian breakout, trend, volume, RSI, extension, and failed-breakout guards

These results enforce `trading.max_position_pct: 0.08` for every entry, including momentum/Kelly sizing. The active default enables every configured strategy. The latest tuning increases opportunity through strategy-level signal thresholds while leaving stop-loss, take-profit, daily-loss halt, max-position cap, and mode unchanged.

| Period | Final Value | Return | Trades | Win Rate | Max Drawdown |
|---|---:|---:|---:|---:|---:|
| 1 month | $10,308.70 | +3.09% | 11 | 45.5% | -1.09% |
| 2 months | $10,258.78 | +2.59% | 14 | 35.7% | -1.09% |
| 6 months | $10,875.92 | +8.76% | 44 | 47.7% | -2.22% |
| 12 months | $12,286.93 | +22.87% | 111 | 48.6% | -4.12% |

---

## 12-Month Per-Strategy Stats

| Strategy | Trades | Win Rate | Avg P&L % | Total P&L | Best | Worst |
|---|---:|---:|---:|---:|---:|---:|
| `gap_up` | 15 | 73.3% | +3.19% | $412.89 | +12.53% | -5.59% |
| `ma_crossover` | 20 | 35.0% | +2.79% | $475.13 | +19.33% | -3.58% |
| `momentum` | 42 | 40.5% | +1.81% | $698.03 | +20.95% | -9.17% |
| `range_breakout` | 9 | 66.7% | +6.44% | $449.49 | +17.26% | -4.44% |
| `rsi_reversion` | 25 | 52.0% | +0.99% | $213.95 | +13.48% | -12.69% |

## 12-Month Quarterly Breakdown

| Quarter | Start Value | End Value | Return | Trades | Win Rate |
|---|---:|---:|---:|---:|---:|
| Q2 2025 | $10,000.00 | $10,521.04 | +5.21% | 15 | 66.7% |
| Q3 2025 | $10,487.83 | $11,381.96 | +8.53% | 42 | 45.2% |
| Q4 2025 | $11,446.05 | $11,483.97 | +0.33% | 24 | 45.8% |
| Q1 2026 | $11,483.97 | $11,918.99 | +3.79% | 19 | 47.4% |
| Q2 2026 | $11,918.99 | $12,286.93 | +3.09% | 11 | 45.5% |

---

## Strategy and Screener Comparison

| Scenario | Screener | Strategies | Return | Trades | Win Rate | Max Drawdown |
|---|---|---|---:|---:|---:|---:|
| Current moderate-risk all-enabled strategy set | On | `momentum`, `rsi_reversion`, `gap_up`, `ma_crossover`, `range_breakout` | +22.87% | 111 | 48.6% | -4.12% |
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

- The moderate-risk all-enabled profile improved the 12-month return to +22.87% versus +12.12% for the previous default and +9.43% for the prior all-enabled profile. Drawdown increased to -4.12%, so this is a profit-seeking paper profile rather than a conservative allocation.
- Range Breakout and MA Crossover remained strong crypto contributors. Range Breakout still has only 9 closed trades, so its edge needs continued forward validation before scaling allocation.
- Gap-Up was profitable over 12 months and stabilized after the gap and volume thresholds were narrowed from the prior all-enabled profile. Keep its dedicated gate in the monitoring loop before scaling allocation.
- RSI Reversion recovered from the prior weak 12-month run and contributed $213.95, but the worst trade was -12.69%. Keep monitoring the dedicated RSI gate before scaling allocation.
- Use the current row above for live/paper expectations and treat older rows as historical baselines only.
- The configured position cap remains 8%. With `max_positions: 10`, this caps fully deployed gross long exposure at roughly 80% before cash, position, and asset-class constraints.

---

## Reproduction Commands

Recommended default:

```bash
python3 scheduler/run_backtest.py --days 365 --fund 10000 --end-date 04/29/2026 --exit-policy profit_trailing --screener
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
  --set strategies.ma_crossover.max_loss_exit_pct=0.01
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
python3 scheduler/run_backtest.py --days 365 --fund 10000 --end-date 04/29/2026 --exit-policy profit_trailing --screener
python3 scheduler/run_validation_gate.py --profile production
python3 scheduler/run_validation_gate.py --profile gap
```

All checks passed at the time this document was updated.

The production validation gate is cost-aware. Its default model assumes 10 bps
adverse slippage and 5 bps fees per side. Required gates cover the default
12-month and 6-month windows plus a 12-month crypto-sleeve window. The approved
moderate-risk profile allows up to 6% costed drawdown on the 12-month default
gate and 4% on the 6-month default gate. The latest 30-day crypto-sleeve window
is tracked as a watch-only gate because the current 365-day MA Crossover crypto
sleeve remains profitable but recent crypto trades were weak.

Latest production-gate result:

| Gate | Result | Return | Max Drawdown | Trades | Win Rate | Profit Factor | Daily Sharpe |
|---|---|---:|---:|---:|---:|---:|---:|
| `default_12m_costed` | PASS | +12.23% | -5.65% | 109 | 42.2% | 1.56 | 1.60 |
| `default_6m_costed` | PASS | +5.27% | -2.00% | 44 | 40.9% | 1.61 | 1.53 |
| `crypto_12m_costed` | PASS | +8.19% | -1.44% | 29 | 41.4% | 3.38 | 2.35 |
| `crypto_recent_30d_watch` | WARN | -0.83% | -0.83% | 4 | 0.0% | 0.00 | -4.84 |

RSI Reversion is enabled in the active profile by configuration, but the dedicated
`--profile rsi` gate should still be used before scaling its allocation. The
latest default 12-month run had 25 RSI trades, 52.0% win rate, and $213.95
total P&L, while still showing one large -12.69% loser.

Range Breakout is enabled in the active profile. Its dedicated gate validates
the hardened Donchian-style implementation before scaling allocation:

| Gate | Result | Return | Max Drawdown | Trades | Win Rate | Profit Factor | Daily Sharpe |
|---|---|---:|---:|---:|---:|---:|---:|
| `range_breakout_12m_costed` | PASS | +6.52% | -1.18% | 14 | 64.3% | 4.44 | 1.93 |
| `range_breakout_recent_30d_watch` | WARN | -0.36% | -0.36% | 1 | 0.0% | 0.00 | -5.17 |

Gap-Up is enabled in the active profile. Its dedicated gate validates the
opening-minute implementation with the dynamic screener enabled before scaling
allocation:

| Gate | Result | Return | Max Drawdown | Trades | Win Rate | Profit Factor | Daily Sharpe |
|---|---|---:|---:|---:|---:|---:|---:|
| `gap_up_12m_costed` | PASS | +1.12% | -0.99% | 12 | 66.7% | 2.00 | 0.42 |
| `gap_up_recent_30d_watch` | WARN | -0.05% | -0.35% | 2 | 50.0% | 0.86 | -0.43 |

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
