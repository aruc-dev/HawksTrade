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

## Strategy Logic

| Strategy | Market | Key Parameters | Approach |
|----------|--------|----------------|----------|
| **Momentum** | US Stocks | Top 5 by 5-day return, Kelly sizing | Captures high-velocity tech rallies with dynamic Half-Kelly position sizing. |
| **RSI Reversion** | US Stocks | RSI < 30, SMA-200 within 15%, vol spike 1.5x, 2-bar recovery | Mean reversion with volume confirmation and consecutive higher-close gate. |
| **Gap-Up** | US Stocks | 3% gap, high volume, SMA-200 trend | Intraday gap plays on strong trend confirmation. |
| **EMA Crossover** | Crypto | 9/21 EMA, RSI 35-70, slope + volatility filters | Bullish EMA crossover with BTC regime gate. |
| **Range Breakout** | Crypto | Prior-day high breakout, 1.5x volume, EMA-50 trend | Breakout entries with BTC regime gate and volume confirmation. |

**Crypto Universe**: `BTC`, `ETH`, `SOL`, `AVAX`, `LINK`, `POL`, `DOGE`, `LTC`, `DOT`.

### Market Regime Filters

- **SPY SMA-50 (Stocks)**: All stock strategies (Momentum, RSI Reversion, Gap-Up) are gated by SPY trading above its 50-day SMA. When SPY is below SMA-50 (bear regime), stock scans are skipped.
- **BTC EMA-20 (Crypto)**: EMA Crossover and Range Breakout strategies are gated by BTC/USD trading above its 20-day EMA. When BTC is below EMA-20 (crypto bear regime), crypto scans are skipped.

Both filters fail open (return True) if data is unavailable, ensuring the system doesn't halt on data issues.

### Kelly Criterion Dynamic Position Sizing

Momentum strategy uses Half-Kelly position sizing with parameters derived dynamically from the last 30 closed momentum trades. When fewer than 10 trades are available, it falls back to hardcoded defaults (WR=0.567, avg_win=14.0%, avg_loss=5.4%). Position size is capped at 8% of portfolio and floored at 1%.

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
