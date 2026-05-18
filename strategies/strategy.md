# HawksTrade — Strategy Reference

> **Maintenance rule:** Update this file whenever a strategy's entry conditions, exit
> conditions, filters, parameters, or enabled state change. This is mandatory — see
> section 16 of `CLAUDE.md`.

---

## Overview

| Strategy | Asset | Status | File |
|---|---|---|---|
| Momentum | Stocks | **Enabled** | `momentum.py` |
| Relative Strength | Stocks | **Enabled** | `relative_strength.py` |
| RSI Reversion | Stocks | **Enabled as conditional bear/chop sleeve** | `rsi_reversion.py`; live entries require paper-readiness evidence |
| Gap-Up | Stocks | **Disabled in default profile** | `gap_up.py`; run standalone validation before re-enabling |
| MA Crossover | Crypto | **Enabled** | `ma_crossover.py`; live entries require paper-readiness evidence |
| Range Breakout | Crypto | **Enabled** | `range_breakout.py`; high-water profit protection enabled; live entries require paper-readiness evidence |

All strategies share a common global risk layer (8% max position size,
3.5% stop-loss, 12% take-profit, max 10 open positions, 5% daily-loss halt)
enforced by `core/risk_manager.py` and `scheduler/run_risk_check.py`.
Individual strategies may override the stop via
`atr_stop_price` on their signals; the executor writes that value to the trade log
when it is wider than the global stop.

---

## 1. Momentum *(Stocks — Enabled, Adaptive v2.1)*

**Type:** Trend-following, swing trade.

**Entry:** Ranks every stock in the scan universe by its 5-day smoothed alpha
momentum. Buys up to the top two candidates that have gained at least 4%,
subject to four layers of adaptive
filtering:

1. **Phase 1 — ATR Stop + 1% Risk Sizing**: Each signal carries an ATR-based stop
   (`entry - 1.2 × ATR_14`, capped at 5% below entry). Position size is computed as `(equity × 1%) / (entry - atr_stop)`
   so that every trade risks exactly 1% of capital, capped at 8% per-position max.
2. **Phase 2 — Sector-Neutral Ranking**: Enforces `max_positions_per_sector: 1`
   using a static GICS sector map across both existing/pending positions and new
   candidates. If the top two ranked stocks share a capped sector, the lower-ranked
   one is skipped in favour of the next stock in a different sector.
3. **Breadth Coverage Guard**: Requires at least 65% of the scan universe to have
   enough valid bars for SMA50 breadth computation. If coverage is lower, Momentum
   fails closed and opens no new positions rather than trading from a partial market sample.
4. **Market Breadth Tiered Regime Guard**: Computes what fraction of
   the scan universe trades above its own SMA50 (`rm.market_breadth_pct`):
   - **Red** (SPY < SMA50 OR breadth < 25%): no new entries.
   - **Yellow** (breadth 25–50%): reduced deployment, up to `yellow_max_positions: 1`.
   - **Green** (breadth ≥ 50%): full `top_n: 2` deployment.

**Volume Confirmation (per-signal):** Momentum uses time-of-day normalized volume
pace by default (`volume_confirmation_mode: pace`). Current regular-session volume
must be at least `1.8x` the expected volume for the elapsed market minutes
(`volume_pace_ratio: 1.8`), based on the candidate's 20-day average daily volume.
If intraday bars are unavailable, the scan time is outside regular-session
context, or no valid elapsed-session volume can be calculated, it falls back to
the legacy daily-volume check
(`volume_spike_ratio: 2.0`). The screener provides a separate 20-day ADV baseline
at universe construction time; this check adds a per-signal guard at scan time.

**Optional Alpha Gate:** `min_alpha_pct` can require a candidate's 5-day return to
exceed SPY's 5-day return by a configured amount. It is set to `0.0` in the validated
default because the stricter alpha gate reduced the latest 12-month result.

**Exit:** Three-layer policy:
- A trailing stop can activate before the minimum hold once peak gain reaches
  6%; price must then not fall more than 4% from that peak.
- After the minimum 4-day hold, flat or losing trades exit immediately.
- A hard 20-day cap closes any position that never pulled back to trigger the trail.

**Stop:** The ATR-based stop is written to the trade log as `stop_loss` and used by
the live risk check as the custom stop input, giving volatile stocks more breathing
room while the global 3.5% stop remains the baseline fixed-percentage stop.

**Key parameters (`config/config.yaml`):**

| Parameter | Value |
|---|---|
| `top_n` | 2 |
| `min_momentum_pct` | 4% (5-day smoothed return) |
| `min_alpha_pct` | 0% excess return over SPY (disabled) |
| `hold_days` (minimum) | 4 business days |
| `max_hold_days` | 20 business days |
| `trail_activation_pct` | 6% peak gain |
| `trailing_stop_pct` | 4% from peak |
| `exit_policy` | `profit_trailing` |
| `atr_period` | 14 |
| `atr_multiplier` | 1.2 ATR stop extension |
| `risk_per_trade_pct` | 1% of equity |
| `max_positions_per_sector` | 1 |
| `breadth_green_threshold` | 50% |
| `breadth_red_threshold` | 25% |
| `min_breadth_coverage_pct` | 65% |
| `yellow_max_positions` | 1 |
| `volume_confirmation_mode` | `pace` |
| `volume_pace_ratio` | 1.8x expected elapsed-session volume pace |
| `volume_pace_timeframe` | `1Min` |
| `session_minutes` | 390 |
| `volume_spike_ratio` | 2.0 legacy daily fallback |

**Regime filters:**
- SPY > SMA50 (hard requirement; Red if fails).
- Market breadth ≥ 25% of universe above SMA50 (Red if fails).
- Valid breadth inputs for at least 65% of the scan universe.

---

## 2. Relative Strength *(Stocks — Enabled)*

**Type:** Medium-term benchmark-relative momentum, swing trade.

**Entry:** Ranks stock candidates by 20-day return minus SPY's 20-day return.
The default profile buys up to one sector-diversified candidate only when all of
these conditions are true:

1. Candidate 20-day excess return versus SPY is at least 5%.
2. Candidate absolute 20-day return is at least 8%.
3. Candidate 3-day return is no more than 3%, avoiding fresh short-term blow-offs.
4. Price is above the 50-day SMA and no more than 30% above it.
5. Stock regime is not red: SPY/QQQ regime passes and market breadth is at least 25%.
6. Breadth coverage is at least 65% of the scan universe.
7. Volume pace is at least 1.8x expected elapsed-session volume pace, with a 1.8x
   daily-volume fallback when intraday bars are unavailable.

**Stop and sizing:** Signals include a 1.2x ATR(14) stop capped at 5% below
entry and ATR-risk quantity targeting 1% account risk before the global 8%
max-position cap.

**Exit:** The standard risk manager handles global stop-loss, take-profit, and
custom ATR stops. The scan hold-exit pass enforces a 7-business-day hold cap;
pre-hold high-water profit protection can still exit once peak gain reaches 6%
and price falls 4% from that peak.

**Key parameters:**

| Parameter | Value |
|---|---|
| `top_n` | 1 |
| `lookback_days` | 20 |
| `benchmark_symbol` | SPY |
| `min_rs_pct` | 5% excess return over SPY |
| `min_abs_return_pct` | 8% absolute return |
| `recent_lookback_days` | 3 |
| `max_recent_return_pct` | 3% |
| `hold_days` | 7 business days |
| `atr_period` | 14 |
| `atr_multiplier` | 1.2 ATR stop extension |
| `max_stop_loss_pct` | 5% |
| `max_positions_per_sector` | 1 |
| `volume_confirmation_mode` | `pace` |
| `volume_pace_ratio` | 1.8x expected elapsed-session volume pace |

**Validation note:** This sleeve improved the default 12-month production gate
point estimate and lower-bound return, but production validation remains
blocking until the 12-month default lower bound and crypto lower-bound gates
clear.

---

## 3. RSI Reversion *(Stocks — Enabled, conditional bear/chop sleeve)*

**Type:** Mean reversion, swing trade.

**Entry:** Seven conditions must all be true simultaneously:
1. RSI(14) < 40 — oversold/recovering with enough signal frequency for the sleeve.
2. Bollinger Band %B < 20% — price in the lower quintile of the 20-day, 2σ band.
3. Volume ≥ 0.7× 20-day average — sufficient liquidity confirmation.
4. Last close > prior close — 1-bar recovery; freefall has paused.
5. SMA200 Band: Price must be within configurable buffers of the 200-day MA.
   - Entry blocked if `price < SMA200 × (1 - sma200_lower_buffer_pct)` (broken stocks).
   - Entry blocked if `price > SMA200 × (1 + sma200_upper_buffer_pct)` (overextended stocks).
   - Default buffers: ±15%.
6. Recent 5-day drawdown ≤ 10% — avoids entries still buried in unresolved waterfall moves.
7. ATR/price ≤ 5% — blocks legacy or high-volatility names where mean reversion
   tail risk is too wide for the sleeve.

**Stop:** The 0.8 × ATR(14) stop extension flows through
`order_executor.enter_position` into the trade log and is picked up by
`run_risk_check` in both backtest and live/paper modes. It is capped at 6%
below entry. The global 3.5% stop governs when the ATR stop is tighter or
absent; otherwise the ATR stop can widen the trade's breathing room.

**Exit:** Whichever fires first:
- Price ≥ SMA(`bb_period`) — mean-reversion target reached (default: SMA20).
- RSI(14) > `overbought_threshold` — momentum neutral; edge evaporated (default: 50).
- Latest daily close is at least 6% below entry (`max_loss_exit_pct`) — strategy
  fail-safe for tail loss control.
- High-water profit protection once peak gain reaches `trail_activation_pct`.
- 10-business-day hard cap (`hold_days`).

**Key parameters:**

| Parameter | Value |
|---|---|
| `rsi_period` | 14 |
| `market_regime_mode` | `bear_or_chop_only` |
| `oversold_threshold` | 40 |
| `overbought_threshold` | 50 (RSI neutral exit) |
| `hold_days` | 10 business days |
| `bb_period` | 20 |
| `bb_std` | 2.0 |
| `atr_period` | 14 |
| `atr_multiplier` | 0.8 ATR stop extension |
| `max_entry_atr_pct` | 5% ATR/price ceiling |
| `max_stop_loss_pct` | 6% RSI ATR stop cap |
| `max_loss_exit_pct` | 6% below entry on latest daily close |
| `profit_trailing_enabled` | true |
| `trail_activation_pct` | 6% |
| `trailing_stop_pct` | 4% from post-entry peak |
| `vix_multiplier` | 0.95 |
| `sma200_lower_buffer_pct` | 15% |
| `sma200_upper_buffer_pct` | 15% |
| `volume_spike_ratio` | 0.7 |
| `recent_drawdown_lookback_days` | 5 |
| `max_recent_drawdown_pct` | 10% |

**Regime filters:**
- Bear/chop mode: stand down when the stock market regime filter is bullish
  (SPY or QQQ above SMA50); only scan when that regime check is not bullish.
- Crash filter: skip if SPY is >20% below its 252-day peak.
- VIX proxy: skip if SPY realised HV(20) > 200-day HV MA × `vix_multiplier` (default: 0.95).

**Monitoring gate:** RSI is included in the default validation and walk-forward
profiles only through its conditional bear/chop regime filter. Continue running
`python3 scheduler/run_validation_gate.py --profile rsi` before scaling its
capital allocation. The gate requires cost-aware backtest performance plus at
least 60 paper-trading days, 20 closed RSI trades, 48% win rate, 1.15 profit
factor, +2% aggregate paper return, and max drawdown no worse than 4%.

**Live readiness:** If this sleeve is enabled in live mode, runtime entries are
blocked until the trade log shows at least 20 closed paper exits and 60 calendar
days of RSI paper validation. Paper mode remains available for evidence
collection.

---

## 4. Gap-Up *(Stocks — Disabled in Default Profile)*

**Type:** Opening momentum, short swing trade.

**Entry:** All of the following must be true at market open (within first 45 minutes):
1. Today's open is 5–15% above the prior close (gap bounded to avoid exhaustion gaps).
2. Today's open is above the prior day's high (`require_true_gap: true`).
3. Opening minute-bar volume pace is at least 1.3× the 20-day average daily pace.
4. At least 65% of the scan universe is above SMA50 — broad participation guard.
5. Prior completed close > SMA200 and today's open > SMA200 — avoids buying a gap
   that is only jumping into long-term resistance.
6. Today's open is no more than 35% above SMA200 — avoids exhausted gaps far above trend.
7. Prior day closed green (close > open) — pre-gap momentum confirmation.
8. The latest opening-window price has not faded more than 0.5% below the session open
   and is not already more than 3% above the session open.
9. Entry within 45 minutes of the 9:30 ET open.

Completed daily bars are used for SMA200, ATR, prior-day OHLC, and average
volume. Current-session minute bars are used for the live opening gap and volume
pace, so the strategy does not trade from the current day's completed daily
volume or close. Signals are ranked by confidence and capped at one per scan.

**Exit:** 2-day hold cap, failed-gap exit if the completed close falls 3% below entry,
and trend-loss exit if the completed close loses SMA200. Stop-loss and take-profit
from the global risk manager apply throughout.

**Key parameters:**

| Parameter | Value |
|---|---|
| `min_gap_pct` | 5% |
| `max_gap_pct` | 15% |
| `volume_multiplier` | 1.3× opening volume pace |
| `min_breadth_pct` | 65% |
| `require_prior_close_above_trend` | true |
| `max_trend_extension_pct` | 35% above SMA200 |
| `entry_window_minutes` | 45 min after open |
| `max_signals` | 1 top-ranked candidate per scan |
| `hold_days` | 2 business days |

**Regime filter:** SPY > SMA50 (bull market required).

**Default status:** Disabled in `config/config.yaml` and excluded from the
default production/walk-forward strategy lists until its standalone bootstrap
and minute-fill validation improve.

**Monitoring gate:** Run `python3 scheduler/run_validation_gate.py --profile gap`
before re-enabling or scaling capital allocated to this sleeve.

---

## 5. MA Crossover *(Crypto — Enabled)*

**Type:** Trend-following, medium-term swing. Runs 24/7 on daily bars.

**Entry:** Seven conditions must all be true:
1. 6-EMA crosses above 18-EMA on the latest completed daily transition.
2. 21-EMA is sloping upward over the last 5 bars — no crossovers into a flat trend.
3. Today's price range ≥ 50% of the 10-day average range — market is moving.
4. RSI(14) between 35 and 75 — not entering an already-overbought or deeply-oversold
   state.
5. Volume Confirmation: Entry-bar volume ≥ 100% of its 20-day average (`volume_spike_ratio: 1.0`).
6. 3-day return ≥ -2% — avoids buying a fresh cross that is still sliding.
7. Close ≥ 0.5% above the slow EMA — avoids underpowered crosses.

**Exit:** Whichever fires first:
- Latest daily close is at least 2% below entry (`max_loss_exit_pct`) — strategy-level capital preservation exit.
- 9-EMA crosses back below 21-EMA (bearish crossover).
- RSI(14) > 75 (`rsi_exit_max`) — overbought target reached.
- Hard cap at 16 calendar days (`hold_days`).

**Key parameters:**

| Parameter | Value |
|---|---|
| `fast_ema` | 6 |
| `slow_ema` | 18 |
| `timeframe` | 1Day |
| `entry_cross_lookback_days` | 1 |
| `trend_return_lookback_days` | 3 |
| `min_trend_return_pct` | -2% |
| `min_price_above_slow_pct` | 0.5% |
| `max_signals` | 1 top-ranked candidate per scan |
| `hold_days` | 16 calendar days |
| `max_loss_exit_pct` | 2% below entry on latest daily close |
| `rsi_entry_min` | 35 |
| `rsi_entry_max` | 75 |
| `rsi_exit_max` | 75 |
| `volume_spike_ratio` | 1.0 |
| `vol_filter_period` | 10 |

**Regime filter:** BTC/USD > 20-day EMA, and the EMA20 may not be falling more
than 0.5% over five days.

**Live readiness:** Live entries are blocked until the trade log shows at least
25 closed paper exits and 90 calendar days of paper validation for this sleeve.
Paper mode remains available for evidence collection.

**Monitoring gate:** Run `python3 scheduler/run_validation_gate.py --profile ma`
before scaling capital allocated to this sleeve.

---

## 6. Range Breakout *(Crypto — Enabled)*

**Type:** Breakout, short swing trade. Runs 24/7 on daily bars.

**Entry:** All of the following must be true:
1. Today's close ≥ prior 20-day high × 1.006, excluding the current bar.
2. Volume ≥ 2.5× 20-day average — breakout backed by conviction.
3. Price > 50-day EMA and EMA50 is non-declining over 5 bars — breakout in the direction of the longer trend.
4. Today's range ≥ 45% of the 10-day average range — market is not compressed.
5. Close is at least 0.8% and no more than 8% beyond the breakout level — requires
   real follow-through without chasing stale vertical moves.
6. Close is in the upper 10% of the day's range — avoids weak breakout closes.
7. RSI(14) ≤ 82 — avoids severely overextended breakout closes.

**Sizing:** Each signal carries a 2 × ATR(14) stop and ATR-risk quantity targeting
1% account risk before the executor applies the global 8% max-position cap.

**Ranking:** Simultaneous crypto breakouts are sorted by confidence, combining
breakout excess, volume ratio, and trend spread. This avoids entering lower-quality
signals first just because they appear earlier in `crypto.scan_universe`.

**Exit:** Failed breakouts can exit before the 14-calendar-day cap:
- Close ≤ entry × 0.98 — breakout failure.
- Close < EMA50 — trend filter failure.
- RSI(14) ≥ 82 after at least 3% open profit — exhaustion profit-taking.
- High-water profit protection once peak gain reaches `trail_activation_pct`;
  exit if price then falls by `trailing_stop_pct` from the observed peak.

Stop-loss and take-profit from the global risk manager apply throughout.

**Key parameters:**

| Parameter | Value |
|---|---|
| `breakout_lookback_days` | 20 |
| `breakout_pct` | 0.6% above prior 20-day high |
| `min_breakout_extension_pct` | 0.8% above breakout level |
| `max_breakout_extension_pct` | 8% above breakout level |
| `min_close_location` | 80% of daily range |
| `volume_multiplier` | 2.5× |
| `volume_avg_period` | 20 |
| `trend_ema_period` | 50 |
| `trend_slope_lookback` | 5 |
| `min_range_ratio` | 45% of recent average range |
| `rsi_entry_max` | 82 |
| `rsi_exit_max` | 82 |
| `breakdown_exit_pct` | 2% below entry |
| `profit_trailing_enabled` | true |
| `trail_activation_pct` | 6% |
| `trailing_stop_pct` | 4% from post-entry peak |
| `timeframe` | 1Day |
| `hold_days` | 14 calendar days |

**Regime filter:** BTC/USD > 20-day EMA, and the EMA20 may not be falling more
than 0.5% over five days.

---

## Adding a New Strategy

1. Create `strategies/<name>.py` implementing `BaseStrategy` (`scan` + `should_exit`).
2. Add the strategy config block to `config/config.yaml` under `strategies:`.
3. Register it in `scheduler/run_scan.py` (import + strategy list) and add it to
   `HOLD_DAYS` if it uses a hold-day cap.
4. Write unit tests in `tests/`.
5. **Update this file** with a new section following the template above.

## Modifying an Existing Strategy

After any change to entry conditions, exit conditions, filters, parameters, or
enabled state:

1. Update the relevant section in this file to reflect the new behaviour.
2. Update `config/config.yaml` description field to match.
3. Update `README.md` and `backtests.md` per section 16 of `CLAUDE.md`.
