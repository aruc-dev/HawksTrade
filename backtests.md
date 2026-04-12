# HawksTrade Backtest Summary

> **Updated:** April 12, 2026
> **Starting Capital:** $10,000
> **Backtest End Date:** April 10, 2026
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

| Period | Final Value | Return | Trades | Win Rate | Max Drawdown |
|---|---:|---:|---:|---:|---:|
| 12 months | $12,652.86 | +26.53% | 274 | 34.7% | -9.34% |
| 6 months | $10,911.44 | +9.11% | 104 | 32.7% | -5.80% |

---

## 12-Month Per-Strategy Stats

| Strategy | Trades | Win Rate | Avg P&L % | Total P&L | Best | Worst |
|---|---:|---:|---:|---:|---:|---:|
| `momentum` | 226 | 31.0% | +0.94% | $1,846.24 | +19.10% | -12.85% |
| `ma_crossover` | 23 | 52.2% | +3.80% | $463.43 | +18.74% | -7.40% |
| `range_breakout` | 25 | 52.0% | +1.79% | $216.05 | +26.50% | -7.56% |

## 12-Month Quarterly Breakdown

| Quarter | Start Value | End Value | Return | Trades | Win Rate |
|---|---:|---:|---:|---:|---:|
| Q2 2025 | $10,000.00 | $10,923.15 | +9.23% | 55 | 40.0% |
| Q3 2025 | $10,955.62 | $11,701.20 | +6.81% | 97 | 34.0% |
| Q4 2025 | $11,775.83 | $11,162.32 | -5.21% | 71 | 21.1% |
| Q1 2026 | $11,114.30 | $12,530.92 | +12.75% | 50 | 50.0% |
| Q2 2026 | $12,530.92 | $12,652.86 | +0.97% | 1 | 0.0% |

---

## Strategy and Screener Comparison

| Scenario | Screener | Strategies | Return | Trades | Win Rate | Max Drawdown |
|---|---|---|---:|---:|---:|---:|
| Old screener baseline | On | all | +8.35% | 337 | not recorded | not recorded |
| Tight screener, all strategies | On | all | +19.78% | 316 | 30.4% | -11.50% |
| Tight screener, default strategy set | On | `momentum`, `ma_crossover`, `range_breakout` | +26.53% | 274 | 34.7% | -9.34% |
| Fixed universe, all strategies | Off | all | +22.55% | 229 | 35.4% | -6.96% |
| Fixed universe, default strategy set | Off | `momentum`, `ma_crossover`, `range_breakout` | +20.16% | 172 | 39.0% | -4.64% |

Interpretation:

- The tightened screener fixed the main underperformance seen in the old broad screener run.
- The highest 12-month return came from the tightened screener plus the default strategy set.
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
