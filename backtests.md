# HawksTrade v4 — Backtest Summary

![HawksTrade v4 Dashboard](assets/hawkstrade_v4_dashboard.png)

> **Generated:** April 11, 2026  
> **Strategy Version:** v4 (3 improvements over v3)  
> **Starting Capital:** $10,000  
> **Test Environment:** Alpaca Paper Trading (backtest simulation)  
> **Note on partial runs:** The 365-day backtest (12mo) hit the 600-second process timeout and captured ~77% of the target period. Returns and metrics reflect the actual days simulated.

---

## What Was Implemented (v4 Changes)

| # | Change | Files Modified | Summary |
|---|--------|---------------|---------|
| 1 | **Crypto Regime Filter** | `risk_manager.py`, `ma_crossover.py`, `range_breakout.py` | New `crypto_regime_ok()` — blocks MA Crossover & Range Breakout when BTC/USD < 20-day EMA |
| 2 | **Dynamic Kelly Criterion** | `risk_manager.py`, `order_executor.py`, `tracking/trade_log.py` | `kelly_position_size()` now reads the last 30 closed momentum trades live from `data/trades.csv`. |
| 3 | **RSI 2-Bar Recovery** | `strategies/rsi_reversion.py` | Added consecutive-higher-close guard — prevents entering falling-knife situations. |

---

## Results Overview

| Period | Days Simulated | Final Value | Return | Win Rate | Profit Factor | Trades | Max DD |
|--------|---------------|-------------|--------|----------|--------------|--------|--------|
| **Last 6 Months** | 182/182 ✓ | $9,758 | **-2.41%** | 21.3% | 0.69x | 75 | -4.79% |
| **Last 12 Months** | 280/365 ⚑ | $13,502 | **+35.02%** ✅ | 43.2% | 2.09x | 118 | -5.39% |

> ⚑ = Partial run (backtest timed out at 600s). Returns reflect actual days simulated only.

---

## Strategy-by-Strategy Analysis (12-Month)

| Strategy | Trades | Win Rate | Net P&L | PF |
|----------|--------|----------|---------|----|
| **Momentum** | 98 | 44.9% | +$3,218 | 2.14x |
| **MA Crossover** | 10 | 50.0% | +$329 | 3.97x |
| **Range Breakout** | 9 | 22.2% | -$11 | 1.01x |
| **RSI Reversion** | 0 | — | $0 | — |

---

## Market Regime Filter — Impact Analysis

| Period | SPY<SMA50 Blocks (stocks) | BTC<EMA20 Blocks (crypto) | Total Capital Protected |
|--------|--------------------------|--------------------------|------------------------|
| Last 6 Months | 180 | 262 | 442 scan-days blocked |
| Last 12 Months | 93 | 266 | 359 scan-days blocked |

---

*HawksTrade v4 — Alpaca Paper Trading · Simulation data only · Not financial advice*
