# Walk-Forward Interpretation Guide

## Purpose

The master walk-forward report validates the current HawksTrade profile across
multiple market regimes instead of relying on one recent 12-month backtest. Use
it before scaling capital, enabling larger strategy allocations, or treating a
strategy change as production-ready.

## Commands

```bash
python3 scheduler/run_walkforward.py --profile master
python3 scheduler/run_walkforward.py --profile master --oos-only
python3 scheduler/run_walkforward.py --quick --no-write-report --no-artifacts
```

The master command writes `reports/walkforward_master.md`. The quick command is
for advisory pre-commit checks and should not be treated as the full evidence
base.

## How To Read The Report

- Summary pass rate is the first decision point. The configured
  `blocking_levels` control the command exit code; for the master profile,
  stressed cost is the binding capital-scaling level. Baseline and severe remain
  diagnostic unless they are explicitly added to `blocking_levels`.
- Per-window detail shows whether failures are regime-specific or broad.
- Per-strategy attribution shows which sleeve contributed to the window result.
- Data caveats matter. Missing historical symbols shrink the tested universe and
  should be treated as a bias note, especially in older windows.
- Synthetic intraday notes mean daily bars were used as proxies for behaviors
  that would ideally be replayed with minute bars.

Some windows can fail without invalidating the system. The gate is designed to
find whether failures are isolated, explainable regime sensitivity or broad
overfitting. A failure is acceptable only when it is documented, bounded, and the
aggregate pass-rate threshold still holds.

## Stop-The-Line Policy

If `reports/walkforward_master.md` falls below the configured stressed pass-rate
threshold:

1. Pause capital scaling, strategy enablement, and new-entry expansion.
2. File a beads issue with the failing profile, pass rate, failing windows, and
   report path.
3. Revert the triggering change or pause new entries until the human owner
   reviews the regression.

The runner can auto-file the regression issue when the master profile is
regenerated and the configured binding level fails.

## Locked OOS Window

The most recent held-out window is separate from the master report. Run it only
when final validation is needed:

```bash
python3 scheduler/run_walkforward.py --profile master --oos-only
```

Do not tune strategy parameters against the locked OOS result.

The OOS window is shorter than the 180-day master windows, so its minimum trade
count is duration-normalized when `scale_min_trades_to_window` is enabled. The
master profile therefore applies the stressed 25-trade/180-day cadence as an
8-trade floor for the 60-day locked OOS window.

## Quarterly Refresh

Regenerate `walkforward_master` on the first business day of each calendar
quarter:

1. Run `python3 scheduler/run_walkforward.py --profile master`.
2. Review failing windows, per-strategy attribution, and missing-history caveats.
3. Commit the refreshed `reports/walkforward_master.md`.
4. Add a beads issue for any regression or unexplained regime failure.
