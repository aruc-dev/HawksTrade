# Phase 1 Hardened Gate Follow-Up

Generated: 2026-05-17

## Context

Phase 1 added point-in-time universes, sample-size risk scaling, OOS lockup
enforcement, bootstrap confidence intervals, and SPA-style multiple-testing
controls. After those controls landed, code validation passed but hardened
strategy-quality gates failed.

This is not a runtime failure. It is the expected effect of evaluating the
system with conservative bootstrap bounds and realistic sample-size risk caps.

## Production Gate Result

Command:

```bash
python3 scheduler/run_validation_gate.py --profile production
```

Result:

- `default_12m_costed`: FAIL
  - Point estimate: return `+0.52%`, drawdown `-0.69%`, trades `58`, PF `1.19`, Sharpe `0.38`
  - Gate bounds failed: return `-1.27% < +10.00%`, PF `0.69 < 1.50`, Sharpe `-0.96 < 1.00`, trades `58 < 60`
- `default_6m_costed`: FAIL
  - Point estimate: return `+0.87%`, drawdown `-0.40%`, trades `35`, PF `1.64`, Sharpe `1.26`
  - Gate bounds failed: return `-0.59% < +1.50%`, PF `0.81 < 1.20`, Sharpe `-0.96 < 0.50`
- `crypto_12m_costed`: FAIL
  - Point estimate: return `+1.49%`, drawdown `-0.42%`, trades `29`, PF `2.79`, Sharpe `2.03`
  - Gate bounds failed: return `+0.20% < +5.00%`, PF `1.39 < 2.00`
  - Watch warning: trades `29 < 30` reliability floor
- `crypto_recent_30d_watch`: SKIP
  - Correctly skipped because the recent window is inside the active OOS lockup.

## Quick Walk-Forward Result

Command:

```bash
python3 scheduler/run_walkforward.py --quick --no-write-report --no-artifacts
```

Result:

- Stressed pass rate: `1/3` windows passed, below required `66.0%`.
- `covid_crash_2020`: PASS.
- `tech_bear_2022`: FAIL on bootstrap-bound annualized return, PF, and Sharpe.
- `current_regime_auto`: FAIL on bootstrap-bound annualized return, PF, and Sharpe.

## Interpretation

The post-Phase 1 result is: **do not scale capital or expand entries yet**.

The failures are mainly confidence-bound failures rather than catastrophic point
estimate losses. That means the current evidence is too fragile for higher
allocation under the new hardening rules. The correct response is to collect
more out-of-sample evidence and improve weak strategy sleeves, not to relax the
bootstrap gate.

## Follow-Up Work

- Keep production and quick walk-forward gates blocking for capital scaling.
- Use the updated validation and walk-forward output, which now shows point
  estimates separately from the bootstrap-bound gate metrics.
- Prioritize Phase 2 execution work for Gap-Up intraday realism and separate
  strategy-quality work for weak/choppy regimes before any allocation increase.
