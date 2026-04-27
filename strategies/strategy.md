# HawksTrade — Strategy Reference

> **Maintenance rule:** Update this file whenever a strategy's entry conditions, exit
> conditions, filters, parameters, or enabled state change. This is mandatory — see
> section 16 of `CLAUDE.md`.

---

## Overview

| Strategy | Asset | Status | File |
|---|---|---|---|
| Momentum | Stocks | **Enabled** | `momentum.py` |
| RSI Reversion | Stocks | Disabled | `rsi_reversion.py` |
| Gap-Up | Stocks | Disabled | `gap_up.py` |
| MA Crossover | Crypto | **Enabled** | `ma_crossover.py` |
| Range Breakout | Crypto | **Enabled** | `range_breakout.py` |

All strategies share a common global risk layer (3.5% stop-loss, 12% take-profit,
max 10 open positions, 5% daily-loss halt) enforced by `core/risk_manager.py` and
`scheduler/run_risk_check.py`. Individual strategies may override the stop via
`custom_stop_price` on their signals (see Momentum and RSI Reversion).

---

## 1. Momentum *(Stocks — Enabled, Adaptive v2.0)*

**Type:** Trend-following, swing trade.

**Entry:** Ranks every stock in the scan universe by its 5-day price return. Buys
the top 3 that have gained at least 6%, subject to three layers of adaptive
filtering:

1. **Phase 1 — ATR Stop + 1% Risk Sizing**: Each signal carries an ATR-based stop
   (`entry - 2 × ATR_14`). Position size is computed as `(equity × 1%) / (entry - atr_stop)`
   so that every trade risks exactly 1% of capital, capped at 5% per-position max.
2. **Phase 2 — Sector-Neutral Ranking**: Enforces `max_positions_per_sector: 1`
   using a static GICS sector map. If the top two ranked stocks share a sector,
   the lower-ranked one is skipped in favour of the next stock in a different sector.
3. **Phase 3 — Market Breadth Tiered Regime Guard**: Computes what fraction of
   the scan universe trades above its own SMA50 (`rm.market_breadth_pct`):
   - **Red** (SPY < SMA50 OR breadth < 25%): no new entries.
   - **Yellow** (breadth < 40%): reduced deployment, up to `yellow_max_positions: 3`.
   - **Green** (breadth ≥ 40%): full `top_n: 3` deployment.

**Exit:** Three-layer policy:
- After the minimum 4-day hold, flat or losing trades exit immediately.
- Profitable trades run under a trailing stop that activates once the peak gain
  reaches 6%; price must then not fall more than 4% from that peak.
- A hard 20-day cap closes any position that never pulled back to trigger the trail.

**Stop:** The ATR-based stop is written to the trade log as `stop_loss` and used by
the live risk check as `custom_stop_price`, giving volatile stocks more breathing
room while the global 3.5% stop remains the absolute floor.

**Key parameters (`config/config.yaml`):**

| Parameter | Value |
|---|---|
| `top_n` | 3 |
| `min_momentum_pct` | 6% (5-day return) |
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
| `breadth_yellow_threshold` | 40% |
| `breadth_red_threshold` | 25% |
| `yellow_max_positions` | 3 |

**Regime filters:**
- SPY > SMA50 (hard requirement; Red if fails).
- Market breadth ≥ 25% of universe above SMA50 (Red if fails).

---

## 2. RSI Reversion *(Stocks — Disabled)*

**Type:** Mean reversion, swing trade.

**Entry:** Five conditions must all be true simultaneously:
1. RSI(14) < 30 — deeply oversold.
2. Bollinger Band %B < 20% — price in the lower quintile of the 20-day, 2σ band.
3. Volume ≥ 1.5× 20-day average — confirming capitulation selling.
4. Last close > prior close — 1-bar recovery; freefall has paused.
5. Price > SMA200 × 0.85 — not a structurally broken stock (within 15% of 200-day MA).

**Stop:** 2 × ATR(14) below entry (volatility-adjusted). Wider than the global 3.5%
stop in high-vol conditions; global stop remains the absolute floor.

**Exit:** Whichever fires first:
- Price ≥ SMA20 — mean-reversion target reached.
- RSI(14) > 50 — momentum neutral; edge evaporated.
- 10-business-day hard cap.

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

**Regime filters:**
- Crash filter: skip if SPY is >20% below its 252-day peak.
- VIX proxy: skip if SPY realised HV(20) > 200-day HV MA × 1.2.

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

**Entry:** Four conditions must all be true:
1. 9-EMA crosses above 21-EMA on the daily chart (bullish crossover).
2. 21-EMA is sloping upward over the last 5 bars — no crossovers into a flat trend.
3. Today's price range ≥ 50% of the 10-day average range — market is moving.
4. RSI(14) between 35 and 70 — not entering an already-overbought or deeply-oversold
   state.

**Exit:** 9-EMA crosses back below 21-EMA (bearish crossover). Hard cap at 12
calendar days.

**Key parameters:**

| Parameter | Value |
|---|---|
| `fast_ema` | 9 |
| `slow_ema` | 21 |
| `timeframe` | 1Day |
| `hold_days` | 12 calendar days |

**Regime filter:** BTC/USD > 20-day EMA (crypto bull regime required).

---

## 5. Range Breakout *(Crypto — Enabled)*

**Type:** Breakout, short swing trade. Runs 24/7 on daily bars.

**Entry:** All of the following must be true:
1. Today's close ≥ prior day's high × 1.008 (price breaks 0.8% above the prior range).
2. Volume ≥ 1.8× 20-day average — breakout backed by conviction.
3. Price > 50-day EMA — breakout in the direction of the longer trend.
4. Today's range ≥ 50% of the 10-day average range — market is not compressed.

**Exit:** No active strategy-level exit signal. Held for 3 calendar days, then
exited by the hold-days cap. Stop-loss and take-profit from the global risk manager
apply throughout.

**Key parameters:**

| Parameter | Value |
|---|---|
| `breakout_pct` | 0.8% above prior high |
| `volume_multiplier` | 1.8× |
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
