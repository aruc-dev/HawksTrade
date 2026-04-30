# HawksTrade — Strategy Reference

> **Maintenance rule:** Update this file whenever a strategy's entry conditions, exit
> conditions, filters, parameters, or enabled state change. This is mandatory — see
> section 16 of `CLAUDE.md`.

---

## Overview

| Strategy | Asset | Status | File |
|---|---|---|---|
| Momentum | Stocks | **Enabled** | `momentum.py` |
| RSI Reversion | Stocks | Disabled by default | `rsi_reversion.py` |
| Gap-Up | Stocks | Disabled | `gap_up.py` |
| MA Crossover | Crypto | **Enabled** | `ma_crossover.py` |
| Range Breakout | Crypto | **Enabled** | `range_breakout.py` |

All strategies share a common global risk layer (8% max position size,
3.5% stop-loss, 12% take-profit, max 10 open positions, 5% daily-loss halt)
enforced by `core/risk_manager.py` and `scheduler/run_risk_check.py`.
Individual strategies may override the stop via
`atr_stop_price` on their signals; the executor writes that value to the trade log
when it is wider than the global stop.

---

## 1. Momentum *(Stocks — Enabled, Adaptive v2.1)*

**Type:** Trend-following, swing trade.

**Entry:** Ranks every stock in the scan universe by its 5-day price return. Buys
only the top candidate that has gained at least 10%, subject to four layers of adaptive
filtering:

1. **Phase 1 — ATR Stop + 1% Risk Sizing**: Each signal carries an ATR-based stop
   (`entry - 2 × ATR_14`). Position size is computed as `(equity × 1%) / (entry - atr_stop)`
   so that every trade risks exactly 1% of capital, capped at 8% per-position max.
2. **Phase 2 — Sector-Neutral Ranking**: Enforces `max_positions_per_sector: 1`
   using a static GICS sector map across both existing/pending positions and new
   candidates. If the top two ranked stocks share a capped sector, the lower-ranked
   one is skipped in favour of the next stock in a different sector.
3. **Breadth Coverage Guard**: Requires at least 75% of the scan universe to have
   enough valid bars for SMA50 breadth computation. If coverage is lower, Momentum
   fails closed and opens no new positions rather than trading from a partial market sample.
4. **Market Breadth Tiered Regime Guard**: Computes what fraction of
   the scan universe trades above its own SMA50 (`rm.market_breadth_pct`):
   - **Red** (SPY < SMA50 OR breadth < 25%): no new entries.
   - **Yellow** (breadth 25–50%): reduced deployment, up to `yellow_max_positions: 1`.
   - **Green** (breadth ≥ 50%): full `top_n: 1` deployment.

**Volume Confirmation (per-signal):** Each candidate must have entry-bar volume above 180% of
its 20-day average volume (`volume_spike_ratio: 1.8`). Signals where today's volume is
suspiciously thin — a common trait of exhaustion moves — are skipped. The screener
provides a separate 20-day ADV baseline at universe construction time; this check adds
a per-signal guard at scan time.

**Optional Alpha Gate:** `min_alpha_pct` can require a candidate's 5-day return to
exceed SPY's 5-day return by a configured amount. It is set to `0.0` in the validated
default because the stricter alpha gate reduced the latest 12-month result.

**Exit:** Three-layer policy:
- After the minimum 4-day hold, flat or losing trades exit immediately.
- Profitable trades run under a trailing stop that activates once the peak gain
  reaches 6%; price must then not fall more than 4% from that peak.
- A hard 20-day cap closes any position that never pulled back to trigger the trail.

**Stop:** The ATR-based stop is written to the trade log as `stop_loss` and used by
the live risk check as the custom stop input, giving volatile stocks more breathing
room while the global 3.5% stop remains the absolute floor.

**Key parameters (`config/config.yaml`):**

| Parameter | Value |
|---|---|
| `top_n` | 1 |
| `min_momentum_pct` | 10% (5-day return) |
| `min_alpha_pct` | 0% excess return over SPY (disabled) |
| `hold_days` (minimum) | 4 business days |
| `max_hold_days` | 20 business days |
| `trail_activation_pct` | 6% peak gain |
| `trailing_stop_pct` | 4% from peak |
| `exit_policy` | `profit_trailing` |
| `atr_period` | 14 |
| `atr_multiplier` | 2.0 |
| `risk_per_trade_pct` | 1% of equity |
| `max_positions_per_sector` | 1 |
| `breadth_green_threshold` | 50% |
| `breadth_red_threshold` | 25% |
| `min_breadth_coverage_pct` | 75% |
| `yellow_max_positions` | 1 |
| `volume_spike_ratio` | 1.8 (entry bar > 180% of 20-day avg volume) |

**Regime filters:**
- SPY > SMA50 (hard requirement; Red if fails).
- Market breadth ≥ 25% of universe above SMA50 (Red if fails).
- Valid breadth inputs for at least 75% of the scan universe.

---

## 2. RSI Reversion *(Stocks — Disabled by default)*

**Type:** Mean reversion, swing trade.

**Entry:** Five conditions must all be true simultaneously:
1. RSI(14) < 30 — deeply oversold.
2. Bollinger Band %B < 20% — price in the lower quintile of the 20-day, 2σ band.
3. Volume ≥ 1.5× 20-day average — confirming capitulation selling.
4. Last close > prior close — 1-bar recovery; freefall has paused.
5. SMA200 Band: Price must be within configurable buffers of the 200-day MA.
   - Entry blocked if `price < SMA200 × (1 - sma200_lower_buffer_pct)` (broken stocks).
   - Entry blocked if `price > SMA200 × (1 + sma200_upper_buffer_pct)` (overextended stocks).
   - Default buffers: ±15%.

**Stop:** 2 × ATR(14) below entry (volatility-adjusted). The ATR stop flows
through `order_executor.enter_position` into the trade log and is picked up by
`run_risk_check` in both backtest and live/paper modes. It widens the stop only
when it falls further below entry than the global 3.5% stop; the global stop
governs whenever the ATR stop is tighter or absent.

**Exit:** Whichever fires first:
- Price ≥ SMA(`bb_period`) — mean-reversion target reached (default: SMA20).
- RSI(14) > `overbought_threshold` — momentum neutral; edge evaporated (default: 50).
- 10-business-day hard cap (`hold_days`).

**Key parameters:**

| Parameter | Value |
|---|---|
| `rsi_period` | 14 |
| `oversold_threshold` | 30 |
| `overbought_threshold` | 50 (RSI neutral exit) |
| `hold_days` | 10 business days |
| `bb_period` | 20 |
| `bb_std` | 2.0 |
| `atr_period` | 14 |
| `atr_multiplier` | 2.0 |
| `vix_multiplier` | 1.2 |
| `sma200_lower_buffer_pct` | 15% |
| `sma200_upper_buffer_pct` | 15% |
| `volume_spike_ratio` | 1.5 |

**Regime filters:**
- Crash filter: skip if SPY is >20% below its 252-day peak.
- VIX proxy: skip if SPY realised HV(20) > 200-day HV MA × `vix_multiplier` (default: 1.2).

**Enablement gate:** This strategy remains disabled by default until
`python3 scheduler/run_validation_gate.py --profile rsi` passes. The gate
requires cost-aware backtest performance plus at least 60 paper-trading days,
20 closed RSI trades, 48% win rate, 1.15 profit factor, +2% aggregate paper
return, and max drawdown no worse than 4%.

---

## 3. Gap-Up *(Stocks — Disabled)*

**Type:** Opening momentum, short swing trade.

**Entry:** All of the following must be true at market open (within first 45 minutes):
1. Today's open is 3–15% above the prior close (gap bounded to avoid exhaustion gaps).
2. Volume ≥ 1.5× 20-day average.
3. Price > SMA200 — stock is in a long-term uptrend.
4. Prior day closed green (close > open) — pre-gap momentum confirmation.
5. Entry within 45 minutes of the 9:30 ET open.

A "true gap" bonus (today's open also above the prior day's high) raises the
confidence score.

**Exit:** 2-day hold cap. No active strategy-level exit signal; stop-loss and
take-profit from the global risk manager apply throughout.

**Key parameters:**

| Parameter | Value |
|---|---|
| `min_gap_pct` | 3% |
| `max_gap_pct` (code) | 15% (hard cap) |
| `volume_multiplier` | 1.5× |
| `entry_window_minutes` | 45 min after open |
| `hold_days` | 2 business days |

**Regime filter:** SPY > SMA50 (bull market required).

---

## 4. MA Crossover *(Crypto — Enabled)*

**Type:** Trend-following, medium-term swing. Runs 24/7 on daily bars.

**Entry:** Five conditions must all be true:
1. 9-EMA crosses above 21-EMA on the daily chart, or crossed within the configured recent-entry window.
2. 21-EMA is sloping upward over the last 5 bars — no crossovers into a flat trend.
3. Today's price range ≥ 50% of the 10-day average range — market is moving.
4. RSI(14) between 35 and 70 — not entering an already-overbought or deeply-oversold
   state.
5. Volume Confirmation: Entry-bar volume ≥ 120% of its 20-day average (`volume_spike_ratio: 1.2`).

**Exit:** Whichever fires first:
- Latest daily close is at least 1% below entry (`max_loss_exit_pct`) — strategy-level capital preservation exit.
- 9-EMA crosses back below 21-EMA (bearish crossover).
- RSI(14) > 75 (`rsi_exit_max`) — overbought target reached.
- Hard cap at 12 calendar days (`hold_days`).

**Key parameters:**

| Parameter | Value |
|---|---|
| `fast_ema` | 9 |
| `slow_ema` | 21 |
| `timeframe` | 1Day |
| `entry_cross_lookback_days` | 2 |
| `hold_days` | 12 calendar days |
| `max_loss_exit_pct` | 1% below entry on latest daily close |
| `rsi_entry_min` | 35 |
| `rsi_entry_max` | 70 |
| `rsi_exit_max` | 75 |
| `volume_spike_ratio` | 1.2 |
| `vol_filter_period` | 10 |

**Regime filter:** BTC/USD > 20-day EMA (crypto bull regime required).

---

## 5. Range Breakout *(Crypto — Enabled)*

**Type:** Breakout, short swing trade. Runs 24/7 on daily bars.

**Entry:** All of the following must be true:
1. Today's close ≥ prior day's high × 1.008 (price breaks 0.8% above the prior range).
2. Volume ≥ 1.8× 20-day average — breakout backed by conviction.
3. Price > 50-day EMA and EMA50 is non-declining over 5 bars — breakout in the direction of the longer trend.
4. Today's range ≥ 50% of the 10-day average range — market is not compressed.
5. Close is no more than 8% beyond the breakout level — avoids chasing stale vertical moves.
6. RSI(14) ≤ 78 — avoids severely overextended breakout closes.

**Sizing:** Each signal carries a 2 × ATR(14) stop and ATR-risk quantity targeting
1% account risk before the executor applies the global 8% max-position cap.

**Ranking:** Simultaneous crypto breakouts are sorted by confidence, combining
breakout excess, volume ratio, and trend spread. This avoids entering lower-quality
signals first just because they appear earlier in `crypto.scan_universe`.

**Exit:** Failed breakouts can exit before the 3-calendar-day cap:
- Close ≤ entry × 0.98 — breakout failure.
- Close < EMA50 — trend filter failure.
- RSI(14) ≥ 82 after at least 3% open profit — exhaustion profit-taking.

Stop-loss and take-profit from the global risk manager apply throughout.

**Key parameters:**

| Parameter | Value |
|---|---|
| `breakout_pct` | 0.8% above prior high |
| `max_breakout_extension_pct` | 8% above breakout level |
| `volume_multiplier` | 1.8× |
| `volume_avg_period` | 20 |
| `trend_ema_period` | 50 |
| `trend_slope_lookback` | 5 |
| `min_range_ratio` | 50% of recent average range |
| `rsi_entry_max` | 78 |
| `rsi_exit_max` | 82 |
| `breakdown_exit_pct` | 2% below entry |
| `timeframe` | 1Day |
| `hold_days` | 3 calendar days |

**Regime filter:** BTC/USD > 20-day EMA (crypto bull regime required).

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
