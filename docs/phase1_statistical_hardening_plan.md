# Phase 1 — Statistical Hardening (Remainder)

**Status:** Draft for review
**Owner:** Arun
**Priority:** P0 — must complete before any capital scaling
**Target window:** 4–8 weeks (parallelisable with the walk-forward plan)
**Scope note:** The multi-regime walk-forward (item 1 of the original list) is covered in `docs/multi_regime_walkforward_plan.md` and is excluded here. This plan covers items **2–6** of Phase 1 from `reports/REVIEW_2026-05-16.md`.

The unifying goal of this phase: **replace anecdotal evidence with statistically defensible evidence.** Today's headline +20.7% is one observation. After Phase 1, every number the system reports will carry confidence intervals, the universe will be free of forward-looking selection, sample-size discipline will be code-enforced, and any "best strategy" claim will have been corrected for multiple-testing bias.

---

## Acceptance Criteria for the Whole Phase

Phase 1 is "done" when **all** of the following are true:

- [ ] Universe construction is point-in-time and documented; the previous `EXTENDED_POOL` is deprecated.
- [ ] Per-strategy risk caps are mechanically enforced based on closed-trade counts (no manual config edits).
- [ ] Every published backtest result includes bootstrap 95% confidence intervals on return, drawdown, profit factor, and Sharpe.
- [ ] A White's Reality Check / Hansen SPA-style p-value is computed and published for the "best strategy in the ensemble" claim.
- [ ] A locked out-of-sample (OOS) window of at least 90 days exists and is enforced — no tuning, no read access during research, single-use validation only.
- [ ] CHANGELOG entries document each gate added.
- [ ] `CLAUDE.md` references the new mandatory pre-scaling checks.

---

# Item P1-2 — Fix Universe Selection Bias

## Problem

`scheduler/run_backtest.py` includes a hand-picked `EXTENDED_POOL` of ~100 stocks. Some of those names (SMCI, ARM, MSTR, HOOD, COIN, NET, PANW, CRWD) became prominent **after** the 2024 AI/BTC rally. Backtesting them over 2025 captures a survivorship/selection bias that you would not have had access to in real time. The result: the headline +20.7% is partially the system's edge and partially the bias.

The backtester does respect point-in-time data *within* each window (correctly), but the **pool composition itself is forward-biased**. Fixing this is the second-highest-leverage statistical change after walk-forward.

## Goal

Universe membership at each backtest date is **only** symbols that would have been knowable to a 2019 / 2020 / 2022 trader on that date.

## Approach

Replace the hand-picked pool with **point-in-time index membership** plus an explicit "addition rule" for non-index names.

**Primary source:** historical S&P 500 constituents (free from Wikipedia + GitHub-maintained PIT lists, or paid from CRSP if budget allows). Optionally extend with historical Russell 1000 membership for breadth.

**Secondary inclusion rule for non-index names:** a stock can enter the pool 90 days after IPO and only if it meets the screener's liquidity floor (min_adv_dollars 50M) as of the candidate inclusion date. This way new IPOs (ARM, RBLX, ABNB) can be added but only after they have a tradable trading record.

## Work Breakdown

**Task 2.1 — Inventory the current bias.** [4h]
For each symbol in the current `EXTENDED_POOL`, document IPO date, first date it would have met the screener's liquidity floor, and rough "fame date" (date it appeared in any major index or hit $10B market cap). Save to `docs/universe_audit.md`. Some symbols (MSTR, ARM, SMCI in current form) will have a "fame date" inside the backtest window — these are the survivorship contributors.

**Task 2.2 — Acquire historical index membership.** [1d]
Pull S&P 500 constituent lists by date. Free option: the `pit_sp500.csv` files maintained on GitHub (multiple options, verify against Wikipedia's "List of S&P 500 companies" change log). Paid option: CRSP/Sharadar (~$50–200/mo). Store as `data/universe/sp500_constituents.csv` with columns `[symbol, added_date, removed_date]`.

**Task 2.3 — Build `PITUniverseBuilder`.** [1d]
New class in `screener/pit_universe.py`. Method `members_as_of(date) -> list[str]` returns the universe that would have been knowable on `date`. Internally:
- Index members active on that date, **plus**
- Non-index names that meet the inclusion rule, **minus**
- Anything that was delisted before `date`.

**Task 2.4 — Wire `PITUniverseBuilder` into the backtest path.** [4h]
`run_backtest.py` currently reads `EXTENDED_POOL` as a Python constant. Replace with a call to `PITUniverseBuilder` keyed by `sim.current_date`. Keep `EXTENDED_POOL` available behind a `--legacy-pool` CLI flag for A/B comparison only — never the default.

**Task 2.5 — Re-run the 12-month default backtest with the PIT universe.** [4h]
Document the delta vs. the current `EXTENDED_POOL` result. Expectation: headline return drops by 200–500 bps. **That delta is the survivorship bias** you were unknowingly harvesting. Publish in `reports/universe_bias_audit.md`.

**Task 2.6 — Unit tests.** [4h]
Cover: a symbol added to the index on date D is excluded for date D-1; a symbol removed on date D is included for date D-1; non-index names respect the 90-day-after-IPO rule; delisted names don't appear in any window after delisting.

**Task 2.7 — Deprecation path.** [2h]
Add a deprecation warning when `EXTENDED_POOL` is invoked. Remove the constant entirely in a follow-up release after 30 days.

## Acceptance

- [ ] `screener/pit_universe.py` exists with `members_as_of(date)` and is the default universe source for `run_backtest.py`.
- [ ] `reports/universe_bias_audit.md` quantifies the headline delta.
- [ ] Tests pass; existing backtest tests updated to use the new universe.
- [ ] `CHANGELOG.md` documents the change and the "delta represents prior survivorship bias" finding.

## Risks

| Risk | Mitigation |
|---|---|
| Free PIT membership data is incomplete for pre-2015 dates | Limit early windows to symbols with verifiable PIT membership; document gaps explicitly in each report |
| Delta vs. legacy pool is uncomfortably large (>10% lower headline return) | This is the *correct* result — do not "fix" by re-adding bias. Update the documented forward expectation downward |
| Strategies that worked on the hand-picked tech tilt fail without it | Either accept the lower return, add a tech-tilt strategy explicitly (with appropriate risk caps), or document that the system is not a general equity bot |

---

# Item P1-3 — Code-Enforced Sample-Size Discipline

## Problem

`config/config.yaml` declares `validation.min_reliable_trades: 30` as a watch-only warning. Today **Gap-Up (n=12) and Range Breakout (n=7–10) are below that floor but still run at full 1% risk / 8% position size in live**. The 70% win rate on Range Breakout is statistically indistinguishable from 50% at that sample size.

The fix is to make the floor **mechanical** — the strategy's per-trade risk and position cap scale with its accumulated closed-trade count, not with operator discretion.

## Goal

A strategy with <30 closed trades runs at exploratory risk (≤0.25% per trade). A strategy with 30–100 trades runs at half-risk (0.5%). A strategy with 100+ trades runs at full configured risk. Enforcement is in code, not policy.

## Approach

Add a **risk-scaling layer** that sits between strategy signal generation and order sizing. Reads closed-trade counts from `tracking.trade_log` and returns an effective risk multiplier.

## Work Breakdown

**Task 3.1 — Define the scaling schedule.** [2h]
Defaults (configurable per-strategy):
```yaml
validation:
  sample_size_scaling:
    enabled: true
    tiers:
      - { min_trades:   0, risk_multiplier: 0.25, position_cap_pct: 0.02 }  # exploration
      - { min_trades:  30, risk_multiplier: 0.50, position_cap_pct: 0.04 }  # half
      - { min_trades: 100, risk_multiplier: 1.00, position_cap_pct: 0.08 }  # full
```

**Task 3.2 — `core/sample_size_governor.py`.** [1d]
Pure function `effective_risk_for(strategy: str, cfg: dict, closed_trades: int) -> RiskMultiplier`. Reads tier from config, picks the highest tier whose `min_trades` ≤ `closed_trades`. Returns multiplier *and* the per-strategy position cap override.

**Task 3.3 — Wire into `core/order_executor.py`.** [4h]
Before calling `construct_entry_size`, fetch closed trade count for that strategy from `tracking.trade_log.get_closed_trades(strategy=...)`. Apply the multiplier to `risk_per_trade_pct` and the position cap override to `max_position_pct`. Log the scaling decision to the trade log entry under a new `risk_tier` column for auditability.

**Task 3.4 — Wire into `scheduler/run_backtest.py`.** [3h]
The backtester needs to apply the same scaling so backtest results reflect realistic forward sizing. In the backtest, "closed trades" means *closed trades within the simulation*, not historical live trades. Each strategy starts at tier 0 and graduates as its sim trades close. **This is important** — it means a backtested strategy with strong early performance still ramps up gradually, which is the realistic forward behaviour.

**Task 3.5 — Reporting and visibility.** [3h]
Add a "risk tier per strategy" section to the daily report from `run_report.py`. Show: strategy, closed trade count, current tier, effective risk, days until next tier promotion (estimate).

**Task 3.6 — Override mechanism.** [2h]
Allow `validation.sample_size_scaling.overrides.<strategy>` for manual override (only with a human-readable `reason` and `expires_on` date). Logged loudly.

**Task 3.7 — Tests.** [4h]
- Tier transitions at exact boundaries.
- Strategy with no historical trades returns tier-0 risk.
- Backtest tier graduation: simulate 50 winning trades and verify the strategy progresses through tiers mid-simulation.
- Override expiration: an expired override is ignored and a warning logged.

## Acceptance

- [ ] Gap-Up and Range Breakout automatically run at 0.25% risk / 2% position cap in live until they accumulate 30 closed live trades.
- [ ] The daily report shows risk tier per strategy.
- [ ] Backtests reflect the same scaling (no more "infinite confidence on day one").
- [ ] `CLAUDE.md` documents that operator must not edit live `risk_per_trade_pct` to bypass the gate — overrides go through the documented mechanism.

## Risks

| Risk | Mitigation |
|---|---|
| Strategies that need many trades to mature (Range Breakout) may never graduate to full tier | Acceptable. If a strategy can't produce 30 trades in 6 months at exploratory size, it doesn't deserve more capital |
| Backtest results change after scaling is wired in (likely lower) | Document the change as "more realistic forward expectation" |
| Operator urge to override | Override mechanism requires explicit reason + expiry; logged to trade log |

---

# Item P1-4 — Bootstrap / Monte Carlo Confidence Intervals

## Problem

Every published metric (return, drawdown, Sharpe, profit factor) is a **point estimate** from one realisation of one backtest. The same strategy run on the same data with trades resampled in a different order produces a noticeably different drawdown. Without confidence intervals, the operator cannot tell "this is real" from "this is sampling luck."

Institutional practice: report a 95% confidence interval, and **plan to the lower bound, not the point estimate**.

## Goal

Every backtest result includes:
- Median, 5th-percentile, 95th-percentile for: return, max drawdown, profit factor, Sharpe, win rate.
- Probability of a 12-month return below 0%.
- Probability of a 12-month drawdown exceeding 10%.

## Approach

**Bootstrap on the trade ledger.** Resample with replacement from the backtest's closed trades 10,000 times. For each resample, reconstruct an equity curve and compute the metrics. The distribution of those metrics gives you the confidence intervals.

This is the standard technique because it preserves the strategy's per-trade payoff distribution while randomising the *order* of trades, which is what reveals path-dependent risk (especially drawdown).

Two-variant choice — implement both:

1. **Trade-resample bootstrap** (path-randomising). Most useful for drawdown CIs.
2. **Block bootstrap** on daily returns (preserves autocorrelation). Most useful for Sharpe and return CIs.

## Work Breakdown

**Task 4.1 — `analysis/bootstrap.py`.** [1d]
Functions:
- `trade_resample(trades_df, n_iter=10000, seed=42) -> pd.DataFrame` returning columns `[iter, return_pct, max_drawdown, profit_factor, win_rate]`.
- `block_bootstrap_returns(daily_returns, block_size=5, n_iter=10000, seed=42)` returning the same shape.
- `summarise(distribution_df) -> dict` with median, p05, p95, prob_loss, prob_dd_gt_X.

**Task 4.2 — Hook into `run_backtest.py`.** [4h]
After the main simulation completes, run both bootstraps on the resulting trade ledger and daily-equity curve. Add a new section to the report:
```
## Bootstrap confidence intervals (10,000 iterations)
| Metric | Median | 5th pct | 95th pct |
| Return  | +18.4% | +6.2% | +29.1% |
| Max DD  | -3.8%  | -1.2% | -8.7%  |
| Sharpe  |   1.92 |  0.94 |  2.71  |
| P(loss over 12m): 4.1%  |
| P(DD > -10% over 12m): 7.3%  |
```

**Task 4.3 — Hook into `run_validation_gate.py` and `run_walkforward.py`.** [4h]
Gates currently evaluate point estimates. Change them to evaluate **the lower 5th percentile** of the bootstrap distribution for failure-side metrics (return ≥ X, profit factor ≥ Y), and the **upper 95th percentile** for risk metrics (drawdown ≤ X). This is a stricter gate but the right one.

**Task 4.4 — Backtest reports include the new section.** [2h]
Update `backtests.md` template. Add the bootstrap CI to every committed report going forward.

**Task 4.5 — Tests.** [4h]
- Bootstrap on a known synthetic series (e.g. all-positive trades) produces a 95% return CI that excludes zero.
- Block bootstrap on autocorrelated returns produces wider CIs than i.i.d. bootstrap (sanity check that blocking is doing something).
- Seed reproducibility: same seed produces identical distribution.

**Task 4.6 — Documentation.** [2h]
Add `docs/interpreting_bootstrap_cis.md` explaining how to read the numbers, why the lower bound is the planning number, and what an unreasonably wide CI implies (insufficient trade count).

## Acceptance

- [ ] Every committed backtest report in `reports/` includes the bootstrap section.
- [ ] Validation gates use lower 5th percentile / upper 95th percentile rather than point estimates.
- [ ] Tests pass.
- [ ] The 12-month default backtest's bootstrap output is committed to `reports/bootstrap_default_12m.md` as the reference number going forward.

## Risks

| Risk | Mitigation |
|---|---|
| Bootstrap is computationally expensive on each backtest | 10,000 iterations on ~100 trades is sub-second in numpy. Negligible |
| CIs reveal the headline is statistically borderline | This is the *point*. Update forward expectations accordingly |
| Trade-resample doesn't preserve regime context (a bear-market trade can land in a bull resample) | Acknowledged limitation; that's why block bootstrap on daily returns runs alongside it |

---

# Item P1-5 — Multiple-Testing Correction (White's Reality Check / Hansen SPA)

## Problem

Across the project's history, the team has tested dozens of strategy variants, hundreds of parameter combinations, and multiple universes. The current production set is the result of **selection**: things that looked best after this many tests get kept. The probability that the "best" of those is best by chance, given how many were tried, is not negligible. Without correction, you cannot honestly answer "is this edge real or selection bias?"

White's Reality Check (Bonferroni-style) and Hansen's Superior Predictive Ability (SPA) test are the academic standard for this. They take a candidate "best" strategy and a universe of alternatives, then compute a p-value for "the best is significantly better than the benchmark **after** correcting for the number of strategies tried."

## Goal

For every strategy that is enabled in production, publish a p-value indicating the probability that its observed performance is **at least this good by chance**, given the size of the search space.

## Approach

The Hansen SPA test is the modern standard. Simplified pipeline:

1. Define benchmark: in this case, "buy-and-hold SPY" is appropriate for stock strategies; "buy-and-hold BTC" for crypto strategies.
2. Define the universe of tested strategies: every committed config in git history that produced a backtest result, plus a Monte Carlo sweep across a wider parameter grid for each currently enabled strategy (50–200 variants).
3. For each candidate, compute the loss differential vs. the benchmark over the backtest window.
4. Bootstrap the maximum loss differential across the universe under the null hypothesis that no strategy is better than the benchmark.
5. Report the p-value: probability of seeing a loss differential at least as extreme as the candidate's.

## Work Breakdown

**Task 5.1 — Define the strategy search-space catalog.** [1d]
Reconstruct from git history (`git log --all -- config/config.yaml strategies/`) the set of strategy variants that have been backtested. Approximate count by scanning commits. Plus: for each *currently enabled* strategy, define a parameter grid of 50–100 variants (e.g. for momentum: top_n in [1,2,3], min_momentum_pct in [0.05, 0.06, 0.08, 0.10, 0.12], atr_multiplier in [0.8, 1.0, 1.2, 1.5, 2.0], etc.). This grid represents "what we *would* have tested if we'd been systematic."

**Task 5.2 — `analysis/spa_test.py`.** [2d]
Implement Hansen SPA following Hansen (2005). Inputs: a (T × K) matrix of strategy daily returns (T days, K strategies), a (T,) vector of benchmark daily returns. Outputs: p-value, SPA test statistic, identifier of the best-performing strategy under the test.

The reference implementation in the `arch` Python package is canonical — wrap it rather than reimplement to reduce bug surface.

**Task 5.3 — Backtest harness for the parameter grid.** [1d]
Extend `run_backtest.py` with `--grid <strategy>` mode that runs all parameter combinations and emits a (T × K) returns matrix. Cache results aggressively — this generates a lot of compute.

**Task 5.4 — Run the SPA test for each currently enabled strategy.** [variable, 1–3d compute]
For each of momentum, RSI reversion, gap-up, MA crossover, range breakout: run the grid, run SPA, publish p-value.

**Task 5.5 — Publish `reports/multiple_testing_correction.md`.** [4h]
One section per strategy. Each section: parameter grid description, number of variants tested, benchmark, SPA p-value, interpretation. Decision rule:
- p < 0.05: strong evidence of real edge after correction. Strategy keeps full risk allocation.
- 0.05 ≤ p < 0.20: weak evidence. Strategy stays enabled but capped at half the sample-size-scaling-allowed risk.
- p ≥ 0.20: not statistically distinguishable from a lucky variant. **Strategy is paused** until additional out-of-sample evidence accumulates.

**Task 5.6 — Tests.** [4h]
- SPA on synthetic data where strategy K is constructed to be better than benchmark by N sigma: test recovers the strategy and produces a low p-value.
- SPA on synthetic data of K i.i.d. random walks: produces p > 0.5 (correctly fails to reject the null).
- SPA p-value monotonically increases as K (number of candidates) increases on the same best-strategy data (correctly punishes search-space size).

**Task 5.7 — Add to mandatory pre-scaling workflow.** [1h]
Update `CLAUDE.md`: "Before scaling live allocation for any strategy, the SPA p-value for that strategy must be < 0.20 on the latest grid run."

## Acceptance

- [ ] `analysis/spa_test.py` exists, wraps `arch.bootstrap.SPA` or equivalent, and is unit-tested.
- [ ] `reports/multiple_testing_correction.md` is published with a p-value for each currently enabled strategy.
- [ ] At least one strategy is correctly *not* p < 0.05 — that's the realistic outcome on these sample sizes and is itself useful information.
- [ ] The pre-scaling workflow references the SPA p-value.

## Risks

| Risk | Mitigation |
|---|---|
| Most strategies fail SPA at these sample sizes | This is *the most likely outcome* and is *correct*. The right response is humility about edge, not parameter tuning |
| SPA is mathematically subtle and easy to misapply | Use the `arch` library implementation; reference Hansen (2005). Do not implement from scratch |
| Defining "the search space" is judgmental | Document the chosen grid explicitly; an honest narrower grid is better than a hand-wavy wider one |
| Compute cost for grid runs is high | Cache aggressively; this is a one-off + quarterly refresh, not continuous |

---

# Item P1-6 — Out-of-Sample Lockup

## Problem

Every dataset that has ever been examined for tuning is contaminated. Today the team has full visibility into all historical data when iterating on strategy parameters. Even with good intentions, this creates implicit lookahead — you remember which months were good, you stop trying parameter changes that hurt the recent quarter, etc.

The standard institutional control: **lock the most recent N days. No one looks at them, no tuning runs against them, no parameter sweeps touch them.** They exist only as a one-shot validation. If the locked window passes, the strategy is approved; if it fails, the strategy is rejected (you do not re-tune to fix the locked window — that would re-contaminate it).

## Goal

A 90-day OOS lockup window is enforced **in code**, not just in policy. Any backtest invocation that would read data from the locked window without an explicit `--oos-validation` flag must fail.

## Approach

A wrapper around historical data access that filters out the locked window unless the caller passes the explicit unlock token. The token is rate-limited (e.g. can only be passed once per quarter per locked window).

## Work Breakdown

**Task 6.1 — Define lockup metadata.** [2h]
New file `data/oos_lockup.json`:
```json
{
  "current_lockup": {
    "start_date": "2026-02-15",
    "end_date":   "2026-05-15",
    "created_at": "2026-02-15T00:00:00Z",
    "last_validation_at": null,
    "last_validation_outcome": null
  },
  "history": []
}
```

**Task 6.2 — Lockup-aware data wrapper.** [1d]
New module `core/data_lockup.py`. Wraps `ac.get_stock_bars` and `ac.get_crypto_bars`. By default, filters out bars whose timestamps fall within `current_lockup`. Pass-through if caller provides `oos_unlock_token=<token>`. Token validates against `data/oos_lockup.json` and is single-use per quarter.

**Task 6.3 — Wire wrapper into `run_backtest.py` and all strategy scans.** [4h]
Replace direct `ac.get_stock_bars` calls with the wrapper in the backtest path. Live trading is unaffected (lockup wrapper is a no-op when called with `current_date` in the live present).

**Task 6.4 — `--oos-validation` mode in `run_backtest.py`.** [4h]
Single command that runs the locked OOS window with the unlock token. Produces a one-shot validation report. Marks the lockup as "validated" in `oos_lockup.json` and the lockup window is then *rolled forward* — the most recent 90 days becomes the new lockup, the previously locked window becomes available for analysis.

**Task 6.5 — Pre-commit hook.** [3h]
Check that no committed file under `reports/` references data from inside the current lockup window. Block commit if it does (with explicit override only for `--oos-validation` reports).

**Task 6.6 — Tests.** [4h]
- Default backtest invocation excludes the locked window's bars.
- `oos_unlock_token` permits access; subsequent attempts to use the same token fail.
- Pre-commit hook catches a report containing a locked date.
- Quarterly rollover correctly advances the lockup window.

**Task 6.7 — Documentation and policy.** [2h]
Add `docs/oos_lockup_policy.md`. Add to `CLAUDE.md`: "Before any 'scale capital' or 'enable strategy' decision, the locked OOS window must have been validated within the last 30 days and passed." Add the unlock procedure to the operations runbook.

## Acceptance

- [ ] `data/oos_lockup.json` exists with a current 90-day lockup window.
- [ ] Backtest invocations exclude the locked window by default.
- [ ] `python3 scheduler/run_backtest.py --oos-validation` produces a single-shot report and rolls the lockup forward.
- [ ] Pre-commit hook blocks accidental leakage.
- [ ] Documentation is in place; `CLAUDE.md` references the lockup gate.

## Risks

| Risk | Mitigation |
|---|---|
| Operator (or agent) finds a way to peek at the locked window | Pre-commit hook + the wrapper + explicit unlock token. Defence in depth. The wrapper is the load-bearing control |
| 90 days isn't long enough to be statistically meaningful | True. 90 days is the floor; once the system has 12+ months of live data, the lockup can extend to 180 days |
| Validation of the locked window fails | This is the system working as designed. The response is not to re-tune but to accept the failure and re-research the strategy |
| Single-use token is lost or misused | Token is in a committed file; usage is logged; quarterly rotation gives a fresh one regardless |

---

## Phase 1 Cross-Cutting Concerns

### Order of operations

The five items above are **mostly independent** and can be parallelised, with one constraint:

1. **P1-2 (universe fix)** should land *before* P1-4 (bootstrap CIs), so the CIs reflect the corrected universe.
2. **P1-3 (sample-size scaling)** can land any time.
3. **P1-5 (SPA test)** should run *after* P1-2 (so the search space reflects the corrected universe) but can develop in parallel.
4. **P1-6 (OOS lockup)** should land first — every other change benefits from the lockup discipline.

Suggested sequence: P1-6 → P1-2 → (P1-3 and P1-4 in parallel) → P1-5.

### Cumulative impact on headline numbers

Expect the headline +20.7% / -1.92% to degrade after Phase 1. Realistic post-Phase-1 numbers, in my view:

- Return: **+8% to +14%** (universe fix removes 200–500 bps of survivorship; sample-size scaling removes a few hundred more from previously oversized strategies).
- Max drawdown: **-3% to -6%** (slightly worse than current, but more honest).
- Sharpe: **1.2 to 1.8** (down from the current 3.31, which was unrealistically high anyway).
- Bootstrap 5th percentile return: **-2% to +4%** (this is the planning number — that's what you scale capital against).

**These are better numbers because they will hold up live.** The current headline will not.

### Code-organisation note

This phase adds several new modules. Suggested layout:

```
analysis/
  __init__.py
  bootstrap.py          # P1-4
  spa_test.py           # P1-5
core/
  data_lockup.py        # P1-6
  sample_size_governor.py  # P1-3
screener/
  pit_universe.py       # P1-2
data/
  universe/
    sp500_constituents.csv  # P1-2
  oos_lockup.json       # P1-6
docs/
  universe_audit.md     # P1-2
  interpreting_bootstrap_cis.md  # P1-4
  oos_lockup_policy.md  # P1-6
reports/
  bootstrap_default_12m.md  # P1-4
  multiple_testing_correction.md  # P1-5
  universe_bias_audit.md  # P1-2
```

### Test discipline

Every item adds tests under `tests/`. Run `python3 -m unittest discover -v` after each item. The current suite is at 686 tests passing — Phase 1 should add ~30–50 tests. Do not allow regressions.

---

## What "Done" Looks Like

When Phase 1 is complete, the answer to *"how do you know this works?"* changes from:

> "We ran a 12-month backtest with the latest config and got +20.7% with -1.92% drawdown."

to:

> "Across the multi-regime walk-forward (separate plan), the system passes at 30bps stressed cost in 5/7 windows. The locked OOS window passed at stressed cost within the last 30 days. The universe is point-in-time S&P 500 + filtered non-index names. Bootstrap CIs show a 12-month return 5th-percentile of +6%, drawdown 95th-percentile of -7%. The SPA test for our best strategy returns p=0.08 against a 200-variant grid. Strategies below 30 closed trades are mechanically capped at 0.25% risk."

The first statement is what most retail bots can produce. The second is what an institutional risk committee will accept. That's the gap Phase 1 closes.

---

*End of Phase 1 plan.*
