# HawksTrade Enhancement Implementation Plan

This document captures the recommended implementation plan from the repo research across Freqtrade, vn.py, QuantConnect Lean, and LumiBot. The order is intentional: add validation first, then safety controls, then architecture changes, then research tooling.

Active task tracking lives in beads (`bd`). Do not use this document as a task list.

## Execution Order

1. Backtest realism and acceptance replay
2. Central pre-trade order governor
3. Protection manager and lockouts
4. Signal to Target to Risk to Execution pipeline
5. Offline factor research harness

## Phase 0: Baseline

- Create one beads issue per phase before implementation.
- Record the current validation baseline before changing behavior:
  - `python3 -m unittest discover -v`
  - `python3 scheduler/run_backtest.py --days 30 --fund 10000`
  - `python3 scheduler/run_backtest.py --days 365 --fund 10000 --output /tmp/hawkstrade_baseline.md`
- Save baseline stats for comparison:
  - final value
  - total trades
  - win rate
  - max drawdown
  - profit factor
  - per-strategy P/L

## Phase 1: Backtest Realism And Acceptance Replay

Primary files:

- `scheduler/run_backtest.py`
- `scripts/validate_backtest_realism.py`
- `tests/test_backtest_realism.py`
- `data/backtest_acceptance_baselines.json`

Implementation steps:

1. Add a validation script that runs a deterministic acceptance suite and exits non-zero on failure.
2. Add market-data quality checks:
   - missing bars
   - zero or negative OHLCV
   - `high < low`
   - open/close outside high/low
   - stale prices
3. Add lookahead checks:
   - run strategies with full history
   - run the same decision dates with walk-forward truncated history
   - compare emitted signals for the same dates
4. Add warmup sensitivity checks:
   - compare indicators and signals across different lookback lengths
   - fail if materially different warmup lengths change active signals unexpectedly
5. Replace or isolate Gap-Up's synthetic minute-bar behavior:
   - prefer real Alpaca 1-minute opening bars for replay windows
   - if minute data is unavailable, mark Gap-Up acceptance as not validated instead of pretending daily data is sufficient
6. Add paper/live replay mode:
   - replay a date range from `data/trades.csv`
   - compare simulated fills and realized P/L against broker-tracked results within configured tolerances

Acceptance criteria:

- `python3 scripts/validate_backtest_realism.py --days 30` passes.
- Unit tests cover data gaps, lookahead detection, warmup variance, and Gap-Up minute-data requirements.
- Backtest reports clearly state when a sleeve uses daily approximation versus real intraday replay.

## Phase 2: Central Pre-Trade Order Governor

Primary files:

- `core/order_governor.py`
- `core/order_executor.py`
- `scripts/manual_market_sell.py`
- `tests/test_order_governor.py`

Implementation steps:

1. Create `OrderGovernor.evaluate(order_intent, account_state, broker_orders)` returning allow/block/warn plus reason.
2. Add entry checks:
   - max active orders
   - duplicate symbol/side pending orders
   - order rate per rolling window
   - daily order count
   - max notional
   - invalid quantity
   - missing account state
3. Add exit-specific checks:
   - do not block risk-reducing sells for entry-only limits
   - block duplicate pending exits
   - block oversell quantity
   - fail closed on broker/account lookup failure
4. Hook the governor into:
   - `enter_position`
   - `exit_position`
   - manual market/limit sell script
5. Persist lightweight governor state in `data/order_governor_state.json` only if needed for rolling counters.
6. Log every block reason with enough context for health checks.

Acceptance criteria:

- Existing behavior is preserved with default config.
- Entry blocks are fail-closed.
- Exit and risk-reduction orders are not blocked by entry-only limits.
- Unit tests cover market entry, limit entry, market exit, limit exit, duplicate pending orders, and governor failure.

## Phase 3: Protection Manager And Lockouts

Primary files:

- `core/protection_manager.py`
- `scheduler/run_scan.py`
- `scheduler/run_backtest.py`
- `tracking/trade_log.py`
- `tests/test_protection_manager.py`

Implementation steps:

1. Add lock types:
   - symbol cooldown after exit
   - symbol stoploss guard
   - strategy stoploss guard
   - low-profit strategy lock
   - rolling max-drawdown lock
2. Store active locks in `data/protection_locks.json` with:
   - expiry
   - scope
   - trigger
   - reason
3. Evaluate protections before new entries in `run_scan.py`.
4. Integrate protections into `run_backtest.py` so backtests include lockout behavior.
5. Add visibility in scan logs, reports, and health output for active locks.
6. Keep protections entry-only; they must never block exits.

Acceptance criteria:

- A recent stop-loss can lock a symbol or strategy.
- Locks expire deterministically.
- Backtest and live/paper scan paths use the same protection logic.
- Disabled protections preserve current backtest baseline.

## Phase 4: Signal To Target To Risk To Execution Pipeline

Primary files:

- `core/trading_models.py`
- `core/portfolio_construction.py`
- `core/risk_pipeline.py`
- `scheduler/run_scan.py`
- `core/order_executor.py`
- strategy tests

Implementation steps:

1. Define shared dataclasses:
   - `Signal`
   - `PortfolioTarget`
   - `RiskDecision`
   - `OrderPlan`
   - `ExecutionResult`
2. Build adapters that convert existing strategy dict signals into `Signal` objects before changing strategy internals.
3. Move sizing into `portfolio_construction.py`, preserving:
   - ATR-risk sizing
   - 1% risk per trade
   - 8% max-position cap
4. Move risk filtering into `risk_pipeline.py`:
   - daily loss
   - max positions
   - planned positions
   - sector caps
   - protections
   - regime state
5. Keep `order_executor.py` as the broker execution boundary.
6. Migrate `run_scan.py` to:
   - collect signals
   - build targets
   - apply risk
   - execute order plans
7. Add equivalence tests comparing old and new decisions on fixed fixtures before enabling the new path by default.

Acceptance criteria:

- With protections disabled, backtest output is materially unchanged except for intentional logging/model-shape changes.
- Strategy modules become less coupled to execution details.
- New strategies can be added by emitting `Signal` without touching order execution.

## Phase 5: Offline Factor Research Harness

Primary files:

- `scripts/research_factors.py`
- `reports/factor_research/`
- `tests/test_factor_research.py`

Implementation steps:

1. Build a factor dataset from Alpaca OHLCV using current HawksTrade universe logic.
2. Start with existing features only:
   - 5-day return
   - volume ratio
   - RSI
   - Bollinger %B
   - ATR%
   - SMA50/SMA200 distance
   - gap %
   - breadth regime
   - sector
3. Compute forward returns at:
   - 1 trading day
   - 2 trading days
   - 5 trading days
   - 10 trading days
   - 20 trading days
4. Report:
   - information coefficient
   - quantile returns
   - hit rate
   - drawdown
   - coverage
   - turnover
   - train/validation/test splits
5. Output both machine-readable and human-readable artifacts:
   - JSON
   - CSV
   - Markdown summary
6. Add a rule that no live strategy default changes are made from this harness unless:
   - out-of-sample results improve
   - the Phase 1 acceptance replay passes
   - the strategy change is reviewed in a separate PR

Acceptance criteria:

- Research script runs without placing orders.
- Results are reproducible for a fixed date range.
- Missing data is reported, not silently filled.
- Strategy changes remain separate from research evidence.
