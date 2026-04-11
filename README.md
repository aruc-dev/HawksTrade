# HawksTrade

![HawksTrade - The Digital Sentinel](assets/hawkstrade_sentinel.webp)

**Automated swing trading bot for US stocks and crypto, powered by Alpaca Markets.**

Runs 5 independent strategies, enforces strict risk rules, and is designed to be
operated autonomously by an AI agent (Claude, Codex, or any AI with shell access).

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt --break-system-packages

# 2. Set up your API keys
cp config/.env.example config/.env
# Edit config/.env and fill in your Alpaca keys.
# A root .env file is also supported and takes precedence.

# 3. Verify connection (paper trading is default)
python -c "
import sys; sys.path.insert(0,'.')
from core.alpaca_client import get_account
print('Connected:', get_account().portfolio_value)
"

# 4. Run your first scan
python scheduler/run_scan.py --dry-run

# 5. Check risk / stop-losses
python scheduler/run_risk_check.py --dry-run

# 6. Generate a report
python scheduler/run_report.py
```

---

## Pre-Push Validation

Before pushing or deploying changes:

```bash
python3 -m unittest discover -v
python3 -W error::DeprecationWarning -m unittest discover
python3 -m compileall core strategies scheduler tracking tests
python3 scheduler/run_scan.py --dry-run
python3 scheduler/run_risk_check.py --dry-run
python3 scheduler/run_report.py
```

`--dry-run` validates scan and risk paths without submitting orders. A real paper-order
lifecycle test should only be run intentionally, because it creates and closes a simulated
Alpaca paper position.

---

## Strategies

| Strategy | Market | Approach |
|----------|--------|----------|
| Momentum | US Stocks | Top 5 by 5-day return, hold 4 days |
| RSI Reversion | US Stocks | Buy oversold (RSI<30), sell overbought (RSI>60) |
| Gap-Up | US Stocks | Gap >3% on high volume, swing hold |
| EMA Crossover | Crypto | 9-EMA / 21-EMA crossover on daily bars |
| Range Breakout | Crypto | Prior-day high breakout with volume confirmation |

Configured crypto universe: `BTC/USD`, `ETH/USD`, `SOL/USD`, `AVAX/USD`, `LINK/USD`, `POL/USD`.

---

## Risk Controls

- Max 5% of portfolio per position
- 2% stop-loss / 8% take-profit per trade
- 5% daily loss limit → all trading halts
- Max 10 concurrent open positions
- **Intraday trading disabled by default** (swing trades only)

---

## Configuration

All settings are in `config/config.yaml`:
- Switch between `paper` and `live` mode
- Enable/disable individual strategies
- Adjust position sizing, stop-loss, universe of stocks/crypto

API credentials are loaded from `config/.env` and then root `.env` if present. Do not commit
either file.

---

## For AI Agents

See `CLAUDE.md` for Claude-specific instructions.
See `AGENTS.md` for the universal agent operating manual.
See `TESTING.md` for validation, dry-run, and paper-order lifecycle checks.

---

## Project Structure

```
HawksTrade/
├── CLAUDE.md          ← AI agent instructions (Claude)
├── AGENTS.md          ← AI agent instructions (universal)
├── config/            ← config.yaml + .env.example
├── core/              ← Alpaca client, risk manager, order executor, portfolio
├── strategies/        ← 5 trading strategies
├── scheduler/         ← Scripts called on schedule
├── tracking/          ← Trade log + performance analytics
├── data/              ← trades.csv, performance.csv
├── reports/           ← Generated daily/weekly reports
└── logs/              ← Daily log files
```

`data/`, `reports/`, and `logs/` are generated at runtime and are ignored by git.

---

## Disclaimer

This software is for educational and personal use only.
Trading involves significant risk of financial loss.
Past strategy performance does not guarantee future results.
Always start with paper trading and review results before using real money.
