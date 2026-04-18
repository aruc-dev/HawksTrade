# HawksTrade Reliability TODOs

This backlog converts the reliability review findings into concrete work items.
Use beads (`bd`) as the source of truth when starting implementation work; this
file is a readable reliability roadmap.

## P0 - Trading Safety

- [x] Fix `get_position()` error handling.
  - Problem: `core/alpaca_client.py` currently swallows all exceptions while trying symbol variants and returns `None`. Auth failures, rate limits, timeouts, or server errors can look like "no position".
  - Action: Only suppress true "position not found" responses. Propagate unauthorized, retryable network failures, rate limits, and server errors.
  - Done when: Exit logic cannot silently skip a real broker position because Alpaca returned a non-not-found error.

- [x] Add a single-process execution lock for trade-mutating jobs.
  - Problem: Full scans, crypto scans, and risk checks can overlap under cron.
  - Action: Add a Linux runner wrapper using `flock`, and update cron templates to run scans/risk checks through it.
  - Done when: At most one scan/risk process can place or close orders at a time.

- [x] Protect `data/trades.csv` with file locking.
  - Problem: Trade-log appends and rewrites are not guarded against concurrent writes.
  - Action: Add `fcntl.flock` or an equivalent lock around all trade-log reads/writes.
  - Done when: Concurrent scan/risk/report processes cannot corrupt or lose trade-log updates.

- [x] Make entry order logging fill-aware.
  - Problem: Buy limit orders are logged as `open` immediately after submission, even if Alpaca reports `new`, `accepted`, or partial fill.
  - Action: Track order status as `submitted`, `partially_filled`, or `open` only after confirmed fill.
  - Done when: Local open positions in `trades.csv` reflect broker fills rather than order submission.

- [x] Fail closed when pending exit orders cannot be checked.
  - Problem: If open-order lookup fails, exit logic continues and can submit duplicate sell orders.
  - Action: Change pending-exit lookup failure to block a new exit order and mark the run unhealthy.
  - Done when: A transient Alpaca failure cannot trigger duplicate exits for the same symbol.

- [x] Add broker order idempotency.
  - Problem: Orders are submitted without stable `client_order_id`, so crash/retry paths cannot identify prior submissions.
  - Action: Generate deterministic client order IDs from run id, symbol, side, strategy, and intent timestamp; persist intent before submit.
  - Done when: Retrying a run cannot create duplicate broker orders for the same trading intent.

## P1 - Runtime Resilience

- [x] Make `/dev/shm` secrets fail closed on EC2.
  - Problem: When `secrets_source: shm` is configured, missing `/dev/shm/.hawkstrade.env` can fall back to local dotenv files.
  - Action: Add a deployment guard such as `HAWKSTRADE_REQUIRE_SHM=1` that refuses local fallback on cloud.
  - Done when: EC2 fails clearly if RAM secrets are missing, stale, or unreadable.

- [x] Replace direct cron commands with a runtime wrapper.
  - Problem: Cron uses `python3` directly and does not verify `.venv`, secrets, locks, or Alpaca connectivity first.
  - Action: Add `scripts/run_hawkstrade_job.sh` that activates `.venv`, checks secrets, checks connectivity, takes the lock, runs the job, and logs exit code.
  - Done when: All Linux cron entries use the wrapper and report clear preflight failures.

- [x] Use an explicit trading-session date for the daily loss baseline.
  - Problem: `date.today()` uses host timezone, which is UTC on EC2 and can reset the loss baseline outside the intended ET session.
  - Action: Calculate the baseline date using a configured timezone or `America/New_York` market-session boundary.
  - Done when: Daily loss limits reset at the intended trading session boundary, not at arbitrary host midnight.

- [ ] Add shared Alpaca retry and error classification.
  - Problem: Alpaca calls are direct and exceptions are handled inconsistently.
  - Action: Add a helper that retries timeouts, 429, and 5xx with bounded backoff; never retries 401/403; returns structured error categories.
  - Done when: Entry points can distinguish retryable outage, auth failure, broker rejection, and not-found.

- [ ] Escalate repeated price-fetch failures.
  - Problem: Risk checks skip a position when latest price fetch fails, which can leave stop-loss enforcement blind.
  - Action: Track consecutive price failures per symbol and surface `[NOK]` in health after a threshold.
  - Done when: Repeated inability to price an open position becomes an operational alert.

## P2 - Observability And Operations

- [ ] Fix health-check old traceback false positives.
  - Problem: Raw traceback lines are stored with `timestamp=None`, so old errors can bypass the lookback filter.
  - Action: Carry forward the previous formatted timestamp to traceback lines, or ignore timestamp-less findings when `--hours` is active.
  - Done when: Deleting old logs is not required to get an accurate current-window health report.

- [ ] Run trade-log reconciliation automatically.
  - Problem: `scheduler/reconcile_trade_log.py` exists, but reconciliation is manual.
  - Action: Run reconciliation after scans/risk checks and before health/report generation when Alpaca is reachable.
  - Done when: `trades.csv` stays aligned with broker positions without manual intervention.

- [ ] Add alerting for unhealthy operational states.
  - Problem: Health output is available only when manually checked.
  - Action: Send alerts for Alpaca auth failures, missed cron runs, repeated price failures, pending unfilled exits, and `[NOK]` health status.
  - Done when: A critical failure creates a visible notification without waiting for manual log review.

- [ ] Add systemd units/timers for production Linux deployment.
  - Problem: Cron cannot express dependencies on secret loading, network readiness, lock handling, or restart policy well.
  - Action: Provide systemd service/timer templates for scans, risk checks, reports, health checks, and secret loading.
  - Done when: EC2 deployment can run through systemd with `After=network-online.target`, secret dependency checks, and journal logs.

- [ ] Persist health snapshots.
  - Problem: Health reports are overwritten, making trend/debug history harder.
  - Action: Save timestamped health JSON/HTML snapshots and keep a short retention window.
  - Done when: Recent health history can be inspected without preserving all runtime logs.
