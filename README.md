# HawksTrade

![HawksTrade - The Digital Sentinel](assets/hawkstrade_sentinel.webp)

**Automated swing trading bot for US stocks and crypto, powered by Alpaca Markets.**

Ships with 5 independent strategies, enables the validated core set by default,
enforces strict risk rules, and is designed to be operated autonomously by an AI agent.

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
python3 scheduler/run_backtest.py --days 365 --fund 10000 --screener
```

---

## Backtesting & Performance

HawksTrade includes a high-fidelity historical simulator. The current default strategy set achieved **+19.00% annual return** in the 12-month backtest ending 2026-04-10 on $10,000 starting capital, with the configured 5% max-position risk cap enforced.

- **Backtest Summary**: [backtests.md](backtests.md)
- **Configuration Guide**: [config.md](config.md)
- **Features**: Split-adjusted data, portfolio compounding, and per-strategy attribution.

---

## Strategy Logic

| Strategy | Market | Key Parameters | Approach |
|----------|--------|----------------|----------|
| **Momentum** | US Stocks | Top 3 by 5-day return, min 6% momentum, profit-aware exit | Captures high-velocity rallies, exits flat/losing trades after the minimum hold, and lets profitable trades run under trailing protection. |
| **RSI Reversion** | US Stocks | Disabled by default; RSI < 38, SMA-200 within 15%, vol spike 1.5x, 2-bar recovery | Mean reversion with volume confirmation and consecutive higher-close gate. |
| **Gap-Up** | US Stocks | Disabled by default; 3% gap, high volume, SMA-200 trend | Gap plays on strong trend confirmation. |
| **EMA Crossover** | Crypto | 9/21 EMA, RSI 35-70, slope + volatility filters | Bullish EMA crossover with BTC regime gate. |
| **Range Breakout** | Crypto | Prior-day high breakout, 1.8x volume, EMA-50 trend | Breakout entries with BTC regime gate and volume confirmation. |

**Crypto Universe**: `BTC/USD`, `SOL/USD`, `LINK/USD`, `DOGE/USD`, `LTC/USD`, `DOT/USD`.

### Market Regime Filters

- **SPY SMA-50 (Stocks)**: All stock strategies (Momentum, RSI Reversion, Gap-Up) are gated by SPY trading above its 50-day SMA. When SPY is below SMA-50 (bear regime), stock scans are skipped.
- **BTC EMA-20 (Crypto)**: EMA Crossover and Range Breakout strategies are gated by BTC/USD trading above its 20-day EMA. When BTC is below EMA-20 (crypto bear regime), crypto scans are skipped.

Both filters fail open (return True) if data is unavailable, ensuring the system doesn't halt on data issues.

### Kelly Criterion Dynamic Position Sizing

Momentum strategy uses Half-Kelly position sizing with parameters derived dynamically from the last 30 closed momentum trades. When fewer than 10 trades are available, it falls back to hardcoded defaults (WR=0.567, avg_win=14.0%, avg_loss=5.4%). Position size is capped by `trading.max_position_pct` and currently cannot exceed 5% of portfolio.

### Momentum Exit Policy

Momentum uses `exit_policy: profit_trailing` by default. After the 4-trading-day minimum hold, flat or losing trades are exited, profitable trades can continue, and a trailing stop exits trades that fall 4% from a post-entry peak after reaching a 6% peak gain. Backtests can compare policies with:

```bash
python3 scheduler/run_backtest.py --days 365 --exit-policy fixed_hold
python3 scheduler/run_backtest.py --days 365 --exit-policy profit_trailing
python3 scheduler/run_backtest.py --days 365 --exit-policy risk_only_baseline
```

Use `--no-screener` to backtest only the fixed configured stock universe, or `--screener` to force the dynamic screener. Use `--strategies` and `--set` for backtest-only experiments without editing the live config:

```bash
python3 scheduler/run_backtest.py --days 365 --fund 10000 --screener \
  --strategies momentum,ma_crossover,range_breakout \
  --set strategies.momentum.top_n=3 \
  --set strategies.momentum.min_momentum_pct=0.06
```

---

## Risk Controls (Tuned)

- **Asymmetric Reward**: 3.5% stop-loss / 12% take-profit.
- **Capital Protection**: SMA-based trend filters on all strategies.
- **Position Limits**: Max 5% of portfolio per trade, cap of 10 concurrent positions.
- **Daily Guardrail**: 5% daily loss limit (hard stop for the day).

---

## Configuration

All settings are in `config/config.yaml`. See [config.md](config.md) for the available configuration options and the recommended backtest-backed profile. Toggle strategies, adjust risk, or switch between `paper` and `live` modes only when you intend to revalidate those changes.

---

## Scheduling

Operational schedules are documented in [scheduler/README.md](scheduler/README.md). That directory includes templates for macOS `launchd`, Linux `cron`, and Windows Task Scheduler.

---

## Cloud Deployment

For running HawksTrade on AWS EC2 with IAM-based secrets management (no keys on disk), see [cloud-setup/aws-setup.md](cloud-setup/aws-setup.md).

---

## Project Structure

```
HawksTrade/
├── config/            ← config.yaml + .env.example
├── core/              ← Alpaca client, risk manager, order executor
├── strategies/        ← Momentum, RSI, Gap-Up, EMA, Breakout
├── scheduler/         ← Scanner, risk check, backtester, scheduler templates
├── tracking/          ← Trade logs and performance metrics
└── assets/            ← Generated equity curves and branding
```

---

## Disclaimer

Trading involves significant risk. This software is for educational use. Past performance (backtests) does not guarantee future results. Start with paper trading.

## License

HawksTrade uses a dual-license model:

- Open-source use: GNU Affero General Public License v3.0 or later (AGPL-3.0-or-later)
- Proprietary/closed-source use: separate commercial license by written agreement — contact [bait.wall.store@staycloaked.com](mailto:bait.wall.store@staycloaked.com)

See [LICENSE](./LICENSE) and [LICENSE-AGPL](./LICENSE-AGPL) for full details.
