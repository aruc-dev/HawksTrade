# HawksTrade

![HawksTrade - The Digital Sentinel](assets/hawkstrade_sentinel.webp)

**Automated swing trading bot for US stocks and crypto, powered by Alpaca Markets.**

Runs 5 independent strategies, enforces strict risk rules, and is designed to be
operated autonomously by an AI agent.

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt --break-system-packages

# 2. Set up your API keys
cp config/.env.example config/.env
# Edit config/.env and fill in your Alpaca keys.

# 3. Verify connection
python3 -c "import sys; sys.path.insert(0,'.'); from core.alpaca_client import get_account; print('Connected:', get_account().portfolio_value)"

# 4. Run a backtest (12 months)
python3 scheduler/run_backtest.py --days 365 --fund 10000 --output backtests.md
```

---

## Backtesting & Performance

HawksTrade includes a high-fidelity historical simulator. The latest "Prplxty" version achieved **+26.30% annual return** in backtesting.

- **View Full Report**: [backtests.md](backtests.md)
- **Features**: Split-adjusted data, portfolio compounding, and per-strategy attribution.

---

## Strategies

| Strategy | Market | Approach |
|----------|--------|----------|
| **Momentum** | US Stocks | Top 5 by 5-day return, captures high-velocity tech rallies. |
| **RSI Reversion** | US Stocks | Mean reversion on RSI < 35 with SMA-200 trend filter. |
| **Gap-Up** | US Stocks | 3% gap at open on high volume + SMA-200 trend confirmation. |
| **EMA Crossover** | Crypto | 9/21 EMA crossover with RSI (35-70) momentum filter. |
| **Range Breakout** | Crypto | 20-day high breakout on 1.5x volume + SMA-50 filter. |

**Crypto Universe**: `BTC`, `ETH`, `SOL`, `AVAX`, `LINK`, `POL`, `DOGE`, `LTC`, `DOT`.

---

## Risk Controls (Tuned)

- **Asymmetric Reward**: 3.5% stop-loss / 12% take-profit.
- **Capital Protection**: SMA-based trend filters on all strategies.
- **Position Limits**: Max 5% of portfolio per trade, cap of 10 concurrent positions.
- **Daily Guardrail**: 5% daily loss limit (hard stop for the day).

---

## Configuration

All settings are in `config/config.yaml`. Toggle strategies, adjust risk, or switch between `paper` and `live` modes.

---

## Project Structure

```
HawksTrade/
├── config/            ← config.yaml + .env.example
├── core/              ← Alpaca client, risk manager, order executor
├── strategies/        ← Momentum, RSI, Gap-Up, EMA, Breakout
├── scheduler/         ← Scanner, Risk Check, Backtester
├── tracking/          ← Trade logs and performance metrics
└── assets/            ← Generated equity curves and branding
```

---

## Disclaimer

Trading involves significant risk. This software is for educational use. Past performance (backtests) does not guarantee future results. Start with paper trading.
