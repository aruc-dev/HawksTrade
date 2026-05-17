# Bootstrap Reference Report

Bootstrap confidence intervals are now generated directly by
`scheduler/run_backtest.py` when closed trades are present.

## Reproduction

```bash
python3 scheduler/run_backtest.py --days 365 --fund 10000 --screener --slippage-bps 10 --fee-bps 5 --min-fee 0 --output reports/bootstrap_default_12m.md
```

The report includes:

- trade-resample CIs for return, drawdown, profit factor, Sharpe, and win rate
- daily block-bootstrap CIs for the same metrics
- probability of a losing period
- probability of drawdown breaching the configured threshold

## Gate Policy

Production validation and walk-forward gates use bootstrap bounds when present.
The planning number is the lower-bound return, not the point estimate.
