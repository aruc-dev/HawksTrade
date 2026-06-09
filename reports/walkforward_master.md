# Master Walk-Forward Report - 2026-06-09

- Profile: `master`
- Generated: `20260609T084950Z` UTC
- Reproduction command: `python3 scheduler/run_walkforward.py --profile master`
- Binding capital-scaling level: `stressed`
- Blocking report levels: `stressed`

## Summary

| Cost Level | Gate | Windows Passed | Pass Rate | Required | Result |
|---|---|---:|---:|---:|---|
| baseline | advisory | 0/7 | 0.0% | 80.0% | FAIL |
| stressed | blocking | 2/7 | 28.6% | 66.0% | FAIL |
| severe | advisory | 2/7 | 28.6% | 50.0% | FAIL |

## Per-Window Detail

Point estimates are shown first. Gate columns show the bootstrap-bound metrics used for pass/fail decisions when confidence intervals are present.

| Cost | Window | Regime | End Date | Days | Return | Annualized | Max DD | PF | Trades | Win | Sharpe | Gate Ann. | Gate DD | Gate PF | Gate Sharpe | Result | Notes |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| baseline | calm_bull_2019 | Calm bull, low volatility | 12/31/2019 | 180 | +0.90% | +1.83% | -1.02% | 1.65 | 46 | 47.8% | 1.10 | -1.32% | -1.51% | 0.86 | -0.77 | FAIL | annualized_return -1.32% < +4.00%; profit_factor 0.86 < 1.20; daily_sharpe -0.77 < 0.50 |
| baseline | covid_crash_2020 | COVID crash and V-recovery | 09/30/2020 | 180 | +3.46% | +7.13% | -1.19% | 2.84 | 52 | 50.0% | 2.87 | +1.37% | -1.74% | 1.54 | 0.55 | FAIL | annualized_return +1.37% < +4.00% |
| baseline | stimulus_bull_2021 | Stimulus-driven late-cycle bull | 12/31/2021 | 180 | +0.19% | +0.38% | -0.93% | 1.07 | 59 | 42.4% | 0.23 | -3.12% | -2.11% | 0.59 | -1.99 | FAIL | annualized_return -3.12% < +4.00%; profit_factor 0.59 < 1.20; daily_sharpe -1.99 < 0.50 |
| baseline | tech_bear_2022 | Rate-hike technology bear market | 12/31/2022 | 180 | +0.18% | +0.36% | -0.98% | 1.05 | 37 | 32.4% | 0.18 | -3.02% | -2.20% | 0.51 | -1.51 | FAIL | annualized_return -3.02% < +4.00%; profit_factor 0.51 < 1.20; daily_sharpe -1.51 < 0.50 |
| baseline | ai_reversal_2023 | SVB shock and AI-led reversal | 12/31/2023 | 180 | +2.28% | +4.68% | -1.07% | 1.49 | 97 | 50.5% | 1.62 | -1.74% | -2.63% | 0.97 | -0.62 | FAIL | annualized_return -1.74% < +4.00%; profit_factor 0.97 < 1.20; daily_sharpe -0.62 < 0.50 |
| baseline | election_chop_2024 | Pre-election range-bound chop | 09/30/2024 | 180 | +0.21% | +0.43% | -0.82% | 0.80 | 55 | 34.5% | 0.31 | -2.45% | -1.66% | 0.43 | -1.93 | FAIL | annualized_return -2.45% < +4.00%; profit_factor 0.43 < 1.20; daily_sharpe -1.93 < 0.50 |
| baseline | current_regime_auto | Current regime excluding locked OOS | 02/14/2026 | 180 | +4.49% | +9.31% | -0.74% | 2.15 | 75 | 50.7% | 3.89 | +3.75% | -1.39% | 1.32 | 1.64 | FAIL | annualized_return +3.75% < +4.00% |
| stressed | calm_bull_2019 | Calm bull, low volatility | 12/31/2019 | 180 | +0.51% | +1.04% | -1.16% | 1.30 | 48 | 45.8% | 0.61 | -2.25% | -1.82% | 0.68 | -1.26 | FAIL | annualized_return -2.25% < +0.00%; profit_factor 0.68 < 1.00; daily_sharpe -1.26 < 0.20 |
| stressed | covid_crash_2020 | COVID crash and V-recovery | 09/30/2020 | 180 | +3.18% | +6.56% | -1.22% | 2.54 | 53 | 49.1% | 2.61 | +0.75% | -1.85% | 1.38 | 0.31 | PASS |  |
| stressed | stimulus_bull_2021 | Stimulus-driven late-cycle bull | 12/31/2021 | 180 | -0.01% | -0.02% | -1.03% | 1.00 | 59 | 42.4% | -0.00 | -3.49% | -2.26% | 0.55 | -2.24 | FAIL | annualized_return -3.49% < +0.00%; profit_factor 0.55 < 1.00; daily_sharpe -2.24 < 0.20 |
| stressed | tech_bear_2022 | Rate-hike technology bear market | 12/31/2022 | 180 | +0.02% | +0.03% | -0.98% | 0.96 | 37 | 32.4% | 0.03 | -3.42% | -2.31% | 0.46 | -1.71 | FAIL | annualized_return -3.42% < +0.00%; profit_factor 0.46 < 1.00; daily_sharpe -1.71 < 0.20 |
| stressed | ai_reversal_2023 | SVB shock and AI-led reversal | 12/31/2023 | 180 | +1.98% | +4.06% | -1.15% | 1.40 | 97 | 50.5% | 1.41 | -2.32% | -2.77% | 0.90 | -0.83 | FAIL | annualized_return -2.32% < +0.00%; profit_factor 0.90 < 1.00; daily_sharpe -0.83 < 0.20 |
| stressed | election_chop_2024 | Pre-election range-bound chop | 09/30/2024 | 180 | +0.05% | +0.10% | -0.93% | 0.75 | 55 | 32.7% | 0.08 | -2.76% | -1.78% | 0.40 | -2.18 | FAIL | annualized_return -2.76% < +0.00%; profit_factor 0.40 < 1.00; daily_sharpe -2.18 < 0.20 |
| stressed | current_regime_auto | Current regime excluding locked OOS | 02/14/2026 | 180 | +4.80% | +9.97% | -0.76% | 2.24 | 75 | 50.7% | 3.29 | +4.00% | -1.42% | 1.36 | 1.61 | PASS |  |
| severe | calm_bull_2019 | Calm bull, low volatility | 12/31/2019 | 180 | +0.25% | +0.50% | -1.24% | 1.14 | 50 | 42.0% | 0.29 | -2.76% | -1.99% | 0.59 | -1.48 | FAIL | annualized_return -2.76% < -2.00%; profit_factor 0.59 < 0.90 |
| severe | covid_crash_2020 | COVID crash and V-recovery | 09/30/2020 | 180 | +3.02% | +6.21% | -1.24% | 2.42 | 52 | 48.1% | 2.46 | +0.42% | -1.93% | 1.31 | 0.18 | PASS |  |
| severe | stimulus_bull_2021 | Stimulus-driven late-cycle bull | 12/31/2021 | 180 | -0.41% | -0.83% | -1.29% | 0.86 | 60 | 40.0% | -0.47 | -4.47% | -2.65% | 0.46 | -2.80 | FAIL | annualized_return -4.47% < -2.00%; profit_factor 0.46 < 0.90 |
| severe | tech_bear_2022 | Rate-hike technology bear market | 12/31/2022 | 180 | -0.27% | -0.54% | -0.99% | 0.82 | 38 | 31.6% | -0.25 | -3.98% | -2.51% | 0.37 | -2.03 | FAIL | annualized_return -3.98% < -2.00%; profit_factor 0.37 < 0.90 |
| severe | ai_reversal_2023 | SVB shock and AI-led reversal | 12/31/2023 | 180 | +1.50% | +3.06% | -1.30% | 1.25 | 97 | 49.5% | 1.07 | -3.24% | -3.02% | 0.80 | -1.17 | FAIL | annualized_return -3.24% < -2.00%; profit_factor 0.80 < 0.90 |
| severe | election_chop_2024 | Pre-election range-bound chop | 09/30/2024 | 180 | -0.60% | -1.22% | -1.52% | 0.55 | 55 | 29.1% | -0.82 | -4.10% | -2.34% | 0.28 | -3.16 | FAIL | annualized_return -4.10% < -2.00%; profit_factor 0.28 < 0.90 |
| severe | current_regime_auto | Current regime excluding locked OOS | 02/14/2026 | 180 | +4.40% | +9.13% | -0.77% | 2.03 | 75 | 49.3% | 3.03 | +3.24% | -1.50% | 1.23 | 1.33 | PASS |  |

## Per-Strategy Attribution (stressed cost)

| Window | Strategy | Trades | Win | Avg P&L | Total P&L | Best | Worst | PF |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| calm_bull_2019 | momentum | 26 | 38.5% | +1.20% | $62.59 | +15.34% | -5.33% | 1.67 |
| calm_bull_2019 | relative_strength | 9 | 22.2% | -1.92% | $-34.70 | +6.33% | -5.38% | 0.39 |
| calm_bull_2019 | rsi_reversion | 13 | 76.9% | +0.85% | $22.08 | +5.68% | -3.74% | 2.36 |
| covid_crash_2020 | momentum | 41 | 51.2% | +2.94% | $237.43 | +33.46% | -5.38% | 2.70 |
| covid_crash_2020 | relative_strength | 10 | 50.0% | +3.85% | $77.48 | +39.51% | -5.38% | 2.95 |
| covid_crash_2020 | rsi_reversion | 2 | 0.0% | -3.74% | $-15.49 | -3.74% | -3.74% | 0.00 |
| stimulus_bull_2021 | ma_crossover | 6 | 16.7% | -0.98% | $-11.22 | +11.72% | -3.92% | 0.61 |
| stimulus_bull_2021 | momentum | 36 | 41.7% | +0.62% | $32.96 | +28.11% | -5.38% | 1.18 |
| stimulus_bull_2021 | range_breakout | 3 | 66.7% | +0.89% | $3.29 | +6.08% | -6.01% | 1.30 |
| stimulus_bull_2021 | relative_strength | 8 | 37.5% | -1.97% | $-31.68 | +4.44% | -5.38% | 0.24 |
| stimulus_bull_2021 | rsi_reversion | 6 | 66.7% | +0.48% | $5.73 | +4.94% | -3.74% | 1.38 |
| tech_bear_2022 | ma_crossover | 8 | 25.0% | +0.40% | $6.64 | +11.77% | -4.36% | 1.16 |
| tech_bear_2022 | momentum | 20 | 25.0% | -0.73% | $-29.48 | +13.88% | -5.38% | 0.75 |
| tech_bear_2022 | relative_strength | 7 | 42.9% | +0.17% | $2.33 | +7.18% | -4.14% | 1.09 |
| tech_bear_2022 | rsi_reversion | 2 | 100.0% | +3.32% | $13.25 | +3.37% | +3.26% | inf |
| ai_reversal_2023 | ma_crossover | 6 | 33.3% | +0.26% | $4.29 | +11.72% | -4.78% | 1.15 |
| ai_reversal_2023 | momentum | 42 | 40.5% | +1.01% | $76.74 | +26.52% | -5.38% | 1.43 |
| ai_reversal_2023 | range_breakout | 6 | 0.0% | -4.32% | $-47.00 | -3.25% | -5.89% | 0.00 |
| ai_reversal_2023 | relative_strength | 13 | 38.5% | -0.26% | $-6.46 | +11.18% | -5.38% | 0.89 |
| ai_reversal_2023 | rsi_reversion | 30 | 83.3% | +1.87% | $111.84 | +6.39% | -3.74% | 3.98 |
| election_chop_2024 | ma_crossover | 11 | 18.2% | -2.86% | $-65.35 | +11.72% | -7.72% | 0.26 |
| election_chop_2024 | momentum | 30 | 33.3% | -0.24% | $-14.69 | +11.34% | -5.38% | 0.87 |
| election_chop_2024 | relative_strength | 14 | 42.9% | +0.58% | $16.00 | +13.16% | -4.94% | 1.35 |
| current_regime_auto | ma_crossover | 8 | 37.5% | +2.11% | $34.82 | +11.72% | -4.14% | 2.58 |
| current_regime_auto | momentum | 38 | 47.4% | +2.83% | $240.55 | +26.83% | -5.38% | 2.52 |
| current_regime_auto | relative_strength | 16 | 56.2% | +1.01% | $32.27 | +13.38% | -5.38% | 1.63 |
| current_regime_auto | rsi_reversion | 13 | 61.5% | +1.13% | $29.79 | +7.70% | -4.33% | 1.72 |

## Data Caveats

- calm_bull_2019 / baseline: 12 missing-history symbols (AAVE/USD, ADA/USD, AVAX/USD, BTC/USD, DOGE/USD, DOT/USD, ETH/USD, LINK/USD, LTC/USD, SOL/USD, UNI/USD, XRP/USD)
- covid_crash_2020 / baseline: 12 missing-history symbols (AAVE/USD, ADA/USD, AVAX/USD, BTC/USD, DOGE/USD, DOT/USD, ETH/USD, LINK/USD, LTC/USD, SOL/USD, UNI/USD, XRP/USD)
- stimulus_bull_2021 / baseline: 3 missing-history symbols (ADA/USD, DOT/USD, XRP/USD)
- tech_bear_2022 / baseline: 3 missing-history symbols (ADA/USD, DOT/USD, XRP/USD)
- ai_reversal_2023 / baseline: 2 missing-history symbols (ADA/USD, XRP/USD)
- election_chop_2024 / baseline: 1 missing-history symbols (ADA/USD)
- calm_bull_2019 / stressed: 12 missing-history symbols (AAVE/USD, ADA/USD, AVAX/USD, BTC/USD, DOGE/USD, DOT/USD, ETH/USD, LINK/USD, LTC/USD, SOL/USD, UNI/USD, XRP/USD)
- covid_crash_2020 / stressed: 12 missing-history symbols (AAVE/USD, ADA/USD, AVAX/USD, BTC/USD, DOGE/USD, DOT/USD, ETH/USD, LINK/USD, LTC/USD, SOL/USD, UNI/USD, XRP/USD)
- stimulus_bull_2021 / stressed: 3 missing-history symbols (ADA/USD, DOT/USD, XRP/USD)
- tech_bear_2022 / stressed: 3 missing-history symbols (ADA/USD, DOT/USD, XRP/USD)
- ai_reversal_2023 / stressed: 2 missing-history symbols (ADA/USD, XRP/USD)
- election_chop_2024 / stressed: 1 missing-history symbols (ADA/USD)
- calm_bull_2019 / severe: 12 missing-history symbols (AAVE/USD, ADA/USD, AVAX/USD, BTC/USD, DOGE/USD, DOT/USD, ETH/USD, LINK/USD, LTC/USD, SOL/USD, UNI/USD, XRP/USD)
- covid_crash_2020 / severe: 12 missing-history symbols (AAVE/USD, ADA/USD, AVAX/USD, BTC/USD, DOGE/USD, DOT/USD, ETH/USD, LINK/USD, LTC/USD, SOL/USD, UNI/USD, XRP/USD)
- stimulus_bull_2021 / severe: 3 missing-history symbols (ADA/USD, DOT/USD, XRP/USD)
- tech_bear_2022 / severe: 3 missing-history symbols (ADA/USD, DOT/USD, XRP/USD)
- ai_reversal_2023 / severe: 2 missing-history symbols (ADA/USD, XRP/USD)
- election_chop_2024 / severe: 1 missing-history symbols (ADA/USD)

Backtest semantics:
- Momentum volume-pace backtest uses a synthetic elapsed-session volume proxy from daily bars, not real minute bars; volume-pace fills are not intraday-validated. Symbols: AAPL, ABBV, ABNB, ADBE, AMAT, AMD, AMGN, AMT, AMZN.

Raw JSON artifacts: `reports/walkforward/master_20260609T084950Z`

Locked OOS note: the latest held-out period is intentionally excluded from the master run. Run `python3 scheduler/run_walkforward.py --profile master --oos-only` when ready for final validation.
