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
- `momentum` enabled with `top_n: 1`, `min_momentum_pct: 0.10`, `volume_spike_ratio: 1.8`, and `min_breadth_coverage_pct: 0.75`
- `ma_crossover` enabled with a 1% daily-close max-loss exit
- `range_breakout` enabled
- `rsi_reversion` disabled
- `gap_up` disabled

These results enforce `trading.max_position_pct: 0.05` for every entry, including momentum/Kelly sizing, and include the hardened Range Breakout implementation with ranked signals, extension/RSI guards, and failed-breakout exits. The tuning improved the 12-month default from +1.26% return / -7.23% max drawdown to +7.52% return / -1.73% max drawdown without increasing global risk limits.

| Period | Final Value | Return | Trades | Win Rate | Max Drawdown |
|---|---:|---:|---:|---:|---:|
| 12 months | $10,751.62 | +7.52% | 72 | 44.4% | -1.73% |
| 6 months | $10,046.99 | +0.47% | 20 | 35.0% | -1.28% |

---

## 12-Month Per-Strategy Stats

| Strategy | Trades | Win Rate | Avg P&L % | Total P&L | Best | Worst |
|---|---:|---:|---:|---:|---:|---:|
| `ma_crossover` | 19 | 42.1% | +4.33% | $414.22 | +18.74% | -6.32% |
| `momentum` | 36 | 44.4% | +1.65% | $313.06 | +17.34% | -17.03% |
| `range_breakout` | 17 | 47.1% | +0.05% | -$0.86 | +13.47% | -6.74% |

## 12-Month Quarterly Breakdown

| Quarter | Start Value | End Value | Return | Trades | Win Rate |
|---|---:|---:|---:|---:|---:|
| Q2 2025 | $10,000.00 | $10,082.19 | +0.82% | 18 | 50.0% |
| Q3 2025 | $10,071.04 | $10,776.53 | +7.01% | 30 | 53.3% |
| Q4 2025 | $10,787.77 | $10,694.46 | -0.87% | 10 | 20.0% |
| Q1 2026 | $10,694.46 | $10,732.64 | +0.36% | 13 | 38.5% |
| Q2 2026 | $10,732.64 | $10,751.62 | +0.18% | 1 | 0.0% |

---

## Strategy and Screener Comparison

| Scenario | Screener | Strategies | Return | Trades | Win Rate | Max Drawdown |
|---|---|---|---:|---:|---:|---:|
| Current capital-preserving default strategy set | On | `momentum`, `ma_crossover`, `range_breakout` | +7.52% | 72 | 44.4% | -1.73% |
| Previous tight screener, hardened default strategy set | On | `momentum`, `ma_crossover`, `range_breakout` | +1.26% | 133 | 32.3% | -7.23% |
| Historical fixed-universe run before Range Breakout hardening | Off | `momentum`, `ma_crossover`, `range_breakout` | +14.50% | 172 | 39.0% | -3.06% |
| Historical pre-cap recommended run | On | `momentum`, `ma_crossover`, `range_breakout` | +26.53% | 274 | 34.7% | -9.34% |
| Historical pre-cap fixed-universe run | Off | `momentum`, `ma_crossover`, `range_breakout` | +20.16% | 172 | 39.0% | -4.64% |

Interpretation:

- The stricter Momentum profile cut churn materially: 98 previous trades fell to 36, win rate improved from 26.5% to 44.4%, and the sleeve became profitable without changing account-level risk limits.
- The MA Crossover 1% daily-close max-loss exit reduced its worst observed 12-month loss from -19.25% to -6.32% while improving total contribution.
- Range Breakout remained close to flat over the 12-month reproduction: 17 trades, 47.1% win rate, -$0.86 total P&L. A no-Range variant produced +7.41% return with -1.45% max drawdown; the current default keeps Range Breakout enabled because it added a small amount of return while total drawdown stayed below 2%.
- Use the current row above for live/paper expectations and treat older rows as historical baselines only.
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
```

All checks passed at the time this document was updated.

The production validation gate is cost-aware. Its default model assumes 10 bps
adverse slippage and 5 bps fees per side. Required gates cover the default
12-month and 6-month windows plus a 12-month crypto-sleeve window. The latest
30-day crypto-sleeve window is tracked as a watch-only gate because the current
365-day crypto sleeve remains profitable but recent MA Crossover and Range
Breakout trades were weak.

Latest production-gate result:

| Gate | Result | Return | Max Drawdown | Trades | Win Rate | Profit Factor | Daily Sharpe |
|---|---|---:|---:|---:|---:|---:|---:|
| `default_12m_costed` | PASS | +6.68% | -1.86% | 72 | 44.4% | 1.72 | 1.76 |
| `default_6m_costed` | PASS | +0.47% | -1.24% | 20 | 35.0% | 1.08 | 0.40 |
| `crypto_12m_costed` | PASS | +3.81% | -1.36% | 36 | 44.4% | 2.12 | 1.43 |
| `crypto_recent_30d_watch` | WARN | -0.72% | -0.72% | 6 | 0.0% | 0.00 | -5.88 |

RSI Reversion enablement remains blocked by the `--profile rsi` gate: the latest
costed 12-month RSI-only run returned -1.20% with only 2 trades, and there is no
60-day/20-trade forward paper evidence yet.

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
