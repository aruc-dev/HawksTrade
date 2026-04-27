# TODO: HawksTrade Strategy Optimization (v2.0)

This document outlines the technical requirements for upgrading the HawksTrade Momentum Strategy from a fixed-rule system to an adaptive, institutional-grade model.

## Phase 1: Volatility-Adjusted Risk Management (The ATR Bridge)
*Goal: Stop being "shaken out" by normal market noise and normalize risk across different stocks.*

- [x] **Implement ATR Calculation**: `_calc_atr()` added in `strategies/momentum.py`; uses EWM-smoothed 14-day True Range.
- [x] **Refactor Stop-Loss Logic**: 
    - `atr_stop_price = Entry_Price - (atr_multiplier * ATR_14)` computed per signal.
    - Stop flows to trade log via `order_executor.enter_position(atr_stop_price=...)`.
    - Live risk check reads `stop_loss` column and passes as `custom_stop_price` to `should_exit_position`.
- [x] **Risk-Based Position Sizing**: 
    - Formula: `Units = (Total_Equity * risk_per_trade_pct) / (Entry_Price - ATR_Stop)`.
    - `atr_risk_qty` carried in signal and passed as `suggested_qty` to order executor.
    - Each trade risks `risk_per_trade_pct: 0.01` (1%) of equity, capped by `max_position_pct: 5%`.

## Phase 2: Sector-Neutral Ranking (Anti-Correlation)
*Goal: Prevent portfolio clustering in a single industry to survive sector-specific rotations.*

- [x] **Integrate Sector Metadata**: Static GICS `_SECTOR_MAP` in `strategies/momentum.py` covers the full configured universe + extended backtest pool (80+ symbols across 12 sectors).
- [x] **Update Ranking Logic**: 
    - `_sector_filtered_top_n()` enforces `max_positions_per_sector: 1` in candidate selection.
    - If #1 and #2 ranked stocks share a sector, #2 is skipped and replaced with the next highest-ranked stock in a different sector.

## Phase 3: Market Breadth Regime Guard (Advanced Safety)
*Goal: Identify "thin" markets where the index is high but most stocks are failing.*

- [x] **Calculate Participation Rate**: `rm.market_breadth_pct(universe, bars_data)` in `core/risk_manager.py` counts the fraction of the scan universe trading above their own SMA50.
- [x] **Tiered Entry Logic** (in `strategies/momentum.py`):
    - **Green Light**: `SPY > SMA50` AND `Breadth >= 40%` → Full deployment (`top_n` positions).
    - **Yellow Light**: `SPY > SMA50` AND `Breadth < 40%` → Reduced deployment (`yellow_max_positions: 3`).
    - **Red Light**: `SPY < SMA50` OR `Breadth < 25%` → No new entries; scan returns `[]`.

## Phase 4: Validation & Backtesting
*90-day backtest (2026-01-27 to 2026-04-27, $10,000 fund, screener enabled)*

- [x] **A/B Test** results:

| Metric | Pure Momentum (no sector/breadth filters) | Adaptive v2.0 |
|--------|------------------------------------------|---------------|
| Final Value | +7.68% | +5.00% |
| Win Rate | 56.8% | 41.7% |
| Max Drawdown | -1.05% | **-0.76%** |
| Trades | 37 | 36 |

- [x] **Metrics Check**:
    - **Max Drawdown**: Improved from -1.05% to -0.76% (28% reduction in peak drawdown).
    - **Sector diversity**: Adaptive v2.0 entered ARM/Tech + UNH/HealthCare + SLB/Energy vs potentially correlated tech entries without the sector filter.
    - **Trade-off**: Breadth/sector filters reduce exposure in mixed markets — lower return in this bull-trending 90-day window, better protection during Q1 2026 tariff-driven selloff.
    - Note: short 90-day window; benefits of regime protection accumulate over full market cycles with sustained downtrends.
