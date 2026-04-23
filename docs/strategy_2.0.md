# HawksTrade — Strategy 2.0 Proposals

Status: **proposal / draft**. Nothing here has been A/B backtested against the
current baseline yet. Every item below is expected lift based on code review,
not evidence from live or paper P&L. Before merging any of this into
`config/config.yaml`, each change must be validated as its own backtest run
against a clean baseline.

Audience: this doc is written for the next agent (or future me) to pick up
and execute. Each section has enough context to implement the change without
re-reading the source.

---

## 1. Current state of the book

**Active strategies**
- `momentum` (stocks) — top-N by 5-day return, `profit_trailing` exit
- `ma_crossover` (crypto) — EMA 9/21 with slope + volatility + RSI(35–70) filter
- `range_breakout` (crypto) — prior-day high + 1.8× volume + EMA50 + vol filter

**Disabled strategies**
- `rsi_reversion` (stocks) — over-filtered, rarely fires
- `gap_up` (stocks) — needs tick-level fills the 30-min scheduler can't deliver

**Portfolio-level observation.** All three active strategies are *correlated
long-only trend-following*. When the market is in a sustained uptrend all
three fire; in correction or chop all three sit out. There is:
- no mean-reversion contribution (rsi_reversion disabled)
- no market-neutral / defensive allocation
- crypto/stock correlation rises on risk-off days, so the 3-of-10 crypto cap
  doesn't actually diversify drawdown risk

This is a pro-cyclical book. The 5% daily loss limit protects against a
single-day blowup but won't improve Sharpe over a choppy month.

---

## 2. Strategy-by-strategy weaknesses

### 2.1 Momentum (stocks)

Filters: `min_momentum_pct=0.06`, `top_n=3`, 5-day close-to-close return,
`profit_trailing` exit (6% arm / 4% trail, 4-day min hold, 20-day max).
Market-regime gate: SPY > SMA50.

- **5-day window is short and easily triggered by one-day gaps.** A stock can
  show 6% 5-day momentum purely because day 1 was +6% and days 2–5 were flat.
- **`min_momentum_pct` is not volatility-scaled.** A high-ATR name needs 6%
  to be noise; a low-ATR name needs <3% to be a real signal. Using a fixed
  threshold over-selects on quiet names and under-selects on volatile ones.
- **Top-3 universal cap** means strong trend days still only buy 3, and weak
  days buy 3 noisy picks. Capacity should scale with signal quality.
- **No check that the stock is still trending** (e.g., close > 20-day SMA).
  A name can be up 6% over 5 days but in a clear downtrend above that window.

### 2.2 MA Crossover (crypto)

In reasonably good shape. Has slope filter (20-period EMA slope up), volatility
filter (≥0.5× 10-day avg range), RSI 35–70 band, and crypto regime gate
(BTC > EMA20).

- **9/21 on daily bars lags by 5–7 bars.** By the time the cross confirms,
  a good chunk of the move is already done.
- **RSI 35–70 band filters out the strongest breakouts.** RSI > 70 in crypto
  is often valid momentum, not overbought.
- **`should_exit` fetches bars per-symbol on every risk check.** N API calls
  per cycle. Fine for 6 pairs; becomes expensive as the universe grows.

### 2.3 Range Breakout (crypto)

Buys above prior-day high + 1.8× volume + above EMA50 + volatility confirm.

- **Prior *day* high only** — no consideration of multi-day consolidation.
  A tight 5-day range broken is a much higher-quality setup than a single-day
  high broken in an already-trending market.
- **`hold_days: 3` is very short** for a breakout strategy. The premise of
  buying a breakout is that the new range tends to expand over 5–15 days.

### 2.4 RSI Reversion (stocks, disabled)

Requires RSI < 38 *and* 2-bar recovery *and* 1.5× volume spike *and* price
within 15% of SMA200. That many filters together means very rare signals.
Likely why it's disabled.

### 2.5 Gap Up (stocks, disabled)

Requires first 45 min of market open + 3–15% gap + 2× volume + above SMA200 +
prior day green + true gap. Highly conditional, and the 30-min scheduler
can't deliver the tick-level fills these setups need to be profitable.

---

## 3. Proposed changes — ordered by expected lift vs. effort

### 3.1 Volatility-scale the momentum threshold — HIGH impact, LOW effort

Replace the fixed `min_momentum_pct` with a multiple of 14-day ATR%.

**Config:**

```yaml
strategies:
  momentum:
    min_momentum_atr_mult: 1.5   # signal must be >= 1.5× stock's 14-day ATR%
    # keep min_momentum_pct as a floor (e.g. 0.02) so a comatose stock doesn't
    # qualify just because its ATR is tiny
    min_momentum_pct: 0.02
```

**Implementation sketch** (`strategies/momentum.py`):

```python
atr_pct = compute_atr_pct(bars, period=14)
threshold = max(SCFG["min_momentum_pct"], atr_pct * SCFG["min_momentum_atr_mult"])
if momentum >= threshold:
    ...
```

A stock with 2% daily ATR needs ~3% 5-day move to qualify; one with 4% ATR
needs ~6%. Normalizes signal quality across the universe.

### 3.2 Add a pullback-entry filter to momentum — HIGH impact, LOW effort

Instead of buying the top-3 by raw 5-day return, prefer names strong over
20 days but paused in the last 1–3 days (pullback-to-trend). Rank by:

```
score = return_20d × (1 - abs(return_3d))
```

or equivalently: rank by `return_20d`, then filter out anything in the top
third of `abs(return_3d)`. Either way, you buy less-extended names with
tighter stops and smaller drawdowns.

**Config:**

```yaml
strategies:
  momentum:
    ranking: pullback            # "raw_5d" (current) | "pullback"
    pullback_lookback_days: 20   # ranking window
    pullback_pause_days: 3       # exclude names that just ran hard
```

### 3.3 Convert momentum's max-hold to an equity-curve cap — MEDIUM impact, LOW effort

`max_hold_days: 20` cuts trades on the calendar. Better: cap by *days since
peak gain* in the trade. You ride the longer winners and cut the ones that
topped out early.

**Config:**

```yaml
strategies:
  momentum:
    stale_days_since_peak: 10   # exit if N days elapse with no new peak
    # keep max_hold_days as a hard ceiling (e.g. 40) to avoid forever-holds
    max_hold_days: 40
```

**Implementation note:** requires tracking per-position peak unrealized gain
and the timestamp of that peak. Add to the position record in
`core/order_executor.py` / trade log columns, or compute from historical bars
at each risk-check pass.

### 3.4 Widen the range_breakout window — MEDIUM impact, LOW effort

Replace "prior day high" with "20-day high" (Donchian channel). A 20-day
breakout is a much higher-signal setup than a 1-day one — well-supported in
the trend-following literature. Extend `hold_days` to 10 accordingly.

**Config:**

```yaml
strategies:
  range_breakout:
    breakout_lookback_days: 20   # replace prior-day-high with N-day high
    breakout_pct: 0.005          # smaller excess needed because range is wider
    hold_days: 10                # up from 3
```

### 3.5 Re-enable rsi_reversion as a diversifier — MEDIUM impact, MEDIUM effort

Relax the current over-filtered rules and *only* allow it to fire when
momentum has been flat/negative for the last 5 days. This turns it into a
counter-weight to the momentum book rather than an additive long.

**Config:**

```yaml
strategies:
  rsi_reversion:
    enabled: true
    oversold_threshold: 30          # was 38 — tighter
    overbought_threshold: 55        # exit sooner
    require_sma50_uptrend: true     # price > SMA50 (stronger than "within 15% of SMA200")
    require_recovery_bars: 1        # drop the 2-bar requirement
    only_when_momentum_cold: true   # new gate: only fire when momentum book is flat/red
    cold_lookback_days: 5
    weight: 0.7                     # lower weight than momentum book
```

**Implementation note:** the "only when momentum cold" check requires the
strategy to know the recent performance of the momentum book. Easiest path:
track `momentum_recent_return` in `core/risk_manager.py` from closed trades
over the last 5 trading days; read it in `rsi_reversion.scan()` as a gate.

### 3.6 Regime-aware position sizer — HIGH impact, MEDIUM effort

Replace static 5% sizing with: `signal confidence × inverse volatility ×
regime multiplier`.

```
position_size_pct = max_position_pct
                  × min(confidence, 1.0)
                  × clip(target_vol / stock_vol, 0.5, 1.5)
                  × regime_mult
```

Where `regime_mult` is a continuous function of SPY's position vs SMA200:
- 1.0 if SPY > SMA200 and SMA200 slope is positive
- 0.5 if SPY > SMA200 but SMA200 slope is flat/negative
- 0.25 if SPY < SMA200 and SMA50 slope is positive (recovery)
- 0 if SPY < SMA200 and SMA50 slope is negative

The current SPY > SMA50 gate is binary; making it continuous lets us stay
(smaller) in weakened uptrends rather than being fully in or out.

**Implementation:** add `core/position_sizer.py`, called from
`core/order_executor.py::enter_position()`. `target_vol` and `stock_vol` come
from 20-day close-to-close stdev or ATR%. Move `max_position_pct` from a
hard number to a ceiling, with the actual size computed per-trade.

### 3.7 New strategy: Relative-strength momentum (RS vs SPY) — HIGH impact, MEDIUM effort

Rank stocks by `20-day return - SPY 20-day return`. Buy top-5 where RS > 0
*and* absolute return > 0. Pairs with the existing absolute-momentum strategy
because on a day when SPY is +3% and AAPL is +3%, raw momentum picks AAPL
but RS says "AAPL is just beta, pass." In a choppy market, RS finds the
handful of names leading regardless of direction.

This is the one academic momentum anomaly that has held up out-of-sample
across decades (Jegadeesh & Titman 1993; many replications since).

**Config:**

```yaml
strategies:
  rs_momentum:
    enabled: true
    asset_class: stocks
    lookback_days: 20
    benchmark: SPY
    top_n: 5
    min_rs_pct: 0.02            # must beat SPY by 2%+ over lookback
    min_abs_return_pct: 0.0     # avoid "least-bad" picks in a down market
    hold_days: 10
    exit_policy: profit_trailing
    profit_floor_pct: 0.0
    trail_activation_pct: 0.05
    trailing_stop_pct: 0.04
    max_hold_days: 30
    weight: 1.0
```

**Implementation sketch** (new file `strategies/rs_momentum.py`):

```python
# Fetch universe bars + benchmark bars in one call (already batched in alpaca_client)
bench_ret = pct_return(spy_bars, lookback_days)
for sym, bars in universe_bars.items():
    abs_ret = pct_return(bars, lookback_days)
    rs = abs_ret - bench_ret
    if rs >= SCFG["min_rs_pct"] and abs_ret >= SCFG["min_abs_return_pct"]:
        scores.append({"symbol": sym, "rs": rs, "abs_ret": abs_ret, ...})
scores.sort(key=lambda x: x["rs"], reverse=True)
signals = scores[:SCFG["top_n"]]
```

Add position-overlap dedupe with `momentum` in `core/order_executor.py` so
the same name doesn't get double-bought by both strategies.

### 3.8 Batch `ma_crossover` exit bar-fetches — LOW impact, LOW effort

On every 15-min risk check, `ma_crossover.should_exit()` fetches bars one
symbol at a time (`ac.get_crypto_bars([symbol], ...)` per call). Batch them:

```python
# In run_risk_check.py, fetch all open-position crypto bars once:
open_crypto_syms = [p.symbol for p in positions if is_crypto(p)]
crypto_bars = ac.get_crypto_bars(open_crypto_syms, timeframe=tf, limit=slow_span + 10)
# Pass into should_exit() as a kwarg.
```

Minor performance win now, important as the crypto universe grows. Avoids
rate-limit issues and reduces the window between check time and action time.

---

## 4. What I would NOT change

- **Daily loss limit (5%) and stop-loss (3.5%).** These are fine.
- **`profit_trailing` exit policy for momentum.** The design is sound.
- **`max_crypto_positions` cap.** Let it run before tuning.
- **Disabling `gap_up`.** Opening-gap strategies need tick-level fills. Don't
  re-enable unless the scheduler gets a 9:30:01 cron *and* orders move to
  `market` type with aggressive slippage tolerance.

---

## 5. Risk & honest caveats

- **Every "high impact" label above is a judgment call**, not a measurement.
  It's based on code review, not evidence from your data. The priority order
  will change once we see backtest results.
- **Filters trade frequency for quality.** #1, #2, #4, and #5 all add
  filtering layers. That's usually the right trade in live markets, but it
  means fewer trades, which means more variance in short-term P&L even if
  expected return goes up. Budget for longer validation periods.
- **No free lunch.** If a proposal seems strictly dominant against the
  current config across all backtest windows, treat it as suspicious and
  re-check for look-ahead bias in the implementation.

---

## 6. Validation protocol (before any item ships to live)

Per `CLAUDE.md`, every code change must pass unit tests + a 1-month backtest
before committing. For strategy-level changes, extend that to a full A/B
comparison:

1. **Baseline snapshot.** Run `python3 scheduler/run_backtest.py --days 180
   --fund 10000` on unmodified `config/config.yaml` and save the output to
   `backtests/baseline_<DATE>.txt`.

2. **Per-proposal runs.** For each item in §3 (one at a time, not in
   combination), use `--set` overrides to apply *only* that change:

   ```bash
   # Example: proposal 3.1
   python3 scheduler/run_backtest.py --days 180 --fund 10000 \
     --set strategies.momentum.min_momentum_atr_mult=1.5 \
     --set strategies.momentum.min_momentum_pct=0.02 \
     --strategies momentum \
     > backtests/prop_3_1_<DATE>.txt
   ```

3. **Compare.** For each proposal, measure vs baseline:
   - Total return
   - Max drawdown
   - Sharpe (simple: mean daily return / stdev)
   - Trade count (to spot "looks great but fires 3× a year" cases)
   - Win rate
   - Worst losing trade

4. **Ship criteria.** A proposal only merges to `config.yaml` if:
   - Sharpe ≥ baseline
   - Max drawdown ≤ baseline × 1.1
   - Trade count within [0.5×, 1.5×] of baseline (neither too sparse nor a
     signal-frequency regression)
   - Improvement holds across at least two disjoint 90-day windows (guard
     against overfitting to one regime)

5. **Combined run.** After individual wins are confirmed, run all accepted
   proposals together once. Expect some interaction loss — if the combined
   result is worse than the best individual run, drop the lowest-lift item
   and retry.

6. **Paper-trade soak.** Ship accepted changes to paper mode for 14 calendar
   days minimum before any consideration of live.

---

## 7. Suggested implementation order

If we're picking one thing to do first, do **§3.1 (ATR-scaled threshold)** —
smallest code change, biggest expected lift on momentum quality, no new
dependencies.

Then, in order:
1. §3.1 — ATR-scaled momentum threshold
2. §3.4 — 20-day Donchian for range_breakout
3. §3.2 — Pullback ranking for momentum
4. §3.8 — Batch exit bar-fetches (pure refactor, independent of the above)
5. §3.3 — Stale-days exit for momentum (requires trade-log schema update)
6. §3.7 — New RS momentum strategy (new file + dedupe wiring)
7. §3.6 — Regime-aware position sizer (touches core/order_executor.py)
8. §3.5 — rsi_reversion revival (needs the "momentum cold" gate wired first)

Items 1–4 are independent and can land in any order. Items 5–8 depend on
earlier items or on core plumbing changes — sequence them as above.

---

## 8. Out of scope for 2.0

- Short positions. HawksTrade is long-only by design; keeping it that way.
- Options overlay (covered calls, protective puts). Big operational lift for
  marginal risk improvement at this portfolio size.
- Multi-timeframe ensemble (combining daily + 4-hour signals). Interesting,
  but the current scheduler is daily-oriented; revisit after 2.0 lands.
- Machine-learned signals (gradient-boosted trees, etc.). Not until the rule-
  based baseline is stable and we have ≥12 months of live trade history to
  train on.
