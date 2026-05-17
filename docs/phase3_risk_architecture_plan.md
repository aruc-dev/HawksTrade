# Phase 3 — Risk Architecture Upgrade

**Status:** Draft for review
**Owner:** Arun
**Priority:** P1 — execute alongside or after Phase 2; Phase 1 must be substantially complete
**Target window:** 8–12 weeks
**Prerequisites:** Phase 1 walk-forward and bootstrap CIs in place. Phase 2 TCA and trade-log instrumentation are not blocking but make Phase 3 monitoring much sharper.

The system's current risk framework — per-trade ATR sizing, sector caps, daily-loss baseline, protection cooldowns, order governor, broker-side stops, correlation guard — is already above average. Phase 3 closes the remaining gap to *systematic, portfolio-level* risk control. The shift is from "good rules per trade" to "an actively-managed portfolio-level risk model."

The six items below correspond to items **12–17** of Phase 3 in `reports/REVIEW_2026-05-16.md`.

---

## Acceptance Criteria for the Whole Phase

Phase 3 is "done" when **all** of the following are true:

- [ ] Daily portfolio factor exposures are computed, logged, and visible in the daily report. Limits are code-enforced.
- [ ] Portfolio-level volatility targeting is enabled; gross exposure scales with realised vol.
- [ ] Correlation-aware sizing applies to equity positions, not just crypto. Cluster-level risk budgets are enforced.
- [ ] Multi-horizon drawdown circuit breakers (daily, 3-day, 20-day) are code-enforced with documented responses at each threshold.
- [ ] A documented stress test catalog exists with at least 6 historical regime events. Every config change runs the catalog before deployment.
- [ ] An independent watchdog process exists, runs continuously, and pages on inconsistencies.
- [ ] CHANGELOG documents each control; `CLAUDE.md` references the new mandatory checks.

---

# Item P3-12 — Portfolio Factor Decomposition

## Problem

The system currently controls risk **per trade** (ATR sizing) and **per sector** (max 1 momentum position per GICS sector). It does **not** know its portfolio's exposure to systematic factors: market beta, size (small vs. large cap), value vs. growth, momentum factor, low-volatility factor, quality factor. This matters because:

- In April 2025, a hypothetical "long top-tech momentum" portfolio had ~1.4 beta to QQQ. A 2% market drop = 2.8% portfolio drop, regardless of how many "stop-losses" each individual position had.
- The system's good 2025 performance is partially a *factor bet* (long momentum + long quality tech), not pure alpha. Without measuring factor exposure, the operator cannot tell which.
- An institutional risk committee will not approve scaling without factor reporting.

## Goal

Daily, automated computation of portfolio factor exposures using a standard factor model (Fama-French 5 + Momentum, or Barra-style sector + style if data permits). Limits are enforced — if the portfolio drifts above the configured cap on any factor, new entries that would increase that factor are blocked.

## Approach

For this scale, a **regression-based attribution** using freely available factor returns (Kenneth French Data Library, daily, free, well-maintained) is the right tool. Hire-a-Barra-license is overkill.

For each open position:
- Run a 252-day rolling OLS regression of the stock's daily returns on the factor returns (Mkt-RF, SMB, HML, RMW, CMA, Momentum).
- The regression coefficients (betas) are the position's factor loadings.

Portfolio-level exposure = weighted sum of position betas, where weights are dollar-position-as-fraction-of-portfolio.

## Work Breakdown

**Task 12.1 — Factor data ingestion.** [1d]
New module `data/factor_loader.py`:
- Downloads Kenneth French daily factor data (Mkt-RF, SMB, HML, RMW, CMA, MOM, RF) from the canonical URL.
- Caches as parquet under `data/factors/ff5_mom_daily.parquet`.
- Update script `scripts/refresh_factor_data.py` for monthly refresh; schedule via cron.

**Task 12.2 — Per-symbol factor regression.** [1d]
New module `analysis/factor_attribution.py`:
- `compute_factor_betas(symbol, end_date, lookback_days=252) -> dict[str, float]` returns the 6 factor betas + R² + idiosyncratic_vol.
- Uses minute-cache or daily history; falls back gracefully if <120 days of data.
- Caches results by (symbol, end_date) to avoid recomputation.

**Task 12.3 — Portfolio aggregation.** [4h]
`analysis/factor_attribution.py::portfolio_exposure(positions, as_of) -> dict`:
- For each position, gets its factor betas.
- Computes dollar-weighted portfolio betas.
- Returns: `{Mkt: 0.85, SMB: 0.12, HML: -0.31, MOM: 0.62, ...}`.

**Task 12.4 — Factor limit configuration.** [2h]
```yaml
risk:
  factor_limits:
    enabled: true
    market_beta:   { min: -0.30, max:  0.80 }
    size_factor:   { min: -0.40, max:  0.40 }
    value_factor:  { min: -0.50, max:  0.50 }
    momentum_factor: { min: -0.20, max:  0.80 }   # we are explicitly a momentum-tilted system
    quality_factor: { min: -0.30, max:  0.50 }
    investment_factor: { min: -0.40, max:  0.40 }
  factor_breach_action: block_new_entries   # block_new_entries | warn_only
```

**Task 12.5 — Pre-trade factor check.** [4h]
In `core/risk_manager.py.pre_trade_check`: before approving an entry, compute "what the portfolio's factor exposure would be *if* this position were added." If any factor goes outside its limit, block the entry. Log the breach with the factor name and projected value.

**Task 12.6 — Daily report integration.** [3h]
`scheduler/run_report.py` adds a section:
```
Portfolio Factor Exposures (as of 2026-05-16):
  Market beta:     +0.62 (within [-0.30, +0.80])
  Size factor:     +0.18 (within limits)
  Momentum factor: +0.58 (within limits)
  ...
  R² of portfolio vs. factors: 0.78
  Idiosyncratic vol:           4.2% (annualised)
```

**Task 12.7 — Tests.** [1d]
- Factor regression on a synthetic stock that is exactly 1.5x market returns recovers beta≈1.5.
- Portfolio aggregation correctly weighted-averages position betas.
- Factor-limit pre-trade check blocks the right entries and lets others through.
- Falls back gracefully when factor data is stale (>30 days old).

**Task 12.8 — Documentation.** [3h]
`docs/factor_risk.md`: what each factor means, how to interpret the daily report, when to override limits (with documented procedure).

## Acceptance

- [ ] Factor data refreshes monthly without operator intervention.
- [ ] Daily report shows portfolio factor exposures.
- [ ] Pre-trade check enforces factor limits.
- [ ] At least one trade in the test suite is correctly blocked by a factor limit.

## Risks

| Risk | Mitigation |
|---|---|
| FF factor data has lag (typically published with 1–2 day delay) | Use the most recent available factors; if >7 days old, treat as warm-up and don't enforce limits (warn only) |
| Single-symbol regressions noisy for small samples | Require 120+ days of history; fall back to sector-average betas if unavailable |
| Factor regime change makes historical betas misleading | 252-day rolling window self-corrects; consider faster exponential decay weighting in v2 |
| Crypto has no clean factor model | Phase 3 factor work is equities-only; crypto continues to rely on correlation guard |

---

# Item P3-13 — Portfolio Volatility Targeting

## Problem

Today, every position is sized to risk a fixed % of equity per trade. The portfolio's *realised volatility* is therefore an emergent property — sometimes 8%, sometimes 18%, dictated by which strategies happen to be active and how many positions are open.

Institutional convention is the opposite: target a fixed annualised portfolio volatility (commonly 8–12%), then scale gross exposure up or down to match. Quiet markets → more leverage. Wild markets → less. The math:

```
exposure_multiplier = target_vol / max(realised_vol_20d, target_vol)
```

This single equation smooths the equity curve materially and reduces drawdowns in vol-spike regimes without sacrificing returns over a full cycle.

## Goal

A portfolio-level multiplier on all per-trade sizing, recomputed daily from trailing 20-day realised portfolio volatility, that targets a configured annualised vol (default 10%).

## Approach

Compute realised portfolio volatility from the equity curve (already tracked in `data/performance.csv`). Apply an exposure multiplier to all new entries. The multiplier is *capped* (e.g. 0.3 to 1.5) to prevent runaway sizing in either direction.

## Work Breakdown

**Task 13.1 — Volatility computation.** [4h]
New function in `tracking/performance.py`:
- `realised_portfolio_vol(lookback_days=20, annualisation=252) -> float`
- Reads daily portfolio values, computes log returns, returns annualised stddev.
- Returns NaN if <10 days of history; caller treats as "target_vol" (no scaling).

**Task 13.2 — Volatility target configuration.** [2h]
```yaml
risk:
  volatility_targeting:
    enabled: true
    target_annual_vol_pct: 0.10
    lookback_days: 20
    exposure_multiplier_min: 0.30
    exposure_multiplier_max: 1.50
    smoothing_lookback_days: 5    # EMA the multiplier over 5 days to avoid whipsaw
```

**Task 13.3 — Integration into sizing.** [4h]
In `core/risk_manager.py`:
- New function `vol_target_multiplier() -> float` — returns the EMA-smoothed multiplier.
- `calculate_position_size` and `cap_position_qty` multiply their outputs by this multiplier.
- Logged on every entry: "vol target multiplier: 0.85 (realised_vol_20d=11.8%, target=10.0%)"

**Task 13.4 — Interaction with sample-size scaling (P1-3).** [2h]
Two multipliers stack: `effective_risk = base_risk * sample_size_multiplier * vol_target_multiplier`. Document the interaction. Ensure the cap_position_qty respects the smaller of the two.

**Task 13.5 — Daily report integration.** [2h]
Show: realised 20d vol, target vol, current multiplier (raw and smoothed), historical 30-day chart of the multiplier.

**Task 13.6 — Backtest integration.** [4h]
Backtest must apply the same multiplier so backtest and live behaviour match. In the backtest, "realised vol" is computed from the simulated equity curve. The multiplier starts at 1.0 (no history) and converges as the simulation progresses.

**Task 13.7 — Tests.** [4h]
- High realised vol (>2x target) correctly reduces multiplier toward the floor.
- Low realised vol (<0.5x target) correctly raises multiplier toward the ceiling.
- Insufficient history returns multiplier=1.0.
- EMA smoothing reduces day-to-day multiplier volatility.
- Backtest with vol targeting produces a smoother equity curve than without (assertable on a synthetic vol-spike scenario).

## Acceptance

- [ ] Vol targeting is enabled by default at 10% target.
- [ ] Daily report shows the current multiplier.
- [ ] Backtest re-runs with vol targeting show smoother equity curve (committed comparison report).

## Risks

| Risk | Mitigation |
|---|---|
| Multiplier oscillates day-to-day | 5-day EMA smoothing mitigates; can extend to 10 days if needed |
| In a sustained low-vol regime, multiplier hits the ceiling and exposure stays high through a vol regime change | The ceiling (1.5) caps that risk; protection manager and drawdown circuit breakers catch a regime change |
| Backtest vol targeting changes historical results meaningfully | Document and accept; this is an *intentional* improvement to risk-adjusted return at the cost of headline return in trending bulls |

---

# Item P3-14 — Correlation-Aware Equity Sizing

## Problem

The crypto correlation guard correctly blocks adding ETH if BTC is already a position with >0.85 correlation. **Nothing equivalent exists for equities.** A momentum scan that simultaneously fires on NVDA, AVGO, AMD, and MRVL — all 0.85+ correlated semiconductors — currently passes the sector cap (because only 1 enters per sector) but does not control for the broader correlation cluster. The result: when one chip stock drops, the entire cluster drops.

The fix is to compute correlation **clusters** (not just sectors) and risk-budget across clusters.

## Goal

Before approving an equity entry, compute the new position's correlation with all open equity positions. If the position would breach a cluster-level dollar-risk budget, either reject the entry or size it down.

## Approach

For each candidate entry:
1. Compute 60-day rolling correlation with each open position.
2. Identify correlation cluster: open positions with corr ≥ 0.65 to the candidate.
3. Sum the dollar risk of the cluster (each position's `position_size × stop_distance_pct`).
4. If `cluster_dollar_risk + candidate_dollar_risk > cluster_budget`, reject or downsize.

Cluster budgets are configured as fractions of total equity (e.g. 2% per cluster).

## Work Breakdown

**Task 14.1 — Generalise the correlation guard.** [1d]
Refactor `core/correlation_guard.py` to:
- Accept stocks (not just crypto).
- Expose `compute_correlation(symbol_a, symbol_b, lookback_days=60, asset_class='stock') -> float`.
- Keep existing crypto-specific path as the default for crypto callers.

**Task 14.2 — Cluster identification.** [4h]
New function `identify_cluster(candidate, open_positions, threshold=0.65) -> list[Position]`. Returns positions whose correlation with candidate exceeds threshold.

**Task 14.3 — Cluster-level risk budget.** [4h]
```yaml
risk:
  cluster_risk_budget:
    enabled: true
    correlation_threshold: 0.65
    max_cluster_dollar_risk_pct: 0.02   # 2% of equity per cluster
    sizing_action: downsize             # downsize | reject
    min_downsize_qty_pct: 0.25          # below this, treat as reject
```

**Task 14.4 — Pre-trade integration.** [4h]
In `core/risk_manager.pre_trade_check`: after sector check, before position-cap check, compute candidate's cluster and its current dollar risk. Apply the configured action. Log decisions.

**Task 14.5 — Daily report integration.** [3h]
Show current clusters and their dollar risk budgets vs. consumed. Example:
```
Clusters (corr ≥ 0.65):
  [NVDA, AVGO, AMD]     dollar_risk: 1.4% / 2.0% budget
  [JPM, GS, MS]         dollar_risk: 0.6% / 2.0% budget
  [BTC/USD, ETH/USD]    dollar_risk: 1.1% / 2.0% budget   [crypto path]
```

**Task 14.6 — Backtest integration.** [4h]
Backtester must apply the same clustering for fair comparison. Correlations come from the historical data already loaded.

**Task 14.7 — Tests.** [1d]
- Three highly correlated semis: third entry is rejected or downsized.
- Diversified portfolio (uncorrelated names): no rejections.
- Correlation computation matches numpy `corrcoef` on known input.
- Downsize action correctly returns reduced qty.
- Edge: empty open-positions list returns "no cluster, no rejection."

## Acceptance

- [ ] Correlation-aware sizing applies to equities, not just crypto.
- [ ] Daily report shows cluster dollar-risk consumption.
- [ ] At least one regression test demonstrates a third correlated entry being rejected/downsized.

## Risks

| Risk | Mitigation |
|---|---|
| 60-day correlations are unstable for newer issues | Require min 60 days of overlap; if unavailable, treat correlation as 1.0 (most conservative) |
| Cluster threshold of 0.65 is arbitrary | Make configurable; sensitivity-test by re-running the backtest at 0.55 and 0.75 |
| Downsizing produces sub-min trade values | Use `min_downsize_qty_pct` to convert tiny downsizes into rejections; logged for visibility |

---

# Item P3-15 — Multi-Horizon Drawdown Circuit Breakers

## Problem

The system has a 5% daily loss limit that halts new entries for the rest of the day. This is good but incomplete. Missing:

- **Multi-day drawdown limits.** A series of small daily losses (e.g. -1.5% × 4 days = -6% over a week) never triggers the daily limit but is exactly the pattern that precedes the worst drawdowns historically.
- **Strategy-level kill switches.** A single strategy losing on every trade for two weeks is a regime signal; the system currently keeps trading it.
- **Graduated response.** Today's reaction is binary: under 5% → trade normally, over 5% → halt entirely. Institutional practice is graduated: -3% → reduce risk multiplier 0.5x, -5% → halt new entries, -8% → liquidate to neutral.

## Goal

Three-tier drawdown response: **dampen, halt, liquidate**. Plus per-strategy kill switches based on losing streaks and rolling drawdown.

## Approach

Extend the existing daily-loss baseline machinery in `core/risk_manager.py` to track rolling drawdowns over multiple horizons. Each horizon has thresholds. Actions are code-enforced.

## Work Breakdown

**Task 15.1 — Rolling drawdown computation.** [4h]
New module `core/drawdown_monitor.py`:
- `current_drawdown(lookback_days) -> float` — drawdown from rolling-window high to current portfolio value.
- Persists per-window state to `data/drawdown_state.json` so a restart doesn't lose context.

**Task 15.2 — Multi-horizon configuration.** [3h]
```yaml
risk:
  drawdown_circuit_breakers:
    enabled: true
    horizons:
      - days: 1
        thresholds:
          - { level: 0.03, action: warn }
          - { level: 0.04, action: dampen, multiplier: 0.50 }
          - { level: 0.05, action: halt }
      - days: 3
        thresholds:
          - { level: 0.04, action: warn }
          - { level: 0.05, action: dampen, multiplier: 0.50 }
          - { level: 0.07, action: halt }
      - days: 20
        thresholds:
          - { level: 0.05, action: warn }
          - { level: 0.07, action: dampen, multiplier: 0.50 }
          - { level: 0.10, action: halt }
          - { level: 0.12, action: liquidate }
    liquidation_path: closed_orderly  # closed_orderly | market_panic
```

**Task 15.3 — Per-strategy kill switch.** [1d]
```yaml
risk:
  strategy_kill_switch:
    enabled: true
    rolling_lookback_trades: 10
    max_drawdown_pct: 0.06
    consecutive_loss_threshold: 5
    action: disable_until_review
```
New module `core/strategy_kill_switch.py`. After every closed trade for a strategy: compute rolling P&L of last N trades for that strategy. If drawdown exceeds threshold or consecutive losses exceed threshold, set the strategy's `enabled: false` in a runtime override file and log loudly.

**Task 15.4 — Integration.** [1d]
- `core/risk_manager.pre_trade_check` consults drawdown monitor; multiplies effective sizing by the active multiplier; rejects entry on halt.
- Strategy `scan()` methods consult the kill switch override file; return empty signals if disabled.
- `scheduler/run_risk_check.py` triggers liquidation when the 20-day liquidate threshold is breached.

**Task 15.5 — Daily report integration.** [3h]
Show: current drawdown for each horizon, active multiplier, active strategy disables. Three months of history as a sparkline.

**Task 15.6 — Backtest integration.** [4h]
Backtester applies all the same circuit breakers. Necessary for backtest results to reflect realistic risk behaviour.

**Task 15.7 — Liquidation playbook.** [4h]
Document and code the "closed_orderly" liquidation path: cancel all open entry orders, then exit positions sized worst-to-best with marketable limits, with a 2-minute pause between each to avoid spiking the market against yourself. Logged step-by-step.

**Task 15.8 — Tests.** [1d]
- Each tier correctly triggers at exact thresholds.
- Multipliers stack: a halt action takes precedence over dampen.
- Strategy kill switch correctly disables after 5 consecutive losses.
- Liquidation path correctly orders exits and respects pause intervals.
- Backtest with circuit breakers active shows smaller drawdowns on synthetic crash scenarios.

**Task 15.9 — Documentation.** [3h]
`docs/circuit_breakers.md`: every threshold, every action, recovery procedure, override mechanism.

## Acceptance

- [ ] Three-tier multi-horizon circuit breakers active by default.
- [ ] Per-strategy kill switch active by default.
- [ ] Daily report shows drawdown across all horizons.
- [ ] Backtest with synthetic crash demonstrates dampen → halt → liquidate progression.
- [ ] Liquidation path tested in paper mode end-to-end at least once.

## Risks

| Risk | Mitigation |
|---|---|
| Circuit breakers trigger during normal noise | The graduated response (warn/dampen/halt/liquidate) handles this — only liquidation is irreversible, and that's at -12% over 20 days, which is well outside normal noise |
| Liquidation creates fire-sale losses | Use limit orders with model-derived slippage cap; pause between exits |
| Strategy kill switch disables a strategy mid-recovery | "Disable until review" requires human (or scheduled) re-enable; document a daily review prompt |
| Backtest drawdown improvements come at the cost of return | Acceptable — risk-adjusted return is the goal, not headline return |

---

# Item P3-16 — Stress Test Catalog

## Problem

The walk-forward (P1-1) validates the system across recent regimes. But it does not validate the **current portfolio composition** against past events. If you have NVDA + AVGO + MRVL open and the next event is "2024-08 yen carry unwind tech selloff," what happens? The walk-forward can't answer that because it backtested in those regimes; it doesn't shock the *current* book.

The institutional standard is a documented catalog of historical regime events, each replayed as a shock to the current portfolio. Run before every config change to live.

## Goal

A `stress_tests/` directory with at least 6 documented event scenarios. A single command (`python3 scheduler/run_stress_tests.py`) runs all of them against the current open positions. CI gates on the result.

## Approach

Each scenario specifies:
- Event date range.
- Per-symbol or per-sector daily-return shocks.
- Optional: correlation/volatility regime overrides.

The runner takes the current open positions, applies the daily shocks, computes resulting portfolio P&L and drawdown, and reports whether limits would have been breached.

## Work Breakdown

**Task 16.1 — Scenario schema.** [4h]
```yaml
# stress_tests/scenarios/2020_covid_crash.yaml
name: 2020 COVID crash
description: Feb-Mar 2020 fastest 30%+ drawdown in S&P history.
event_window: { start: 2020-02-19, end: 2020-03-23 }
shocks:
  spy_daily_returns:    [-0.005, -0.018, ..., -0.092, 0.06, ..., -0.117]
  vix_multiplier:       4.0
  correlation_override: 0.90    # most equities became 0.9+ correlated
  sector_overrides:
    "Energy":   { daily_return_multiplier: 1.8 }   # energy hit much harder
    "Health Care": { daily_return_multiplier: 0.6 }  # health less so
```

**Task 16.2 — Source initial scenarios.** [1d]
At minimum:
1. 2020-03 COVID crash (1 month, -34% SPY)
2. 2022-Q2 tech bear (3 months, -25% QQQ)
3. 2023-03 SVB shock (1 week, -8% regional banks)
4. 2023-10 rates spike (1 month, -10% growth)
5. 2024-08 yen carry unwind (1 week, -7% tech, -12% Nikkei equivalent)
6. 2025-04 tariff shock (in your existing data — extract the bad week)

Data: download daily prices for SPY, sector ETFs, and any symbols currently in scan_universe, for the event windows. Compute daily returns. Store as the shock vectors.

**Task 16.3 — Stress test runner.** [1d]
`scheduler/run_stress_tests.py`:
- Loads current open positions (from broker or trade log).
- For each scenario in `stress_tests/scenarios/*.yaml`:
  - Apply daily shocks per symbol or per sector.
  - Walk forward day-by-day applying current risk rules (stop-losses, take-profits, circuit breakers).
  - Track: daily P&L, peak-to-trough drawdown, would-have-triggered circuit breakers.
- Produce `reports/stress_tests/<run_date>.md` summary table.

**Task 16.4 — Configurable pass criteria.** [3h]
```yaml
stress_tests:
  pass_criteria:
    max_simulated_drawdown_pct: 0.15
    max_circuit_breaker_liquidation_pct: 0.10
    required_scenarios: [2020_covid, 2022_tech_bear, 2024_yen_carry]
```

**Task 16.5 — Integration with config change workflow.** [3h]
Pre-commit hook: any change to `config/config.yaml` strategy or risk sections triggers `run_stress_tests.py --quick` against current portfolio. Failure blocks commit. Slow (full catalog) version runs nightly.

**Task 16.6 — Tests.** [4h]
- Synthetic scenario: 5 consecutive -10% days correctly produces ~40% drawdown when no circuit breakers active.
- Same scenario with breakers active correctly halts after second day, limits drawdown to ~12%.
- Empty open positions returns "no exposure, all scenarios pass trivially."

**Task 16.7 — Documentation.** [3h]
`docs/stress_test_catalog.md`: each scenario, its real-world context, the pass criteria, how to add a new scenario.

## Acceptance

- [ ] At least 6 scenarios in `stress_tests/scenarios/`.
- [ ] `scheduler/run_stress_tests.py` runs all of them in <5 minutes.
- [ ] Pre-commit hook runs the quick version on config changes.
- [ ] Nightly cron runs the full catalog and posts result.
- [ ] Documentation in place.

## Risks

| Risk | Mitigation |
|---|---|
| Historical shocks are not the next shock | Acknowledged; stress tests are a floor on preparedness, not a ceiling. Add new scenarios as new events occur |
| Per-symbol shock data unavailable for newer symbols | Fall back to sector-level shocks for symbols without per-symbol data |
| Tests reveal current portfolio fails badly on 2022 scenario | Acceptable and useful information. Either reduce gross exposure, add factor limits (P3-12), or accept the realistic worst case |

---

# Item P3-17 — Independent Watchdog Process

## Problem

The trading process is its own monitor. If it has a bug that miscounts positions, fails to detect orphan orders, or silently drops a fill, **no one notices until the daily report or until the broker complains**. Single-source-of-truth monitoring is a known weakness.

Institutional standard: a separate process, with no responsibility for placing trades, whose only job is to cross-check that everything is consistent. Reads broker state, reads trade log, reads run markers, raises alarms on inconsistency.

## Goal

A continuously-running watchdog (separate systemd unit) that performs structural integrity checks every 5 minutes during market hours. On any inconsistency, it logs, alerts (PagerDuty or equivalent), and optionally cancels all open orders.

## Approach

A small Python service in `watchdog/` with read-only access to: broker (positions, orders, account state), trade log, run markers, daily-loss baseline, drawdown state. Performs a checklist on each cycle. Stateless apart from "last alert time per check" to avoid alert flooding.

## Work Breakdown

**Task 17.1 — Watchdog skeleton.** [1d]
New module `watchdog/main.py`:
- Loop: every 5 minutes (configurable), run all checks.
- Each check returns `(name, status, details)` where status is `ok | warn | error`.
- All checks logged to `logs/watchdog_<date>.log`.
- Structured output to `data/watchdog_state.json` for the daily report to consume.

**Task 17.2 — Initial check catalog.** [2d]
1. **Position parity.** Broker positions match trade-log open positions (symbol, qty within 0.001 tolerance).
2. **Orphan orders.** Every broker open order has a corresponding entry in order_intents; every order_intent submitted in the last 60 minutes either filled, was rejected, or is still open at the broker.
3. **Trade log integrity.** No open trade without a corresponding broker position.
4. **Daily loss baseline.** `data/daily_loss_baseline.json` exists, has today's date, value is positive and plausible (within ±50% of current portfolio value).
5. **Drawdown state.** `data/drawdown_state.json` is current (<10 minutes old).
6. **Run marker freshness.** A scheduled run (e.g. scan) was expected within the last 60 minutes during market hours; if missing, alert.
7. **Broker auth.** Can fetch account state without 401.
8. **Pending orders age.** No open broker order older than 30 minutes for a daily-bar strategy.
9. **Live mode interlock present.** If `mode: live` is configured, `HAWKSTRADE_LIVE_ACK` is set in the environment of the trading process.
10. **Disk space.** `/dev/shm` and the data directory have >10% free.
11. **Clock drift.** System clock vs. NTP within 5 seconds.

**Task 17.3 — Alerting integration.** [1d]
- Stage 1: log to a dedicated watchdog log + emit a structured warning the daily report picks up.
- Stage 2 (when budget permits): PagerDuty (free tier 5 services), email via SES, or SMS via Twilio. For solo operation, push to a private Slack/Discord webhook is the cheapest acceptable option.
- Alert deduplication: same check failing repeatedly only alerts once per hour.

**Task 17.4 — Optional cancel-all kill switch.** [4h]
For a configurable subset of errors (orphan orders + position parity), the watchdog can be authorised to cancel all open broker orders. **Off by default**; enable only after operator is confident in false-positive rate.

**Task 17.5 — systemd unit.** [3h]
`scheduler/systemd/hawkstrade-watchdog.service` and `.timer`. Runs the watchdog every 5 minutes during 09:00–17:00 ET on weekdays, and every 30 minutes 24/7 for crypto-relevant checks.

**Task 17.6 — Tests.** [1d]
- Each check correctly detects its target condition on a mocked broker/trade-log.
- Alert deduplication: rapid identical errors produce one alert per hour.
- Cancel-all path correctly invokes broker cancellation; off-by-default verified.

**Task 17.7 — Documentation and runbook.** [4h]
`docs/watchdog.md`: every check, expected failure modes, recovery procedures.

## Acceptance

- [ ] Watchdog runs as a separate systemd unit.
- [ ] All 11 checks implemented and tested.
- [ ] Alerts surface to operator within 5 minutes of inconsistency.
- [ ] Documented runbook for each failure mode.
- [ ] One full end-to-end test in paper mode: introduce a deliberate position mismatch, verify watchdog detects and alerts within one cycle.

## Risks

| Risk | Mitigation |
|---|---|
| Watchdog has bugs that produce false-positive alerts | Start in "log-only" mode for 2 weeks; only enable cancel-all after false-positive rate is <1/week |
| Watchdog itself crashes silently | systemd `Restart=always`; external uptime check (UptimeRobot free tier) hits a /health endpoint |
| Alert fatigue | Per-check rate limiting; severity tiers; quiet hours configurable |
| Cancel-all kill switch nukes legitimate orders | Off by default. Enable only on the checks most worth nuking for (position parity, orphan orders) |

---

## Phase 3 Cross-Cutting Concerns

### Order of operations

The six items have moderate dependencies:

1. **P3-17 (watchdog)** first — independent of the others, immediate operational benefit, lowest risk to deploy.
2. **P3-15 (drawdown circuit breakers)** second — extends existing well-understood machinery; high impact.
3. **P3-12 (factor decomposition)** and **P3-13 (vol targeting)** in parallel — both rely on portfolio-level views; independent of each other.
4. **P3-14 (correlation-aware equity sizing)** — extends the existing crypto guard; lower urgency.
5. **P3-16 (stress test catalog)** last — most useful once the other risk controls are in place to demonstrate.

### Cumulative impact on headline numbers

Phase 3's effect on backtest headlines:

- Vol targeting in a bull market → slightly lower headline return (multiplier <1 during the 2025 vol spikes), materially lower drawdown.
- Factor limits → reduces concentration in any one factor; smoother equity curve, possibly lower return in factor-bull regimes.
- Cluster correlation limits → reduces effective leverage in correlated rallies; fewer of the "all 4 semis up 12% same week" wins.
- Multi-horizon circuit breakers → reduces tail-loss drawdowns; tiny impact in normal periods.
- Strategy kill switches → eliminates persistent losers earlier; small positive expected impact.

Net post-Phase-3, on top of Phase 1 and Phase 2: another 50–200 bps lower headline return, but materially better Sharpe (1.5–2.0 realistic) and materially smaller drawdowns (4–7% realistic max). This is the trade institutions consciously make: lower headline, better risk-adjusted, scalable.

### Code-organisation note

Suggested layout:

```
analysis/
  factor_attribution.py    # P3-12
core/
  drawdown_monitor.py      # P3-15
  strategy_kill_switch.py  # P3-15
  # correlation_guard.py — extended in-place for P3-14
data/
  factors/
    ff5_mom_daily.parquet  # P3-12
  drawdown_state.json      # P3-15
  watchdog_state.json      # P3-17
scheduler/
  run_stress_tests.py      # P3-16
  systemd/
    hawkstrade-watchdog.service / .timer  # P3-17
scripts/
  refresh_factor_data.py   # P3-12
stress_tests/
  scenarios/
    2020_covid_crash.yaml  # P3-16
    2022_tech_bear.yaml    # P3-16
    # etc.
watchdog/
  __init__.py
  main.py                  # P3-17
  checks/                  # P3-17
docs/
  factor_risk.md           # P3-12
  circuit_breakers.md      # P3-15
  stress_test_catalog.md   # P3-16
  watchdog.md              # P3-17
reports/
  stress_tests/<date>.md   # P3-16
```

### Test discipline

Phase 3 adds the most behavioural complexity of the three phases. ~80–120 new tests across all items. Pay particular attention to:

- **Interaction effects.** Vol targeting × sample-size scaling × factor limits × correlation guard × circuit breakers all multiply against base sizing. Integration tests that exercise the full chain are essential.
- **Stateful correctness.** Drawdown state, factor cache, watchdog state all persist across runs. Verify restart-safety.
- **Liquidation correctness.** The closed_orderly liquidation path must not double-submit, must not exceed broker rate limits, must complete even if some exits fail.

---

## What "Done" Looks Like

When Phase 3 is complete, the answer to *"what happens to the portfolio if the next month is bad?"* changes from:

> "We have a 5% daily loss limit and per-trade stop-losses."

to:

> "Daily, we measure portfolio factor exposures and refuse new entries that breach our limits. Position sizing is scaled to target 10% annualised portfolio volatility, currently running at 0.83x base sizing due to recent realised vol of 12%. Correlated-cluster risk is capped at 2% of equity per cluster — currently we have a 1.4% semis cluster and a 0.9% bank cluster. Drawdown circuit breakers dampen at -3% daily, halt at -5% daily, and liquidate at -12% over 20 days. The most recent stress test catalog shows the current portfolio loses 8.2% in a 2020-COVID replay and 6.1% in a 2022-tech-bear replay, both within tolerance. An independent watchdog checks 11 invariants every 5 minutes and has alerted 0 times in the last 30 days."

That paragraph — measurable, reactive, layered — is the difference between "we have risk controls" and "we have a risk *system*." It is the level a risk committee will sign off on at $250k+ capital.

---

*End of Phase 3 plan.*
