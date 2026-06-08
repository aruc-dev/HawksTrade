# HTEC2P Protection Lock Opportunity-Cost Review

Date: 2026-06-08
Bead: HawksTrade-610g
Instance: HTEC2P, HawksTrade paper

## Question

HTEC2P showed many momentum and relative-strength entry signals blocked by
`rolling_max_drawdown_lock` after a recent rolling drawdown of `-7.43%`. The
question is whether the global lock cooldown is too conservative and is leaving
meaningful profit on the table.

No risk parameters were changed during this review.

## Current Setting

The active committed config uses:

- `protections.max_drawdown_lookback_days: 20.0`
- `protections.max_drawdown_pct: 0.05`
- `protections.max_drawdown_cooldown_days: 5.0`

The lock is global and entry-only. It blocks new entries after the recent
closed-trade equity path breaches the configured rolling drawdown threshold;
exits continue.

## Forward Paper Evidence

I parsed HTEC2P scan logs for `2026-06-01` through `2026-06-05`, focusing only
on entries blocked by `rolling_max_drawdown_lock`.

Summary:

| Measure | Value |
|---|---:|
| Raw rolling-drawdown blocked events | 69 |
| Unique symbol/strategy candidates | 12 |
| Unique symbol/strategy/day candidates | 22 |
| Momentum blocked events | 50 |
| Relative-strength blocked events | 19 |

Top blocked symbols by raw event count:

| Symbol | Blocked events |
|---|---:|
| MRVL | 18 |
| ARM | 11 |
| XLK | 11 |
| FCX | 10 |
| IGV | 5 |
| ORCL | 4 |
| AAPL | 3 |
| CSCO | 2 |
| LRCX | 2 |

Forward proxy method:

- For each first unique symbol/strategy block, use the first available Alpaca
  IEX 1-minute bar at or after the blocked signal timestamp as the proxy entry.
- Use the last available 1-minute close on `2026-06-05` as the proxy mark.
- Also compute max favorable and max adverse move over that interval.
- This is not a fill simulation, not capacity-aware, and does not replay the
  full exit policy. It is a directional opportunity-cost screen.

Forward proxy result:

| Candidate Set | Count | Wins | Losses | Avg Return | Median Return | Naive Portfolio Impact at 2% Cap |
|---|---:|---:|---:|---:|---:|---:|
| First symbol/strategy | 12 | 1 | 11 | -8.61% | -7.64% | -2.07% |
| First symbol/strategy/day | 22 | 1 | 21 | -8.41% | -8.16% | -3.70% |

The only positive first symbol/strategy proxy was `CSCO` relative strength,
which was up `+1.53%` by the June 5 mark. The largest negative first
symbol/strategy proxies were `MU` momentum (`-16.31%`), `ARM` momentum
(`-16.08%`), `MRVL` momentum (`-14.81%`), and `ORCL` momentum (`-13.64%`).

Interpretation: in this actual paper-forward episode, the global drawdown lock
appears to have avoided materially negative follow-through. The blocked signals
were not an obvious missed-profit cluster.

## Focused Backtest Evidence

I ran a focused pre-OOS comparison ending `2025-05-31` with 30 calendar days,
dynamic screener enabled, 10 bps slippage, 5 bps fees, and no minimum fee:

1. Default config.
2. `--set protections.max_drawdown_cooldown_days=0`.
3. `--set protections.max_drawdown_cooldown_days=2`.

All three reports were byte-identical:

| Variant | Return | Trades | Win Rate | Max Drawdown | Profit Factor |
|---|---:|---:|---:|---:|---:|
| Default | +1.90% | 4 | 75.0% | -0.74% | 6.43 |
| No global drawdown lock | +1.90% | 4 | 75.0% | -0.74% | 6.43 |
| 2-day global cooldown | +1.90% | 4 | 75.0% | -0.74% | 6.43 |

This focused backtest is neutral because the rolling drawdown lock did not
change any decisions in that narrow window. It is useful only as a sanity check
that the override path works and did not reveal an immediate contradiction.

I started broader 120-day and 365-day pre-OOS comparisons, but the existing
backtest runner was too slow/noisy for an interactive operational review because
it rebuilt missing minute-bar cache and evaluated every strategy day-by-day. I
did not use those interrupted runs as evidence.

## Recommendation

Do not loosen the global rolling drawdown lock from this review alone.

The direct HTEC2P forward evidence from June 1-5 says the current lock likely
protected the account rather than suppressing profitable trades. The focused
backtest did not exercise the lock, so it does not justify a change either.

Before changing `max_drawdown_pct` or `max_drawdown_cooldown_days`, build or run
a dedicated longer A/B validation that:

- Replays multiple pre-OOS windows with identical data and only the global
  drawdown-lock setting changed.
- Records number of global-lock blocks, trades admitted by the relaxed variant,
  return, drawdown, profit factor, and bootstrap bounds.
- Runs the strategy validation ladder before any deployment change.
- Keeps the active OOS lockup excluded unless doing a single approved final OOS
  validation.

For now, the best profitability action is not to relax this lock. The better
follow-up is a faster, explicit protection-lock A/B runner so this can be
evaluated across enough regimes without manual log parsing.
