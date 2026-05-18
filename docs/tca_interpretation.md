# Transaction Cost Analysis

HawksTrade TCA measures whether live fills are matching the execution assumptions
used by backtests.

## Metrics

- `implementation_shortfall_bps`: adverse fill cost from decision price to fill
  price. Positive is worse execution for both buys and sells.
- `timing_bps`: price movement from decision price to broker submission.
- `slippage_bps`: price movement from broker submission to fill.
- `expected_slippage_bps`: the configured liquidity-aware model estimate.
- `residual_bps`: realised slippage minus expected slippage. Persistent positive
  residual means the model is too optimistic or the execution policy is paying
  too much.

## Weekly Review

Review `reports/tca_weekly_<date>.md` before increasing allocation or changing
risk limits. The important checks are:

1. Median residual should stay close to zero by asset class.
2. P95 implementation shortfall should not be driven by one strategy or one
   time-of-day bucket.
3. Worst fills should have an explainable reason, such as open-window volatility.
4. Latency P95 should stay under the configured budget.

If median realised slippage exceeds expected slippage by more than roughly
10 bps for stocks or 25 bps for crypto over a week, pause allocation increases,
run `scripts/calibrate_slippage_model.py`, and review whether the execution
policy or universe liquidity filters need adjustment.
