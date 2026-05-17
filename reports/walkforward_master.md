# Master Walk-Forward Report - 2026-05-16

- Profile: `master`
- Generated: `20260516T214815Z` UTC
- Reproduction command: `python3 scheduler/run_walkforward.py --profile master`
- Binding capital-scaling level: `stressed`
- Blocking report levels: `stressed`

## Summary

| Cost Level | Gate | Windows Passed | Pass Rate | Required | Result |
|---|---|---:|---:|---:|---|
| baseline | advisory | 2/7 | 28.6% | 80.0% | FAIL |
| stressed | blocking | 5/7 | 71.4% | 66.0% | PASS |
| severe | advisory | 5/7 | 71.4% | 50.0% | PASS |

## Per-Window Detail

| Cost | Window | Regime | End Date | Days | Return | Annualized | Max DD | PF | Trades | Win | Sharpe | Result | Notes |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| baseline | calm_bull_2019 | Calm bull, low volatility | 12/31/2019 | 180 | +2.09% | +4.28% | -1.90% | 1.67 | 23 | 47.8% | 1.06 | FAIL | trades 23 < 25 |
| baseline | covid_crash_2020 | COVID crash and V-recovery | 09/30/2020 | 180 | +9.89% | +21.07% | -1.64% | 2.98 | 38 | 65.8% | 2.95 | PASS |  |
| baseline | stimulus_bull_2021 | Stimulus-driven late-cycle bull | 12/31/2021 | 180 | +1.25% | +2.55% | -2.23% | 1.24 | 31 | 35.5% | 0.52 | FAIL | annualized_return +2.55% < +4.00% |
| baseline | tech_bear_2022 | Rate-hike technology bear market | 12/31/2022 | 180 | +1.18% | +2.40% | -4.25% | 1.19 | 30 | 33.3% | 0.33 | FAIL | annualized_return +2.40% < +4.00%; profit_factor 1.19 < 1.20; daily_sharpe 0.33 < 0.50 |
| baseline | ai_reversal_2023 | SVB shock and AI-led reversal | 12/31/2023 | 180 | +3.77% | +7.79% | -1.49% | 2.15 | 32 | 50.0% | 1.77 | PASS |  |
| baseline | election_chop_2024 | Pre-election range-bound chop | 09/30/2024 | 180 | -0.52% | -1.04% | -2.74% | 0.91 | 33 | 36.4% | -0.20 | FAIL | annualized_return -1.04% < +4.00%; profit_factor 0.91 < 1.20; daily_sharpe -0.20 < 0.50 |
| baseline | current_regime_auto | Current regime excluding locked OOS | 03/17/2026 | 180 | +0.85% | +1.74% | -3.01% | 1.08 | 33 | 36.4% | 0.38 | FAIL | annualized_return +1.74% < +4.00%; profit_factor 1.08 < 1.20; daily_sharpe 0.38 < 0.50 |
| stressed | calm_bull_2019 | Calm bull, low volatility | 12/31/2019 | 180 | +1.57% | +3.20% | -2.01% | 1.41 | 23 | 47.8% | 0.80 | FAIL | trades 23 < 25 |
| stressed | covid_crash_2020 | COVID crash and V-recovery | 09/30/2020 | 180 | +9.48% | +20.16% | -1.71% | 2.84 | 38 | 65.8% | 2.82 | PASS |  |
| stressed | stimulus_bull_2021 | Stimulus-driven late-cycle bull | 12/31/2021 | 180 | +0.94% | +1.91% | -2.32% | 1.18 | 31 | 35.5% | 0.40 | PASS |  |
| stressed | tech_bear_2022 | Rate-hike technology bear market | 12/31/2022 | 180 | +0.87% | +1.78% | -4.27% | 1.14 | 30 | 33.3% | 0.26 | PASS |  |
| stressed | ai_reversal_2023 | SVB shock and AI-led reversal | 12/31/2023 | 180 | +3.33% | +6.87% | -1.54% | 2.04 | 31 | 45.2% | 1.59 | PASS |  |
| stressed | election_chop_2024 | Pre-election range-bound chop | 09/30/2024 | 180 | -0.90% | -1.82% | -3.04% | 0.85 | 33 | 36.4% | -0.36 | FAIL | annualized_return -1.82% < +0.00%; profit_factor 0.85 < 1.00; daily_sharpe -0.36 < 0.20 |
| stressed | current_regime_auto | Current regime excluding locked OOS | 03/17/2026 | 180 | +0.62% | +1.27% | -3.13% | 1.04 | 33 | 33.3% | 0.29 | PASS |  |
| severe | calm_bull_2019 | Calm bull, low volatility | 12/31/2019 | 180 | +1.25% | +2.56% | -2.12% | 1.29 | 23 | 47.8% | 0.64 | FAIL | trades 23 < 25 |
| severe | covid_crash_2020 | COVID crash and V-recovery | 09/30/2020 | 180 | +7.20% | +15.13% | -1.87% | 2.34 | 34 | 55.9% | 2.57 | PASS |  |
| severe | stimulus_bull_2021 | Stimulus-driven late-cycle bull | 12/31/2021 | 180 | +1.06% | +2.16% | -1.88% | 1.20 | 30 | 36.7% | 0.43 | PASS |  |
| severe | tech_bear_2022 | Rate-hike technology bear market | 12/31/2022 | 180 | +0.85% | +1.74% | -4.29% | 1.13 | 29 | 31.0% | 0.25 | PASS |  |
| severe | ai_reversal_2023 | SVB shock and AI-led reversal | 12/31/2023 | 180 | +2.53% | +5.19% | -1.64% | 1.76 | 30 | 43.3% | 1.20 | PASS |  |
| severe | election_chop_2024 | Pre-election range-bound chop | 09/30/2024 | 180 | -1.39% | -2.79% | -3.48% | 0.78 | 33 | 36.4% | -0.56 | FAIL | annualized_return -2.79% < -2.00%; profit_factor 0.78 < 0.90 |
| severe | current_regime_auto | Current regime excluding locked OOS | 03/17/2026 | 180 | +0.14% | +0.28% | -3.28% | 0.96 | 33 | 33.3% | 0.09 | PASS |  |

## Per-Strategy Attribution (stressed cost)

| Window | Strategy | Trades | Win | Avg P&L | Total P&L | Best | Worst | PF |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| calm_bull_2019 | gap_up | 4 | 50.0% | -0.19% | $-6.82 | +6.72% | -4.02% | 0.89 |
| calm_bull_2019 | momentum | 19 | 47.4% | +0.82% | $124.80 | +11.72% | -5.38% | 1.55 |
| covid_crash_2020 | gap_up | 4 | 25.0% | -2.37% | $-78.04 | +3.19% | -4.97% | 0.25 |
| covid_crash_2020 | momentum | 34 | 70.6% | +3.64% | $1,026.00 | +11.72% | -5.38% | 3.50 |
| stimulus_bull_2021 | gap_up | 2 | 50.0% | +1.55% | $25.29 | +3.87% | -0.76% | 5.06 |
| stimulus_bull_2021 | ma_crossover | 7 | 14.3% | -1.93% | $-79.96 | +11.72% | -7.68% | 0.47 |
| stimulus_bull_2021 | momentum | 20 | 35.0% | +0.02% | $0.96 | +11.72% | -5.38% | 1.00 |
| stimulus_bull_2021 | range_breakout | 2 | 100.0% | +11.72% | $147.60 | +11.72% | +11.72% | inf |
| tech_bear_2022 | gap_up | 1 | 0.0% | -4.97% | $-40.37 | -4.97% | -4.97% | 0.00 |
| tech_bear_2022 | ma_crossover | 12 | 25.0% | -0.05% | $-21.62 | +11.77% | -8.70% | 0.92 |
| tech_bear_2022 | momentum | 14 | 42.9% | +2.02% | $225.83 | +11.72% | -5.38% | 1.95 |
| tech_bear_2022 | range_breakout | 3 | 33.3% | -3.57% | $-76.68 | +1.17% | -8.20% | 0.11 |
| ai_reversal_2023 | gap_up | 3 | 66.7% | +1.16% | $29.01 | +3.98% | -0.91% | 4.91 |
| ai_reversal_2023 | ma_crossover | 6 | 33.3% | +0.26% | $16.91 | +11.72% | -4.78% | 1.15 |
| ai_reversal_2023 | momentum | 16 | 62.5% | +3.62% | $470.06 | +11.72% | -4.29% | 8.14 |
| ai_reversal_2023 | range_breakout | 6 | 0.0% | -3.35% | $-156.17 | -0.05% | -4.96% | 0.00 |
| election_chop_2024 | gap_up | 2 | 50.0% | +0.17% | $2.83 | +3.35% | -3.02% | 1.12 |
| election_chop_2024 | ma_crossover | 8 | 12.5% | -3.93% | $-248.81 | +1.42% | -5.97% | 0.04 |
| election_chop_2024 | momentum | 22 | 45.5% | +1.01% | $171.28 | +11.72% | -5.38% | 1.57 |
| election_chop_2024 | range_breakout | 1 | 0.0% | -1.93% | $-15.32 | -1.93% | -1.93% | 0.00 |
| current_regime_auto | gap_up | 1 | 100.0% | +1.05% | $8.38 | +1.05% | +1.05% | inf |
| current_regime_auto | ma_crossover | 8 | 25.0% | +0.13% | $19.69 | +11.72% | -4.94% | 1.18 |
| current_regime_auto | momentum | 24 | 33.3% | -0.01% | $-3.72 | +11.72% | -5.38% | 0.99 |

## Data Caveats

- calm_bull_2019 / baseline: 20 missing-history symbols (AAVE/USD, ABNB, ADA/USD, ARM, AVAX/USD, BITO, BTC/USD, COIN, DASH, DOGE/USD, DOT/USD, ETH/USD...)
- covid_crash_2020 / baseline: 20 missing-history symbols (AAVE/USD, ABNB, ADA/USD, ARM, AVAX/USD, BITO, BTC/USD, COIN, DASH, DOGE/USD, DOT/USD, ETH/USD...)
- stimulus_bull_2021 / baseline: 5 missing-history symbols (ADA/USD, ARM, DOT/USD, IBIT, XRP/USD)
- tech_bear_2022 / baseline: 4 missing-history symbols (ADA/USD, ARM, DOT/USD, XRP/USD)
- ai_reversal_2023 / baseline: 2 missing-history symbols (ADA/USD, XRP/USD)
- election_chop_2024 / baseline: 1 missing-history symbols (ADA/USD)
- calm_bull_2019 / stressed: 20 missing-history symbols (AAVE/USD, ABNB, ADA/USD, ARM, AVAX/USD, BITO, BTC/USD, COIN, DASH, DOGE/USD, DOT/USD, ETH/USD...)
- covid_crash_2020 / stressed: 20 missing-history symbols (AAVE/USD, ABNB, ADA/USD, ARM, AVAX/USD, BITO, BTC/USD, COIN, DASH, DOGE/USD, DOT/USD, ETH/USD...)
- stimulus_bull_2021 / stressed: 5 missing-history symbols (ADA/USD, ARM, DOT/USD, IBIT, XRP/USD)
- tech_bear_2022 / stressed: 4 missing-history symbols (ADA/USD, ARM, DOT/USD, XRP/USD)
- ai_reversal_2023 / stressed: 2 missing-history symbols (ADA/USD, XRP/USD)
- election_chop_2024 / stressed: 1 missing-history symbols (ADA/USD)
- calm_bull_2019 / severe: 20 missing-history symbols (AAVE/USD, ABNB, ADA/USD, ARM, AVAX/USD, BITO, BTC/USD, COIN, DASH, DOGE/USD, DOT/USD, ETH/USD...)
- covid_crash_2020 / severe: 20 missing-history symbols (AAVE/USD, ABNB, ADA/USD, ARM, AVAX/USD, BITO, BTC/USD, COIN, DASH, DOGE/USD, DOT/USD, ETH/USD...)
- stimulus_bull_2021 / severe: 5 missing-history symbols (ADA/USD, ARM, DOT/USD, IBIT, XRP/USD)
- tech_bear_2022 / severe: 4 missing-history symbols (ADA/USD, ARM, DOT/USD, XRP/USD)
- ai_reversal_2023 / severe: 3 missing-history symbols (ADA/USD, BTC/USD, XRP/USD)
- election_chop_2024 / severe: 1 missing-history symbols (ADA/USD)

Backtest semantics:
- Gap-Up opening-window backtest uses a synthetic 9:35 ET daily-open proxy, not real minute bars; Gap-Up fills are not intraday-validated. Symbols: AAPL, ABBV, ABNB, ADBE, AMAT, AMD, AMGN, AMT, AMZN.
- Momentum volume-pace backtest uses a synthetic elapsed-session volume proxy from daily bars, not real minute bars; volume-pace fills are not intraday-validated. Symbols: AAPL, ABBV, ABNB, ADBE, AMAT, AMD, AMGN, AMT, AMZN.

Raw JSON artifacts: `reports/walkforward/master_20260516T214815Z`

Locked OOS note: the latest held-out period is intentionally excluded from the master run. Run `python3 scheduler/run_walkforward.py --profile master --oos-only` when ready for final validation.
