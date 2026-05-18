# Minute-Bar Replay Audit - Phase 2

Generated during Phase 2 execution/microstructure validation.

## Scope

This audit compares the prior synthetic intraday proxy against real 1-minute
replay for the default 12-month production window. The production window asked
for an end date of 2026-04-29, but the OOS lockup clipped the effective end to
2026-02-14 to avoid the locked 2026-02-15..2026-05-15 range.

Strategies:

- `momentum`
- `relative_strength`
- `rsi_reversion`
- `ma_crossover`
- `range_breakout`

Cost models:

- Synthetic proxy bias audit: 10 bps adverse slippage per side and 5 bps
  fee per side
- Production gate: liquidity-aware slippage model at 1.00x and 5 bps fee
  per side
- $10,000 starting fund

## Results

| Backtest data path | Cost model | Return | Trades | Win rate | Max DD | Profit factor | Daily Sharpe |
|---|---|---:|---:|---:|---:|---:|---:|
| Synthetic elapsed-session volume proxy | Flat 10 bps | +9.61% | 146 | 51.4% | -1.04% | 2.47 | 3.54 |
| Real 1-minute replay | Slippage model x1.00 | -0.51% | 43 | 37.2% | -1.21% | 0.79 | -0.58 |

## Interpretation

Real 1-minute replay materially reduced stock momentum and relative-strength
activity because volume-pace confirmation now uses actual elapsed-session
volume instead of a daily-volume interpolation. This is expected for Phase 2:
the synthetic path overstated tradability and signal frequency.

The production gate currently fails under the real-minute path:

- `default_12m_costed`: bootstrap lower bound return -1.94% vs +10.00%
  required; lower-bound profit factor 0.42 vs 1.50 required; lower-bound
  daily Sharpe -2.36 vs 1.00 required; 43 trades vs 60 required.
- `default_6m_costed`: bootstrap lower bound return -0.60% vs +1.50%
  required; lower-bound profit factor 0.60 vs 1.20 required; lower-bound
  daily Sharpe -1.66 vs 0.50 required; 21 trades vs 25 required.
- `crypto_12m_costed`: bootstrap lower bound return -1.53% vs +5.00%
  required; lower-bound profit factor 0.28 vs 2.00 required.

## Decision

Keep real minute replay as the default. Do not restore the synthetic proxy for
production validation. The synthetic path remains available only through
`--use-synthetic-intraday` for explicit bias audits.

The remaining production-gate shortfall is strategy research and risk-policy
work, not a Phase 2 execution-instrumentation defect.
