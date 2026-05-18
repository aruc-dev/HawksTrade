# Universe Bias Audit Report

Phase 1 changes the default stock universe source from the deprecated
`EXTENDED_POOL` to the point-in-time ledger in
`data/universe/sp500_constituents.csv`.

## Result

The expected direction of the delta is lower headline return and slightly worse
drawdown versus the legacy extended pool. That degradation is intentional: it is
the survivorship and selection bias that the old hand-picked pool could harvest
in historical tests but a real-time trader could not.

## Controls Added

| Control | Status |
|---|---|
| PIT ledger for index and non-index symbols | Implemented |
| Non-index 90-day IPO/liquidity grace | Implemented |
| Delisted/removed symbol exclusion | Implemented |
| Legacy pool behind explicit flag only | Implemented |
| Backtest report records universe source | Implemented |

## Reproduction

Run the corrected default:

```bash
python3 scheduler/run_backtest.py --days 365 --fund 10000 --screener --slippage-bps 10 --fee-bps 5 --min-fee 0
```

Run the deprecated comparison only for audit purposes:

```bash
python3 -W default scheduler/run_backtest.py --days 365 --fund 10000 --screener --legacy-pool --slippage-bps 10 --fee-bps 5 --min-fee 0
```

Treat the legacy-minus-PIT return difference as prior survivorship bias, not as
lost edge.
