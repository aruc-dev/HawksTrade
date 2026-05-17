# Phase 2 — Execution & Microstructure

**Status:** Draft for review
**Owner:** Arun
**Priority:** P1 — execute after or in parallel with Phase 1 statistical work
**Target window:** 8–12 weeks
**Prerequisite:** Phase 1 walk-forward and bootstrap CIs in place, so you can measure the impact of execution changes against a stable benchmark.

The Phase 1 work hardens the *evidence*. Phase 2 hardens the *execution* — closing the gap between what the backtest assumes and what the live broker actually delivers. At today's $10k size, this gap is roughly 20–50 bps per round-trip; at $100k+ size it becomes the difference between a profitable system and a marginal one.

The five items below correspond to items **7–11** of Phase 2 in `reports/REVIEW_2026-05-16.md`.

---

## Acceptance Criteria for the Whole Phase

Phase 2 is "done" when **all** of the following are true:

- [ ] Gap-Up and Momentum volume-pace backtests use real minute bars; the synthetic 9:35 proxy is deprecated.
- [ ] Slippage in backtests is a function of symbol liquidity, time-of-day, and order size — not a flat constant.
- [ ] Live entries use a documented multi-leg execution policy with measurable improvement vs. naïve marketable-limit.
- [ ] A daily / monthly TCA report is published from the live trade log: implementation shortfall, slippage attribution, fill quality by strategy and time-of-day.
- [ ] End-to-end decision-to-submit latency is measured, logged, and within a documented budget (<500ms target).
- [ ] CHANGELOG documents each change; `CLAUDE.md` references the TCA report as a mandatory weekly review item.

---

# Item P2-7 — Real Minute-Bar Replay for Gap-Up and Momentum Volume-Pace

## Problem

`scheduler/run_backtest.py` currently fakes the intraday signal for Gap-Up and Momentum volume-pace. Specifically, the `_make_bar_fetcher` factory generates a synthetic single 9:35 ET bar with `high = low = close = open`, plus a volume estimate scaled from the daily volume by elapsed-session fraction. This is honest (the code calls it a "proxy" and the report surfaces it) but it means:

- **You cannot verify whether the bot would actually have filled.** The "fill" happens at the synthesised open price by construction.
- **You cannot verify whether the volume-pace filter would have passed.** Volume is interpolated linearly from daily, but real intraday volume is highly concentrated at the open and close. The first 5 minutes can easily be 5–10% of the day's volume.
- **The Gap-Up 75% win rate over 12 trades is not fill-validated.** Real opening-bar fills in fast-moving gappers are frequently 0.5–2% away from the modelled price.

This is the **single largest unaddressed correctness gap in the backtester**.

## Goal

Both backtest paths consume **actual 1-minute bars** for at least the symbol-of-interest during the entry-window minutes. Backtest reports stop emitting the "synthetic proxy" disclaimer for these two strategies.

## Approach

Alpaca's minute-bar history for major US equities extends to at least 2016. The data volume is significant (~50× daily), but only the **entry-window minutes** are needed (first 45 for Gap-Up, full session for Momentum volume-pace). Two strategies for managing the data:

1. **On-demand fetch with disk cache.** First backtest run for a (symbol, date) fetches minute bars from Alpaca and writes a parquet file under `data/minute_cache/<symbol>/<yyyymm>.parquet`. Subsequent runs read from cache.
2. **Bulk pre-fetch.** A one-time script downloads minute bars for the full universe × full backtest period into the cache. Higher upfront cost, faster repeated runs.

Recommendation: implement both. On-demand for ad-hoc and CI; bulk for the master walk-forward.

## Work Breakdown

**Task 7.1 — Minute-bar cache layer.** [1d]
New module `core/minute_cache.py`:
- `get_minute_bars(symbol, start, end) -> pd.DataFrame` checks parquet cache, fetches missing months from Alpaca, writes to cache, returns combined frame.
- Cache schema: `data/minute_cache/<symbol>/<yyyymm>.parquet` with columns `[timestamp, open, high, low, close, volume]`, UTC timestamps.
- Idempotent and concurrent-safe (file lock per (symbol, month) write).

**Task 7.2 — Bulk pre-fetch script.** [4h]
`scripts/prefetch_minute_bars.py --start 2019-01-01 --end auto --symbols=universe`. Multi-threaded (10 concurrent fetches), respects Alpaca rate limits, retries with backoff. Estimated runtime: 1–3 hours for the current universe across 5 years. Run once, then maintain.

**Task 7.3 — Wire into the Gap-Up backtest path.** [1d]
Replace the synthetic opening proxy in `_make_bar_fetcher` with a call to `minute_cache.get_minute_bars` for the relevant symbol and the entry-window date. The bars returned cover 9:30–10:15 ET (allowing 45-min entry window plus buffer). Gap-Up's `_session_opening_metrics` then operates on real data, computing actual session open, actual opening volume, actual current price as of the modelled scan time.

**Task 7.4 — Wire into the Momentum volume-pace backtest path.** [1d]
Same pattern but for the full session and at the configured scan time (default 15:55 ET). Momentum's `_session_volume_from_bars` operates on real intraday volume; the volume-pace ratio is then a real measurement.

**Task 7.5 — Realistic fill modelling.** [4h]
Once you have real minute bars, fills can be modelled honestly:
- **Marketable-limit entry:** modelled fill = next minute's open. If next minute's range doesn't include the limit price, no fill.
- **Slippage on top:** apply the liquidity-aware slippage model (P2-8) once it's built; until then, use a flat 5 bps adverse fill for stocks.
- **Partial fills:** if order size exceeds 10% of the next minute's volume, model partial fill at that proportion and re-attempt on subsequent minutes.

**Task 7.6 — Backward-compat flag.** [2h]
Keep the synthetic proxy code path behind `--use-synthetic-intraday` for A/B comparison only. Default off. Emits a warning in any report generated with it on.

**Task 7.7 — Re-run the master walk-forward.** [variable, ~1d compute]
Once minute-bar replay is wired, regenerate `reports/walkforward_master.md`. Expected: Gap-Up performance numbers change materially (probably downward — real fills are usually worse than synthetic opens). Document the delta in `reports/minute_bar_replay_audit.md`.

**Task 7.8 — Tests.** [1d]
- Cache hit/miss behaviour; concurrent fetch correctness.
- Minute-bar replay produces a non-empty bar series for known dates.
- Gap-Up signal with real bars correctly *rejects* a date where the opening volume pace failed (regression catch for the synthetic case where it would have passed).
- Fill modelling correctly returns "no fill" for a limit beyond the next minute's range.

**Task 7.9 — Storage and bandwidth accounting.** [2h]
Document expected cache size (~5–10 GB for current universe × 5 years), Alpaca API quota implications, and recommended cache pruning policy.

## Acceptance

- [ ] `core/minute_cache.py` exists, tested, used by backtest.
- [ ] Gap-Up and Momentum backtests show no "synthetic proxy" disclaimer in their reports.
- [ ] `reports/minute_bar_replay_audit.md` documents the delta between synthetic-proxy and real-minute results.
- [ ] Walk-forward master re-runs and is committed.
- [ ] Cache directory is git-ignored; bulk-fetch script is documented in `scheduler/README.md`.

## Risks

| Risk | Mitigation |
|---|---|
| Alpaca minute data is gappy or missing for some symbols/dates | Detect and either skip the strategy for that (symbol, date) or fail-closed; never silently substitute synthetic data |
| Real-fill modelling shows Gap-Up's edge was synthetic-fill-dependent | Acceptable and correct — that's the audit's purpose. Reduce Gap-Up risk allocation accordingly via the sample-size governor |
| Cache size grows beyond what's reasonable on the EC2 | Move cache to S3 with on-demand fetch and a small local LRU |
| Bulk-fetch hits Alpaca rate limits | Run overnight, chunked, with checkpointing |

---

# Item P2-8 — Liquidity-Aware Slippage Model

## Problem

`config/config.yaml` declares a flat `slippage_bps: 10.0` for the validation cost model. This is fine for SPY at 14:00 ET. It is **wrong** for:

- A 9:35 entry on a $50 ARM gapper (real slippage easily 25–60 bps).
- Any small-cap or mid-cap in the universe during fast tape.
- Crypto pairs other than BTC/ETH (LINK, AVAX, DOT, UNI, AAVE can show 30–100 bps slippage on $5k orders during volatile periods).
- Any order whose size is a meaningful fraction of the bar's volume.

The literature standard is **the square-root impact model**:

```
slippage_bps = k * volatility_bps * sqrt(order_size / ADV)
```

Where `k` is calibrated empirically from your own fills. This handles all three factors above with one formula.

## Goal

Backtest and live execution use the same liquidity-aware slippage function. The function is calibrated against actual fills from a paper-trading evidence base and re-calibrated quarterly.

## Approach

Two-phase: implement the model, then calibrate it.

### Implementation phase

The model returns a slippage estimate in basis points given inputs known at order time:
- `symbol`
- `asset_class` (stock | crypto)
- `order_size_usd`
- `bar_volume_usd` (volume of the current 1-minute bar in dollars)
- `adv_usd` (20-day average daily volume in dollars)
- `realised_volatility_bps` (intraday volatility on the day)
- `side` (buy | sell)
- `time_of_day` (HH:MM ET)

Output: `expected_slippage_bps`. Default formula:

```
base = k_asset_class
liquidity = sqrt(order_size_usd / adv_usd)
vol_factor = realised_volatility_bps / 100
tod_factor = 1.5 if time in [09:30-09:45, 15:50-16:00] else 1.0
asym = 1.2 if side == 'buy' else 1.0  # buys typically pay more on long-only

slippage_bps = base * liquidity * vol_factor * tod_factor * asym
```

`k_asset_class` defaults: stocks=8, crypto=15. These are starting values to be replaced by calibrated values once paper data exists.

### Calibration phase

For every paper-trade fill, capture: arrival price, fill price, decision price, the inputs above, and the realised slippage. Fit `k_asset_class` (and optionally per-symbol multipliers for the most-traded names) by minimising RMSE between model and realised.

## Work Breakdown

**Task 8.1 — `core/slippage_model.py`.** [1d]
Pure function as specified. Configurable via `config/config.yaml`:
```yaml
slippage_model:
  enabled: true
  k_stock: 8.0
  k_crypto: 15.0
  tod_open_window:  [["09:30", "09:45"]]
  tod_close_window: [["15:50", "16:00"]]
  open_multiplier: 1.5
  buy_asymmetry: 1.2
  per_symbol_overrides: {}  # populated by calibration
```

**Task 8.2 — Replace flat slippage in backtests.** [4h]
`scheduler/run_backtest.py`'s `_normalise_cost_model` and `BacktestSimulator._execution_price` currently apply a constant. Replace with a call to `slippage_model.estimate(...)`. The existing `validation.cost_model.sensitivity_levels_bps` becomes a *multiplier on the model output*, not a flat replacement.

**Task 8.3 — Apply at live order submission.** [4h]
In `core/order_executor.py`, when computing the limit price for a marketable-limit order, derive the offset from `slippage_model.estimate(...)` rather than the flat `limit_slippage_pct: 0.001`. The model's output is used as a sanity ceiling — if it predicts >50 bps slippage, the order is logged as "high-cost" for review.

**Task 8.4 — Slippage capture in the trade log.** [4h]
Trade log gains new columns:
- `decision_price` (price at signal generation)
- `arrival_price` (mid quote when order was submitted)
- `expected_slippage_bps` (from the model)
- `realised_slippage_bps` (computed after fill)

These are required inputs for both TCA (P2-10) and recalibration.

**Task 8.5 — Calibration script.** [1d]
`scripts/calibrate_slippage_model.py --since=<date>`. Reads recent live/paper fills from the trade log, fits `k_stock` and `k_crypto` via least squares, optionally adds per-symbol multipliers for symbols with >20 fills. Outputs the proposed config change as a PR-ready diff. Does not auto-apply — operator reviews and commits.

**Task 8.6 — Walk-forward re-run with the model.** [variable]
Once the model is in place with default (uncalibrated) `k` values, re-run the master walk-forward. Expect headline returns to drop further as small-cap and opening-window costs become more realistic.

**Task 8.7 — Tests.** [4h]
- Slippage scales with sqrt of order size (regression test on a known input).
- Open-window multiplier kicks in at 09:35, not at 10:30.
- Crypto base higher than stock base.
- Per-symbol override correctly takes precedence over default.
- Calibration on synthetic fills with known true `k` recovers `k` within 10%.

**Task 8.8 — Documentation.** [2h]
`docs/slippage_model.md`: formula, parameter meanings, calibration procedure, recommended quarterly review.

## Acceptance

- [ ] `core/slippage_model.py` exists and is used everywhere slippage is computed.
- [ ] Trade log captures decision/arrival/expected/realised slippage for every fill.
- [ ] Calibration script can run end-to-end on the current trade log.
- [ ] `validation.cost_model.sensitivity_levels_bps` becomes a multiplier on the model, not a flat replacement.
- [ ] Documentation in place.

## Risks

| Risk | Mitigation |
|---|---|
| Insufficient paper-trade fills for calibration | Start with literature defaults (k_stock=8, k_crypto=15); calibrate only when n ≥ 50 fills per asset class |
| Per-symbol overrides over-fit to small samples | Require min n=20 fills per symbol before assigning a per-symbol multiplier |
| Model is wrong in unexpected regimes (e.g. flash crash) | Cap the model's output at 200 bps; log a warning when the cap is hit; rely on protection manager for the rest |

---

# Item P2-9 — Smart Order Routing (Multi-Leg Entries)

## Problem

Current entry uses a single marketable-limit at `price × (1 + 0.001)` (10 bps above mid). For liquid names in calm markets this is fine. For:

- Gap-Up entries in the first 5 minutes (fast tape, wide spreads, slippage can be 30–80 bps).
- Mid-cap momentum entries (CRWD, PANW, ARM-class names).
- Crypto entries on smaller pairs.

…a single aggressive marketable-limit will often pay more than necessary. The institutional standard for retail-size orders is a **two-leg passive-then-aggressive split**:

1. **Leg 1 (passive):** place 50% of the order as a limit at the mid (or just inside the bid). Wait 60–120 seconds. Pay zero slippage if it fills.
2. **Leg 2 (aggressive):** if leg 1 hasn't fully filled by timeout, cancel it and place the remaining qty as a marketable-limit at `mid + (slippage_model estimate)`. Guaranteed fill, controlled cost.

Empirically this saves 5–15 bps round-trip on liquid names and more on less-liquid names. At your future $100k size and ~100 trades/year, that's $50–150/year per bp — non-trivial.

## Goal

A documented, A/B-tested entry execution policy that demonstrably reduces realised slippage vs. the single-leg baseline.

## Approach

Implement as a configurable strategy on top of the existing `core/order_executor.py`. Off by default; enable per-asset-class with a config flag.

## Work Breakdown

**Task 9.1 — `core/execution_policy.py`.** [1d]
New module. Class `MultiLegEntryPolicy` with config:
```yaml
execution_policy:
  enabled: true
  policy: "two_leg_passive_aggressive"  # or "single_leg_marketable_limit"
  per_asset_class:
    stock:
      leg1_fraction: 0.5
      leg1_offset_bps: 0      # at mid
      leg1_timeout_seconds: 90
      leg2_offset_bps: "model" # from slippage_model
    crypto:
      leg1_fraction: 0.5
      leg1_offset_bps: 5      # 5 bps inside ask
      leg1_timeout_seconds: 60
      leg2_offset_bps: "model"
```

The policy returns a list of `OrderLeg` dataclasses for the order executor to submit and manage.

**Task 9.2 — Order executor multi-leg handling.** [1d]
Extend `enter_position` in `core/order_executor.py`:
- Submit leg 1.
- Poll fill status every 5 seconds up to `leg1_timeout_seconds`.
- If fully filled: done.
- If partially filled: cancel residual leg 1, submit leg 2 for the unfilled qty.
- If unfilled: cancel leg 1, submit leg 2 for the full qty.

Idempotency: each leg gets a distinct deterministic `client_order_id` derived from the parent intent + leg number. Existing idempotency machinery handles retries.

**Task 9.3 — Order governor extensions.** [4h]
Governor must understand multi-leg orders so it doesn't double-count for rate limits. Add `parent_intent_id` to the OrderIntent dataclass; treat all legs of one parent as one "logical order" for rate-limit accounting.

**Task 9.4 — A/B test framework.** [4h]
Add `execution_policy.ab_test: {fraction: 0.5}` config. Half of qualifying entries use the new policy, half use the legacy single-leg. Trade log captures which policy was used. After ~100 fills, compute realised slippage by policy and publish.

**Task 9.5 — Backtest integration.** [4h]
Backtester must honour the same policy so simulated and live behaviour match. In backtest, leg-1 fill assumption: filled iff the next minute's low ≤ leg-1 limit ≤ next minute's high. Otherwise leg 2 fills at slippage-model estimate.

**Task 9.6 — Tests.** [1d]
- Two-leg policy submits both legs with distinct client_order_ids.
- Timeout cancellation works.
- Partial fill handoff: leg 2 sizes correctly to residual.
- A/B framework correctly bucketises orders.
- Backtest replay deterministically picks fill outcome from minute-bar OHLC.

**Task 9.7 — Publish A/B result.** [2h]
After ≥100 fills (probably 4–8 weeks of paper trading depending on activity), publish `reports/execution_policy_ab_test.md` with median realised slippage by policy, by asset class, by strategy. Decision rule: if multi-leg shows ≥3 bps median improvement vs. single-leg with p<0.05, promote multi-leg to default; otherwise keep single-leg as default and document the negative result.

## Acceptance

- [ ] `core/execution_policy.py` exists and is integrated into `enter_position`.
- [ ] A/B test runs for at least 100 fills.
- [ ] Result is published and committed.
- [ ] Default policy is whichever the A/B test supports; the loser remains available as a config option.

## Risks

| Risk | Mitigation |
|---|---|
| Leg 1 fill timing creates partial exposure during the timeout | Acceptable for swing trades; document. For any future intraday strategies, shorten `leg1_timeout_seconds` to 15–30s |
| Cancel-after-partial-fill leaves a leg orphan if cancellation fails | Order governor's existing duplicate-pending-exit machinery catches this; broker reconciliation cleans up |
| Multi-leg policy adds latency that hurts time-sensitive entries (Gap-Up) | A/B test should reveal this. If Gap-Up shows worse net cost with multi-leg, configure Gap-Up to use single-leg specifically |

---

# Item P2-10 — Post-Trade Transaction Cost Analysis (TCA)

## Problem

You have no idea, right now, whether your live fills match your backtest assumptions. The system logs trades and computes P&L, but it does not separate **strategy P&L** from **execution P&L**. A strategy can show -2% live vs. +5% backtested either because the strategy degraded or because execution gave back 7% — and you cannot tell which without TCA.

TCA is the standard institutional discipline of measuring **implementation shortfall** per trade and aggregating it.

## Goal

A weekly TCA report, generated automatically, that breaks down realised slippage by strategy, asset class, time-of-day, symbol, and order size. Trends over time are visible. Anomalies (e.g. a single bad fill 200 bps off model) are flagged.

## Approach

Build on the trade-log columns added in P2-8 (decision_price, arrival_price, expected_slippage_bps, realised_slippage_bps). The TCA report is a pandas pivot over those columns.

## Work Breakdown

**Task 10.1 — TCA module.** [1d]
`tracking/tca.py`:
- `compute_implementation_shortfall(trade) -> dict` returns `{is_bps, slippage_bps, timing_bps, fees_bps, total_bps}`. Implementation shortfall is the canonical metric: `(fill_price - decision_price) / decision_price * 10000` for buys (negated for sells).
- `aggregate_by(df, dimensions: list[str]) -> pd.DataFrame` pivots fills by the given dimensions.

**Task 10.2 — Daily TCA computation.** [4h]
Add to `scheduler/run_report.py`: after the existing daily summary, append TCA section showing yesterday's fills with IS, slippage, timing. One line per fill plus aggregate.

**Task 10.3 — Weekly TCA report.** [1d]
`scheduler/run_weekly_tca.py`. Generates a markdown report covering the prior 7 days:
- Median and 95th percentile slippage by strategy.
- Slippage by hour-of-day (heatmap-style table).
- Top 10 worst fills (for manual review).
- Model vs. realised: median residual, RMSE, R² between expected and realised slippage.
- Trend: 4-week rolling median slippage vs. configured cost model.

Schedule via the existing cron/systemd infrastructure on Monday 08:30 ET.

**Task 10.4 — Anomaly alerting.** [4h]
After each scan, check the last fill against the model. If realised slippage exceeds model estimate by 3σ (computed from the trailing 30 days), log a `WARNING` line and increment a counter. If 3 such warnings occur in one day, escalate (mark the run as `error` for the run-markers system to pick up).

**Task 10.5 — Backtest vs. live drift monitor.** [1d]
Cross-cutting with the P3 work but most naturally lives in TCA. Weekly: re-run the production backtest for the prior 7 days with the *exact* config that was live. Compare daily P&L curve. If drift exceeds 2σ of expected variance, alert. This is the single best automated check that the system is behaving as designed.

**Task 10.6 — Tests.** [4h]
- IS computation on synthetic fills with known true cost.
- Aggregation by strategy/hour/symbol.
- Anomaly detection fires at 3σ but not 2σ.
- Backtest-vs-live drift correctly identifies a deliberate parameter mismatch.

**Task 10.7 — Documentation.** [2h]
`docs/tca_interpretation.md`: how to read the report, what to do when median slippage exceeds the model, how to identify whether degradation is execution-side or strategy-side.

## Acceptance

- [ ] `tracking/tca.py` exists and is tested.
- [ ] Daily report includes a TCA section.
- [ ] Weekly TCA report runs on schedule and writes to `reports/tca_weekly_<date>.md`.
- [ ] Anomaly alerts surface in the run-markers system.
- [ ] Backtest-vs-live drift check runs weekly.
- [ ] `CLAUDE.md` adds a mandatory weekly review of the TCA report before any allocation changes.

## Risks

| Risk | Mitigation |
|---|---|
| Decision_price not captured for legacy fills | TCA back-fills decision_price from the first scan log entry for that signal; older fills get a "no decision price" flag and are excluded from aggregates |
| Anomaly noise drowns the signal | Tune the 3σ threshold based on observed false-positive rate; consider a 24-hour cool-down between similar anomalies |
| Backtest-vs-live drift always shows drift due to position-cap interactions | Document expected sources of "benign" drift; alert only when drift exceeds the *modelled* variance of those sources |

---

# Item P2-11 — Latency Budget and Measurement

## Problem

Today there is no documented end-to-end latency budget. For daily-bar swing strategies, latency is forgiving — a 30-second decision-to-submit delay rarely costs more than a few bps. But:

- For any future intraday strategy this becomes critical.
- For Gap-Up, a 5-second delay can move the entry past `max_open_extension_pct` and cause the order to be rejected by the strategy itself.
- For risk-check exits during fast moves, delay materially affects realised stop fills.

A measured, logged, alerted latency is the prerequisite for ever moving toward higher-frequency signals.

## Goal

Every order has timestamps for: signal generation, governor evaluation, broker submission, broker ACK. Latency by stage is logged. A weekly report shows P50/P95/P99 per stage. SLA: <500ms decision-to-submit for stocks, <2s for crypto.

## Approach

Add timestamp instrumentation to the existing order pipeline. Use monotonic clocks (not wall clock) for interval measurement. Log to a structured field on the trade log row.

## Work Breakdown

**Task 11.1 — Latency instrumentation.** [1d]
Add to `core/order_executor.py.enter_position`:
- `t_signal_received` (when strategy returned the signal)
- `t_governor_evaluated` (after order governor decision)
- `t_order_submitted` (just before broker call)
- `t_broker_ack` (when broker returned an order object)

Compute deltas and write to trade log under a new `latency_ms` JSON column: `{governor: X, submit: Y, ack: Z, total: W}`.

**Task 11.2 — Latency budget configuration.** [2h]
```yaml
latency_budget:
  stock_decision_to_submit_ms: 500
  crypto_decision_to_submit_ms: 2000
  alert_p95_breach: true
  alert_p99_breach: true
```

**Task 11.3 — Latency report.** [4h]
Add to weekly TCA report a section: P50/P95/P99 per stage, by asset class. Highlight any P95 that exceeded the budget.

**Task 11.4 — Real-time alerting.** [4h]
If any single order exceeds 5x the budget, log a `WARNING` and mark the run as `error`. Surfaces in the existing run-marker monitoring.

**Task 11.5 — Optimise the obvious offenders.** [variable]
Likely culprits based on the existing code:
- `ac.get_portfolio_value()` is called multiple times per scan. Cache for the duration of one scan.
- Order governor's `ac.get_open_orders()` and `ac.get_account()` are sequential. Run concurrently.
- `tracking.trade_log` read/write opens the CSV every time. Consider an in-memory cache for hot reads.

These are micro-optimisations but they compound to a 5–10x latency improvement, which buys headroom for future strategies.

**Task 11.6 — Tests.** [3h]
- Latency computation correct on a mocked clock.
- Budget breach correctly fires alert.
- Caching does not introduce a stale-read bug (cache invalidates on any order submission).

## Acceptance

- [ ] Every trade log entry has a `latency_ms` JSON field with the four stages.
- [ ] Weekly report shows latency percentiles.
- [ ] Real-time alert fires on 5x budget breach.
- [ ] Hot-path latency (decision_to_submit_ms) is documented; current baseline is recorded for trend tracking.

## Risks

| Risk | Mitigation |
|---|---|
| Adding instrumentation itself adds latency | Use monotonic timestamps in microseconds; overhead is sub-millisecond |
| Caching introduces inconsistency | Invalidate cache aggressively; cache lifetime ≤ duration of one scan |
| Alerting on every P95 breach is noisy | Alert only on P99 + sustained P95 breaches (e.g. 3 consecutive days above budget) |

---

## Phase 2 Cross-Cutting Concerns

### Order of operations

The five items have a natural dependency order:

1. **P2-7 (minute-bar replay)** first — everything else benefits from realistic intraday data.
2. **P2-8 (slippage model)** second — needs minute data for calibration; feeds into P2-9 and P2-10.
3. **P2-10 (TCA)** third — needs the new trade-log columns from P2-8; reveals whether P2-9 is helping.
4. **P2-9 (multi-leg execution)** in parallel with P2-10 — the A/B test result requires TCA to interpret.
5. **P2-11 (latency)** last — independent of the others; useful prep for future intraday work.

### Cumulative impact on headline numbers

Phase 2's effect on backtest headlines:

- Real minute-bar replay will most likely **reduce** Gap-Up's reported return (synthetic fills were too optimistic).
- Liquidity-aware slippage will **reduce** overall return for trades in less-liquid names.
- Multi-leg execution should **recover** some of that loss in live trading (5–15 bps round-trip).
- TCA itself doesn't change returns — it makes them measurable.

Net expected post-Phase-2 backtest result, on top of Phase 1's compression: another 100–300 bps lower headline, but the resulting numbers will closely match live experience, which is the entire point.

### Compute and storage budget

- Minute-bar cache: 5–10 GB (manageable on the current EC2; consider S3 for the master copy).
- Backtest runtime increase: ~2–3x for paths that consume minute bars. The current ~half-day master walk-forward becomes ~1–1.5 days. Use the cache aggressively.
- TCA computation: negligible (pandas pivots over recent fills).
- Latency instrumentation: sub-millisecond overhead per order.

### Code-organisation note

Suggested additions:

```
core/
  minute_cache.py        # P2-7
  slippage_model.py      # P2-8
  execution_policy.py    # P2-9
scripts/
  prefetch_minute_bars.py        # P2-7
  calibrate_slippage_model.py    # P2-8
scheduler/
  run_weekly_tca.py      # P2-10
tracking/
  tca.py                 # P2-10
data/
  minute_cache/          # P2-7 (git-ignored)
docs/
  slippage_model.md      # P2-8
  tca_interpretation.md  # P2-10
reports/
  minute_bar_replay_audit.md    # P2-7
  execution_policy_ab_test.md   # P2-9
  tca_weekly_<date>.md          # P2-10
```

### Test discipline

Phase 2 adds ~40–60 new tests across all items. Maintain zero regressions in the existing 686-test suite. Pay particular attention to:

- Backward compatibility for trade log readers (new JSON column must not break old readers).
- Cache invalidation correctness (the easiest place to introduce a subtle bug).
- Multi-leg order idempotency (compound with the existing client_order_id machinery).

---

## What "Done" Looks Like

When Phase 2 is complete, the answer to *"how do you know your execution matches your backtest?"* changes from:

> "We assume 10 bps round-trip and the backtest uses that constant."

to:

> "Every fill in the last 90 days has been measured for implementation shortfall, decomposed into slippage / timing / fees. Median realised slippage is 12 bps for stocks and 28 bps for crypto, within 2 bps of the calibrated slippage model. The two-leg execution policy is saving 7 bps round-trip median versus single-leg in our A/B test (p=0.02). Gap-Up backtests now use real 1-minute bars, and the Gap-Up live performance over 60 paper days is within 1.4σ of the same-period backtest. Decision-to-submit latency P95 is 180ms for stocks."

That paragraph — measurable, current, and reproducible — is what closes the gap between professional retail and prop-grade execution.

---

*End of Phase 2 plan.*
