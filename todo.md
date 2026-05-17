# HawksTrade Phase 1 Implementation TODO

This file is the implementation index for Phase 1 statistical hardening. It is
not a replacement for beads: before starting any item, create or claim a `bd`
issue for that item and keep implementation status in beads.

Source phase document:

- Phase 1: `docs/phase1_statistical_hardening_plan.md`

Use the numbering format `<phase>.<serial>`. Implement items in the order below
unless a beads dependency says otherwise. These items affect profit, trades,
risk, sizing, strategy validation, and validation thresholds, so they usually
require the full strategy validation ladder defined in `AGENTS.md`, `CODEX.md`,
and `CLAUDE.md`.

## Phase 1 - Statistical Hardening

- [ ] **1.1 Implement OOS lockup enforcement.** Source:
  `docs/phase1_statistical_hardening_plan.md`, `Item P1-6 - Out-of-Sample Lockup`.
  Create `data/oos_lockup.json`, add `core/data_lockup.py`, make backtests
  exclude the locked window by default, add `run_backtest.py --oos-validation`,
  add a pre-commit leakage check, document the unlock policy in
  `docs/oos_lockup_policy.md`, and update agent/operator docs.

- [ ] **1.2 Replace forward-biased stock universe with a point-in-time
  universe.** Source: `docs/phase1_statistical_hardening_plan.md`,
  `Item P1-2 - Fix Universe Selection Bias`. Produce `docs/universe_audit.md`,
  add historical universe data under `data/universe/`, implement
  `screener/pit_universe.py`, wire it into `scheduler/run_backtest.py` as the
  default source, retain the legacy pool behind an explicit comparison flag, publish
  `reports/universe_bias_audit.md`, and document the headline delta as prior
  survivorship bias.

- [ ] **1.3 Enforce sample-size risk discipline in code.** Source:
  `docs/phase1_statistical_hardening_plan.md`,
  `Item P1-3 - Code-Enforced Sample-Size Discipline`. Add configurable risk
  tiers, implement `core/sample_size_governor.py`, apply risk and position-cap
  multipliers in live order sizing and backtests, add risk-tier reporting,
  support audited expiring overrides, and cover tier boundaries, graduation, and
  override behavior in tests.

- [ ] **1.4 Add bootstrap and Monte Carlo confidence intervals to backtests and
  gates.** Source: `docs/phase1_statistical_hardening_plan.md`,
  `Item P1-4 - Bootstrap / Monte Carlo Confidence Intervals`. Implement
  `analysis/bootstrap.py` with trade resampling and block bootstrap, add CI
  sections to backtest reports, make validation gates use percentile bounds
  instead of point estimates, publish `reports/bootstrap_default_12m.md`, and
  document interpretation in `docs/interpreting_bootstrap_cis.md`.

- [ ] **1.5 Add multiple-testing correction with Hansen SPA / White's Reality
  Check.** Source: `docs/phase1_statistical_hardening_plan.md`,
  `Item P1-5 - Multiple-Testing Correction`. Define the strategy search-space
  catalog, implement `analysis/spa_test.py` using a vetted SPA implementation,
  add backtest grid generation and caching, publish
  `reports/multiple_testing_correction.md`, and update the pre-scaling workflow
  so allocation increases require current SPA evidence.

- [ ] **1.6 Complete Phase 1 closeout.** Source:
  `docs/phase1_statistical_hardening_plan.md`, `Acceptance Criteria for the
  Whole Phase`, `Phase 1 Cross-Cutting Concerns`, and `What "Done" Looks Like`.
  Verify PIT universe default, enforced sample-size caps, bootstrap CIs in
  reports, SPA p-values published, locked OOS enforcement, CHANGELOG updates,
  and agent/operator docs referencing mandatory pre-scaling checks.
