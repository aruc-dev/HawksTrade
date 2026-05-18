# OOS Lockup Policy

HawksTrade keeps the most recent 90-day research window locked so strategy
tuning does not leak into final validation. The active window is stored in
`data/oos_lockup.json`.

## Default Behavior

`scheduler/run_backtest.py` excludes the active lockup by default. If a requested
research backtest overlaps the lockup, the simulation end date is clipped to the
day before the lockup starts and the report records an OOS lockup note.

The data filter applies after broker historical data is fetched, so accidental
strategy scans inside the locked range do not see those bars in normal research
runs.

## Validation Workflow

Run the locked window only for final validation:

```bash
python3 scheduler/run_backtest.py --oos-validation --output reports/oos_validation_<date>.md
```

The command validates the current lockup exactly once. When the run completes,
`data/oos_lockup.json` records the validation outcome and, when enough newer
data is available, rolls the lockup forward to the latest available 90 days.

Do not tune parameters, strategy filters, universes, or allocation after seeing
an OOS result and then rerun the same lockup. A failed OOS result means the
candidate evidence failed final validation.

## Leakage Guard

Release validation runs `scripts/check_oos_lockup_leakage.py --tracked` and
blocks committed reports that mention a date inside the active lockup. Hook
usage should call `scripts/check_oos_lockup_leakage.py --staged` so only staged
report changes are checked before commit. Reports from the explicit OOS
validation workflow are the only exception; they must be Markdown files named
`reports/oos_validation_*.md` and include the `OOS validation` marker.

Before scaling capital or enabling a strategy, the current locked OOS window
must have a passing validation result within the last 30 days, alongside the
master walk-forward and SPA requirements.
