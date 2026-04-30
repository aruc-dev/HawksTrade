# HawksTrade

![HawksTrade Brand](assets/brand/hawkstrade-brand.png)

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

HawksTrade includes a high-fidelity historical simulator. The current default strategy set achieved **+12.12% annual return** in the 12-month backtest ending 2026-04-10 on $10,000 starting capital, with the configured 8% max-position risk cap enforced.

- **Backtest Summary**: [backtests.md](backtests.md)
- **Configuration Guide**: [config.md](config.md)
- **Features**: Split-adjusted data, portfolio compounding, and per-strategy attribution.

---

## Strategy Logic

| Strategy | Market | Key Parameters | Approach |
|----------|--------|----------------|----------|
| **Momentum** | US Stocks | Top 1 by 5-day return, min 10% momentum, 1.8x volume spike, 75% breadth coverage, profit-aware exit | Captures only high-conviction rallies, exits flat/losing trades after the minimum hold, and lets profitable trades run under trailing protection. |
| **RSI Reversion** | US Stocks | Enabled; RSI < 30, %B < 20%, SMA-200 within +/-15%, vol spike 1.5x, 1-bar recovery | Conservative mean reversion with crash and realised-volatility regime guards. |
| **Gap-Up** | US Stocks | Disabled by default; 3% gap, high volume, SMA-200 trend | Gap plays on strong trend confirmation. |
| **EMA Crossover** | Crypto | 9/21 EMA, 2-day recent-cross window, RSI 35-70, slope + volatility filters, 1% daily-close max-loss exit | Bullish EMA crossover with BTC regime gate and tighter strategy-level capital defense. |
| **Range Breakout** | Crypto | Disabled; prior-day high close breakout, 1.8x volume, rising EMA-50, RSI/extension guards | Ranked breakout implementation remains available, but is not part of the active default strategy set. |

**Crypto Universe**: `BTC/USD`, `SOL/USD`, `LINK/USD`, `DOGE/USD`, `LTC/USD`, `DOT/USD`.

### Market Regime Filters

- **Stock Regime Guards**: Momentum and Gap-Up use the SPY/QQQ SMA-50 regime gate. RSI Reversion has separate crash and realised-volatility filters.
- **BTC EMA-20 (Crypto)**: The active EMA Crossover crypto strategy is gated by BTC/USD trading above its 20-day EMA. Range Breakout shares this gate if re-enabled later.

Live/paper scans fail closed when regime data is unavailable or insufficient, blocking new entries until the bot can confirm market conditions. Backtests still allow early warmup periods with insufficient bars so simulations can start before every long-window filter is populated.

### Strategy Position Sizing

Momentum, RSI Reversion, and EMA Crossover emit ATR-risk quantities that target 1% account risk per trade before the global 8% max-position cap is applied. Range Breakout also emits ATR-risk quantities if re-enabled later. Momentum still has a Half-Kelly fallback in the executor if a signal does not include ATR sizing, but the current strategy path provides ATR-risk sizing by default.

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
  --strategies momentum,rsi_reversion,ma_crossover \
  --set strategies.momentum.top_n=1 \
  --set strategies.momentum.min_momentum_pct=0.10 \
  --set strategies.momentum.volume_spike_ratio=1.8 \
  --set strategies.momentum.min_breadth_coverage_pct=0.75 \
  --set strategies.ma_crossover.max_loss_exit_pct=0.01
```

Before scaling live capital, run the cost-aware validation gate. It applies the
configured slippage/fee assumptions, checks 12-month, 6-month, and crypto-sleeve
windows, and reports watch-only warnings for weak recent crypto windows:

```bash
python3 scheduler/run_validation_gate.py --profile production
```

RSI Reversion is enabled in the active default profile. Use its dedicated gate as an ongoing monitoring check before scaling its capital allocation:

```bash
python3 scheduler/run_validation_gate.py --profile rsi
```

---

## Risk Controls (Tuned)

- **Asymmetric Reward**: 3.5% stop-loss / 12% take-profit.
- **Capital Protection**: SMA-based trend filters on all strategies.
- **Strategy-Local Loss Defense**: MA Crossover exits on a daily close at least 1% below entry, reducing crypto trend-tail losses before the global stop layer is needed.
- **Position Limits**: Max 8% of portfolio per trade, cap of 10 concurrent positions.
- **Daily Guardrail**: 5% daily loss limit (hard stop for the day), keyed to the `America/New_York` trading-session date so UTC cloud hosts do not reset the baseline at UTC midnight. The baseline is the first observed account value for that trading date and is persisted in `data/daily_loss_baseline.json`; it is not reconstructed from the prior close.
- **Broker Resilience**: Alpaca timeouts, rate limits, and 5xx outages use bounded retry; auth failures, not-found responses, and broker rejections are classified for fail-closed logging.
- **Price-Fetch Visibility**: Risk checks track consecutive latest-price failures per open position and surface repeated failures as `[NOK]` in the Linux health dashboard.
- **Trade-Log Reconciliation**: Scheduled scans, risk checks, reports, and health checks reconcile `data/trades.csv` with broker positions when Alpaca is reachable.
- **Health Alerts**: Linux health checks write `reports/alerts/health_alert_latest.txt`, timestamped alert files for `[NOK]` states, and can POST alerts to `HAWKSTRADE_HEALTH_ALERT_WEBHOOK_URL`.
- **Health Snapshots**: Linux health checks persist timestamped HTML/JSON snapshots in `reports/health_snapshots/` with retention pruning for recent operational history.

---

## Configuration

All settings are in `config/config.yaml`. See [config.md](config.md) for the available configuration options and the recommended backtest-backed profile. Toggle strategies, adjust risk, or switch between `paper` and `live` modes only when you intend to revalidate those changes.

For machine-local configuration (e.g. switching to `live` on a specific host without touching the committed file), create `config/config.local.yaml`. When present it is deep-merged over `config/config.yaml`, so it only needs the keys you want to override. This file is gitignored and never committed.

---

## Scheduling

Operational schedules are documented in [scheduler/README.md](scheduler/README.md). That directory includes templates for macOS `launchd`, Linux `cron`, and Windows Task Scheduler.

---

## Cloud Deployment

For running HawksTrade on AWS EC2 with IAM-based secrets management (no keys on disk), see [cloud-setup/aws-setup.md](cloud-setup/aws-setup.md).

### Optional Read-Only Dashboard

HawksTrade can optionally expose a **read-only** operational dashboard for:

- account value, cash, buying power, and open positions
- realized/unrealized P&L snapshots
- recent closed trades and strategy summaries
- Linux health status, cron/systemd execution health, and recent log issues

This dashboard is intentionally separate from trading execution:

- it does **not** place trades, cancel orders, or change config
- it uses a dedicated dashboard service and separate pinned dependencies in
  [requirements-dashboard.txt](requirements-dashboard.txt)
- it is designed to run on the EC2 host only, with loopback binding and
  authentication in front of it

Supported optional deployment modes:

1. **Local-only over SSH tunnel**
   Use `DASHBOARD_AUTH_MODE=local` and access it only through an SSH tunnel to
   `127.0.0.1:8080`.
2. **Cloudflare Tunnel + Cloudflare Access**
   Use `DASHBOARD_AUTH_MODE=cloudflare` for authenticated remote/mobile access
   without opening an inbound port on the EC2 instance.

The dashboard setup is documented separately because it is optional operational
infrastructure, not required for the trading bot itself:

- [cloud-setup/dashboard-setup.md](cloud-setup/dashboard-setup.md)

If you are using the systemd-based EC2 deployment, install the core bot first,
then add the dashboard on top as an optional extra.

---

## Project Structure

```
HawksTrade/
├── config/            ← config.yaml + .env.example (config.local.yaml optional, gitignored)
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
