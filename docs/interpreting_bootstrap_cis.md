# Interpreting Bootstrap Confidence Intervals

Backtest point estimates are one path through one historical sample. Phase 1
adds bootstrap confidence intervals so reports show a distribution instead of a
single fragile number.

## Two Views

| View | What It Answers |
|---|---|
| Trade resample | What happens if the observed closed trades arrive in a different sampled sequence? |
| Daily block bootstrap | What happens if daily return blocks are resampled while preserving short-term clustering? |

Use the lower 5th percentile for planning return, profit factor, and Sharpe.
Use the adverse drawdown percentile as the risk case. If the lower-bound return
is weak or negative, do not scale capital based on the median.

## Operational Rule

Validation gates read bootstrap bounds when they are present unless a gate sets
`use_bootstrap_bounds: false`:

- return must pass using the lower confidence bound
- profit factor must pass using the lower confidence bound
- Sharpe must pass using the lower confidence bound
- drawdown must pass using the adverse confidence bound

Validation output may show a positive point estimate and still fail. In that
case, read the `gate_bounds` or `Gate` columns; those are the conservative
confidence-bound values used for the decision. When a production-gate exception
sets `use_bootstrap_bounds: false`, output reports `bootstrap_bounds_advisory`
instead. Those bounds are diagnostic, not blocking.

Wide intervals mean the trade sample is too thin. The right response is more
out-of-sample evidence, not parameter tuning to tighten the interval.
