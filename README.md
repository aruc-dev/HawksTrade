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

HawksTrade includes a high-fidelity historical simulator. The current tail-risk-hardened default strategy set achieved **+20.70% annual return** in the 12-month backtest through 2026-04-30 on $10,000 starting capital, with the configured 8% max-position risk cap enforced.

- **Backtest Summary**: [backtests.md](backtests.md)
- **Configuration Guide**: [config.md](config.md)
- **Features**: Split-adjusted data, portfolio compounding, and per-strategy attribution.

---

## Strategy Logic

| Strategy | Market | Key Parameters | Approach |
|----------|--------|----------------|----------|
| **Momentum** | US Stocks | Top 2 by 5-day return in green regimes, min 8% momentum, 2.0x volume spike, 65% breadth coverage, 1.2x ATR stop extension, profit-aware exit | Captures high-conviction rallies with a moderate opportunity increase while yellow regimes remain capped at one signal. |
| **RSI Reversion** | US Stocks | Enabled; RSI < 40, %B < 20%, SMA-200 within +/-15%, 0.7x volume confirmation, 1-bar recovery, 5-day drawdown <= 10%, ATR/price <= 5%, 0.8x ATR stop capped at 6% | Mean reversion with crash, realised-volatility, and tail-loss guards. |
| **Gap-Up** | US Stocks | Enabled; true 5-15% opening gap, 1.3x opening-volume pace, 65% breadth guard, prior close above SMA-200, <=35% SMA-200 extension, top-1 ranked signal, 2-day hold, failed-gap exit | Opening momentum sleeve with completed-bar trend confirmation, minute-bar entry confirmation, and ATR-risk sizing. |
| **EMA Crossover** | Crypto | 6/18 EMA, latest completed cross only, top-1 ranked signal, RSI 35-75, slope + volatility filters, 3-day drawdown guard, price/EMA confirmation, 2% daily-close max-loss exit, 16-day hold cap | Bullish EMA crossover with BTC regime gate and tighter same-scan concentration control. |
| **Range Breakout** | Crypto | Enabled; 20-day high close breakout, 2.5x volume, rising EMA-50, RSI, 0.8%-8% breakout-extension, and upper-range close guards | Ranked Donchian-style breakout sleeve with failed-breakout and trend-loss exits. |

**Crypto Universe**: `BTC/USD`, `SOL/USD`, `LINK/USD`, `DOGE/USD`, `LTC/USD`, `DOT/USD`.

### Market Regime Filters

- **Stock Regime Guards**: Momentum and Gap-Up use the SPY/QQQ SMA-50 regime gate. RSI Reversion has separate crash and realised-volatility filters.
- **BTC EMA-20 (Crypto)**: EMA Crossover and Range Breakout are gated by BTC/USD trading above its 20-day EMA.

Live/paper scans fail closed when regime data is unavailable or insufficient, blocking new entries until the bot can confirm market conditions. Backtests still allow early warmup periods with insufficient bars so simulations can start before every long-window filter is populated.

### Strategy Position Sizing

Momentum, RSI Reversion, Gap-Up, EMA Crossover, and Range Breakout emit ATR-risk quantities that target 1% account risk per trade before the global 8% max-position cap is applied. Momentum still has a Half-Kelly fallback in the executor if a signal does not include ATR sizing, but the current strategy path provides ATR-risk sizing by default.

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
  --strategies momentum,rsi_reversion,gap_up,ma_crossover,range_breakout \
  --set strategies.momentum.top_n=2 \
  --set strategies.momentum.min_momentum_pct=0.08 \
  --set strategies.momentum.volume_spike_ratio=2.0 \
  --set strategies.momentum.min_breadth_coverage_pct=0.65 \
  --set strategies.ma_crossover.fast_ema=6 \
  --set strategies.ma_crossover.slow_ema=18 \
  --set strategies.ma_crossover.max_signals=1 \
  --set strategies.range_breakout.volume_multiplier=2.5
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

Gap-Up and Range Breakout are enabled in the active profile. Continue using
their dedicated gates before scaling either sleeve:

```bash
python3 scheduler/run_validation_gate.py --profile gap
```

```bash
python3 scheduler/run_validation_gate.py --profile range
```

---

## Risk Controls (Tuned)

- **Asymmetric Reward**: 3.5% stop-loss / 12% take-profit.
- **Capital Protection**: SMA-based trend filters on all strategies.
- **Strategy-Local Loss Defense**: Momentum and RSI use less-permissive ATR stop extensions on top of the global stop layer, RSI blocks high-ATR and unresolved waterfall entries and exits daily closes 6% below entry, Gap-Up exits failed continuations, and MA Crossover exits on a daily close at least 2% below entry.
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
