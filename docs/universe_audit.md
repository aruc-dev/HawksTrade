# Universe Bias Audit

Phase 1 replaces the forward-biased `EXTENDED_POOL` with a point-in-time
membership ledger at `data/universe/sp500_constituents.csv`.

## Prior Bias Surface

The deprecated extended pool mixed long-lived index members with names that only
became prominent after IPOs, crypto rallies, or later S&P 500 additions. That
made historical windows look better than a trader could have achieved in real
time because those symbols were selected with hindsight.

Representative examples:

| Symbol | PIT Treatment | Bias Controlled |
|---|---|---|
| `SMCI` | Enters from its documented index-addition date | Prevents earlier windows from knowing a later AI-cycle winner |
| `ARM` | Non-index name, available only after IPO grace/liquidity date | Prevents pre-IPO lookahead |
| `COIN` | Non-index name, available only after IPO grace/liquidity date | Prevents crypto-cycle hindsight selection |
| `CRWD` | Enters from its documented index-addition date | Prevents current index membership from leaking backward |
| `IBIT` | ETF, available only after 90-day post-launch grace | Prevents pre-launch ETF exposure |

## Implementation

`PITUniverseBuilder.members_as_of(date)` returns only symbols active on that
date. For index rows, `added_date <= date < removed_date`. For non-index rows,
the symbol becomes eligible on `first_liquid_date`, or 90 days after `ipo_date`
when a first-liquid date is not provided. `delisted_date` removes a symbol from
all later windows.

`scheduler/run_backtest.py` now uses the PIT universe by default when the
screener is enabled. The previous `EXTENDED_POOL` remains available only through
`--legacy-pool` for explicit A/B bias audits and emits a deprecation warning.

## Data Caveat

The committed CSV is an auditable seed ledger, not a paid CRSP-grade corporate
actions database. Rows should be tightened over time as more authoritative
historical membership data is acquired. The important behavior now lives in
code: new backtests no longer assume every currently popular symbol was
available for every historical window.
