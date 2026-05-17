# HawksTrade Phased Implementation TODO

This file is an implementation index for the three enhancement phases. It is
not a replacement for beads: before starting any item, create or claim a `bd`
issue for that item and keep implementation status in beads.

Source phase documents:

- Phase 1: `docs/phase1_statistical_hardening_plan.md`
- Phase 2: `docs/phase2_execution_microstructure_plan.md`
- Phase 3: `docs/phase3_risk_architecture_plan.md`

Use the numbering format `<phase>.<serial>`. Implement items in the order below
unless a beads dependency says otherwise. Most items affect profit, trades,
risk, sizing, strategy validation, or execution realism, so they usually require
the full strategy validation ladder defined in `AGENTS.md`, `CODEX.md`, and
`CLAUDE.md`. Pure documentation or scaffolding-only subtasks can use focused
unit/document validation.

## Phase 1 - Statistical Hardening

- [ ] **1.1 Implement OOS lockup enforcement.** Source:
  `docs/phase1_statistical_hardening_plan.md`, `Item P1-6 - Out-of-Sample
  Lockup`. Create `data/oos_lockup.json`, add `core/data_lockup.py`, make
  backtests exclude the locked window by default, add `run_backtest.py
  --oos-validation`, add a pre-commit leakage check, document the unlock policy
  in `docs/oos_lockup_policy.md`, and update agent/operator docs.

- [ ] **1.2 Replace forward-biased stock universe with a point-in-time
  universe.** Source: `docs/phase1_statistical_hardening_plan.md`, `Item P1-2 -
  Fix Universe Selection Bias`. Produce `docs/universe_audit.md`, add historical
  universe data under `data/universe/`, implement `screener/pit_universe.py`,
  wire it into `scheduler/run_backtest.py` as the default source, retain the
  legacy pool behind an explicit comparison flag, publish
  `reports/universe_bias_audit.md`, and document the headline delta as prior
  survivorship bias.

- [ ] **1.3 Enforce sample-size risk discipline in code.** Source:
  `docs/phase1_statistical_hardening_plan.md`, `Item P1-3 - Code-Enforced
  Sample-Size Discipline`. Add configurable risk tiers, implement
  `core/sample_size_governor.py`, apply risk and position-cap multipliers in
  live order sizing and backtests, add risk-tier reporting, support audited
  expiring overrides, and cover tier boundaries, graduation, and override
  behavior in tests.

- [ ] **1.4 Add bootstrap and Monte Carlo confidence intervals to backtests and
  gates.** Source: `docs/phase1_statistical_hardening_plan.md`, `Item P1-4 -
  Bootstrap / Monte Carlo Confidence Intervals`. Implement `analysis/bootstrap.py`
  with trade resampling and block bootstrap, add CI sections to backtest reports,
  make validation gates use percentile bounds instead of point estimates, publish
  `reports/bootstrap_default_12m.md`, and document interpretation in
  `docs/interpreting_bootstrap_cis.md`.

- [ ] **1.5 Add multiple-testing correction with Hansen SPA / White's Reality
  Check.** Source: `docs/phase1_statistical_hardening_plan.md`, `Item P1-5 -
  Multiple-Testing Correction`. Define the strategy search-space catalog,
  implement `analysis/spa_test.py` using a vetted SPA implementation, add
  backtest grid generation and caching, publish
  `reports/multiple_testing_correction.md`, and update the pre-scaling workflow
  so allocation increases require current SPA evidence.

- [ ] **1.6 Complete Phase 1 closeout.** Source:
  `docs/phase1_statistical_hardening_plan.md`, `Acceptance Criteria for the
  Whole Phase` and `What "Done" Looks Like`. Verify PIT universe default,
  enforced sample-size caps, bootstrap CIs in reports, SPA p-values published,
  locked OOS enforcement, CHANGELOG updates, and agent/operator docs referencing
  mandatory pre-scaling checks.

## Phase 2 - Execution and Microstructure

- [ ] **2.1 Replace synthetic intraday proxy with real minute-bar replay.**
  Source: `docs/phase2_execution_microstructure_plan.md`, `Item P2-7 - Real
  Minute-Bar Replay for Gap-Up and Momentum Volume-Pace`. Add
  `core/minute_cache.py`, add `scripts/prefetch_minute_bars.py`, wire real
  1-minute bars into Gap-Up and Momentum backtests, add realistic fill modeling,
  retain synthetic replay only behind an explicit comparison flag, publish
  `reports/minute_bar_replay_audit.md`, and document cache storage and bandwidth.

- [ ] **2.2 Replace flat slippage with a liquidity-aware slippage model.**
  Source: `docs/phase2_execution_microstructure_plan.md`, `Item P2-8 -
  Liquidity-Aware Slippage Model`. Implement `core/slippage_model.py`, use it in
  backtest execution pricing and live marketable-limit offsets, add trade-log
  fields for decision/arrival/expected/realized slippage, add
  `scripts/calibrate_slippage_model.py`, convert cost sensitivity levels into
  model multipliers, publish docs, and re-run walk-forward with model defaults.

- [ ] **2.3 Add post-trade transaction cost analysis.** Source:
  `docs/phase2_execution_microstructure_plan.md`, `Item P2-10 - Post-Trade
  Transaction Cost Analysis`. Implement `tracking/tca.py`, append TCA to the
  daily report, add `scheduler/run_weekly_tca.py`, schedule weekly TCA reporting,
  surface slippage anomalies through run markers, add weekly backtest-vs-live
  drift monitoring, and document weekly TCA review before allocation changes.

- [ ] **2.4 Implement and A/B test smart multi-leg entry execution.** Source:
  `docs/phase2_execution_microstructure_plan.md`, `Item P2-9 - Smart Order
  Routing (Multi-Leg Entries)`. Add `core/execution_policy.py`, extend
  `core/order_executor.py` for passive-then-aggressive entry legs, make order
  governor accounting parent-intent aware, add configurable A/B bucketing, mirror
  the policy in backtests using minute-bar OHLC fill rules, and publish
  `reports/execution_policy_ab_test.md` after enough fills.

- [ ] **2.5 Add latency budget measurement and reporting.** Source:
  `docs/phase2_execution_microstructure_plan.md`, `Item P2-11 - Latency Budget
  and Measurement`. Instrument order-pipeline stages with monotonic timestamps,
  add `latency_ms` JSON to trade-log entries, configure stock and crypto latency
  budgets, include latency percentiles in weekly TCA, alert on severe breaches,
  and optimize obvious hot-path offenders without introducing stale reads.

- [ ] **2.6 Complete Phase 2 closeout.** Source:
  `docs/phase2_execution_microstructure_plan.md`, `Acceptance Criteria for the
  Whole Phase` and `What "Done" Looks Like`. Verify real minute-bar replay,
  model-based slippage, execution policy evidence, TCA publication, latency
  measurement, CHANGELOG updates, and operator docs for weekly TCA review.

## Phase 3 - Risk Architecture Upgrade

- [ ] **3.1 Add the independent watchdog process.** Source:
  `docs/phase3_risk_architecture_plan.md`, `Item P3-17 - Independent Watchdog
  Process`. Implement `watchdog/main.py`, add all structural integrity checks,
  write `data/watchdog_state.json`, integrate alerting with deduplication, add
  optional off-by-default cancel-all handling, add systemd service/timer files,
  and publish `docs/watchdog.md` with runbooks for every failure mode.

- [ ] **3.2 Implement multi-horizon drawdown circuit breakers and strategy kill
  switches.** Source: `docs/phase3_risk_architecture_plan.md`, `Item P3-15 -
  Multi-Horizon Drawdown Circuit Breakers`. Add `core/drawdown_monitor.py`,
  persist drawdown state, configure warn/dampen/halt/liquidate thresholds, add
  `core/strategy_kill_switch.py`, wire pre-trade checks and risk-check
  liquidation, integrate with reports and backtests, document the liquidation
  playbook, and paper-test the liquidation path.

- [ ] **3.3 Add portfolio factor decomposition and factor limits.** Source:
  `docs/phase3_risk_architecture_plan.md`, `Item P3-12 - Portfolio Factor
  Decomposition`. Add factor data ingestion and refresh, implement
  `analysis/factor_attribution.py`, compute portfolio factor exposures, enforce
  pre-trade factor limits, add factor sections to daily reports, and document
  factor interpretation and override procedure in `docs/factor_risk.md`.

- [ ] **3.4 Add portfolio volatility targeting.** Source:
  `docs/phase3_risk_architecture_plan.md`, `Item P3-13 - Portfolio Volatility
  Targeting`. Compute realized portfolio volatility from performance history,
  add configurable target-vol multipliers, stack them correctly with sample-size
  scaling, apply them in live sizing and backtests, report current multiplier,
  and publish a committed comparison showing smoother equity curves.

- [ ] **3.5 Extend correlation-aware sizing to equities.** Source:
  `docs/phase3_risk_architecture_plan.md`, `Item P3-14 - Correlation-Aware
  Equity Sizing`. Generalize `core/correlation_guard.py` beyond crypto, identify
  equity correlation clusters, enforce cluster-level dollar-risk budgets, support
  reject/downsize actions, show cluster budget consumption in reports, and add
  backtest plus regression coverage for highly correlated equity entries.

- [ ] **3.6 Build the stress test catalog and runner.** Source:
  `docs/phase3_risk_architecture_plan.md`, `Item P3-16 - Stress Test Catalog`.
  Define the scenario YAML schema, add at least six historical scenarios under
  `stress_tests/scenarios/`, implement `scheduler/run_stress_tests.py`, add
  configurable pass criteria, trigger quick stress tests for config changes,
  schedule full nightly catalog runs, and document scenario maintenance in
  `docs/stress_test_catalog.md`.

- [ ] **3.7 Complete Phase 3 closeout.** Source:
  `docs/phase3_risk_architecture_plan.md`, `Acceptance Criteria for the Whole
  Phase` and `What "Done" Looks Like`. Verify factor reporting/enforcement, vol
  targeting, equity cluster risk, multi-horizon drawdown controls, stress test
  catalog, independent watchdog, CHANGELOG updates, and operator docs for all
  mandatory checks.
