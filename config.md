# HawksTrade Configuration Guide

> **Updated:** April 29, 2026
> **Primary config file:** `config/config.yaml`
> **Local config:** `config/config.local.yaml` — if present, deep-merged over `config/config.yaml`. Include only the keys you want to override. Gitignored; use for per-machine settings without modifying the committed file.
> **Recommended profile:** growth-oriented paper trading profile validated by the latest 12-month backtest.

This guide explains the available user-facing configuration sections and the currently recommended defaults. Do not switch `mode` to `live` or change risk parameters unless you explicitly intend to accept the added trading risk.

---

## Recommended Configuration

The latest validated default configuration is:

| Area | Recommended Setting | Reason |
|---|---|---|
| Trading mode | `mode: paper` | Paper trading should remain the default until live trading is explicitly approved. |
| Intraday trading | `intraday.enabled: false` | The system is validated as a swing-trading bot. |
| Screener | `screener.enabled: true` | The tightened screener improved 12-month return versus the old screener and recent fixed-universe test. |
| Momentum | enabled, `top_n: 1`, `min_momentum_pct: 0.10`, `volume_spike_ratio: 1.8`, `min_breadth_coverage_pct: 0.75` | Focuses stock exposure on the single strongest high-volume momentum candidate and blocks entries when breadth data coverage is too thin. |
| RSI Reversion | disabled | Did not improve the recommended 12-month configuration. |
| Gap-Up | disabled | Did not improve the recommended 12-month configuration. |
| MA Crossover | enabled, `max_loss_exit_pct: 0.01` | Positive crypto contribution in the latest 12-month backtest with a tighter daily-close loss exit to preserve capital. |
| Range Breakout | enabled | Hardened crypto breakout sleeve with ranked signals, ATR-risk sizing, and failed-breakout exits; roughly flat contribution in the latest 12-month reproduction. |
| Momentum exit policy | `profit_trailing` | Exits flat/losing trades after the minimum hold while allowing winners to run under trailing protection. |

Latest recommended 12-month result:

| Final Value | Return | Trades | Win Rate | Max Drawdown |
|---:|---:|---:|---:|---:|
| $10,751.62 | +7.52% | 72 | 44.4% | -1.73% |

These results enforce `trading.max_position_pct: 0.05` for all entries, including momentum/Kelly sizing, and include the hardened Range Breakout implementation. No global stop-loss, position-size, or daily-loss-limit risk parameter was increased.

See [backtests.md](backtests.md) for the full comparison.

---

## Trading Mode

```yaml
mode: paper
```

| Value | Meaning |
|---|---|
| `paper` | Uses Alpaca paper trading. Recommended default. |
| `live` | Uses Alpaca live trading. Real money. Do not enable without explicit approval. |

---

## Alpaca Endpoints

```yaml
alpaca:
  paper_base_url: "https://paper-api.alpaca.markets"
  live_base_url: "https://api.alpaca.markets"
  data_base_url: "https://data.alpaca.markets"
  crypto_data_url: "https://data.alpaca.markets"
```

These usually do not need changes. API keys belong in `.env` or `config/.env`, not in `config/config.yaml`.

---

## Intraday Trading

```yaml
intraday:
  enabled: false
```

Recommended: `false`.

This bot is validated as a swing-trading system. Enabling intraday changes behavior and should be treated as a separate strategy experiment.

---

## Trading Risk Controls

```yaml
trading:
  max_positions: 10
  max_position_pct: 0.05
  stop_loss_pct: 0.035
  take_profit_pct: 0.12
  daily_loss_limit_pct: 0.05
  min_trade_value_usd: 100
  order_type: "limit"
  limit_slippage_pct: 0.001
```

| Setting | Meaning | Current Default |
|---|---|---:|
| `max_positions` | Max concurrent open positions | 10 |
| `max_position_pct` | Max portfolio allocation per trade | 5% |
| `stop_loss_pct` | Per-position stop-loss from entry | 3.5% |
| `take_profit_pct` | Per-position take-profit from entry | 12% |
| `daily_loss_limit_pct` | Daily account-level loss halt | 5% |
| `min_trade_value_usd` | Minimum order notional | $100 |
| `order_type` | `limit` or `market` | `limit` |
| `limit_slippage_pct` | Limit price offset for fast fills | 0.1% |

These are risk parameters. Keep them unchanged unless you are deliberately revalidating risk.

---

## Stock Universe

```yaml
stocks:
  scan_universe:
    - AAPL
    - MSFT
    - GOOGL
    - AMZN
    - NVDA
    - META
    - TSLA
    - AMD
    - NFLX
    - JPM
    - BAC
    - GS
    - XOM
    - CVX
    - SPY
    - QQQ
    - ARKK
    - SOFI
    - PLTR
    - COIN
    - ORCL
    - CRM
    - SOUN
    - AI
    - IONQ
    - SMCI
    - ARM
    - AVGO
    - INTC
    - TSM
    - IBM
```

This fixed universe is always merged into the dynamic screener output. It is also the complete stock universe when running with `--no-screener`.

---

## Dynamic Screener

```yaml
screener:
  enabled: true
  min_adv_shares: 1000000
  min_adv_dollars: 50000000
  min_price: 10.0
  max_price: 2000.0
  min_atr_pct: 0.012
  max_atr_pct: 0.06
  target_atr_pct: 0.03
  trend_sma_days: 50
  min_trend_sma_ratio: 1.0
  max_trend_sma_ratio: 1.30
  min_20d_return_pct: -0.05
  max_20d_return_pct: 0.35
  max_universe: 40
```

| Setting | Meaning |
|---|---|
| `enabled` | Enables dynamic stock universe selection. |
| `min_adv_shares` | Minimum 20-day average share volume. |
| `min_adv_dollars` | Minimum 20-day average dollar volume. |
| `min_price`, `max_price` | Price bounds. |
| `min_atr_pct`, `max_atr_pct` | Filters out too-flat and too-volatile symbols. |
| `target_atr_pct` | Scores candidates closer to moderate volatility. |
| `trend_sma_days` | Trend window for the stock screener. |
| `min_trend_sma_ratio` | Requires price at or above the trend SMA. |
| `max_trend_sma_ratio` | Avoids overextended names far above the trend SMA. |
| `min_20d_return_pct` | Avoids recent breakdowns. |
| `max_20d_return_pct` | Avoids blow-off moves. |
| `max_universe` | Caps dynamic candidates before merging the fixed universe. |

Recommended: keep enabled for the growth profile. Use `--no-screener` in backtests when comparing a lower-drawdown fixed-universe profile.

---

## Crypto Universe

```yaml
crypto:
  scan_universe:
    - BTC/USD
    - SOL/USD
    - LINK/USD
    - DOGE/USD
    - LTC/USD
    - DOT/USD
```

These pairs are used by the crypto strategies. Crypto scans can run 24/7.

---

## Strategies

### Momentum

```yaml
momentum:
  enabled: true
  asset_class: stocks
  top_n: 1
  hold_days: 4
  exit_policy: "profit_trailing"
  profit_floor_pct: 0.0
  trail_activation_pct: 0.06
  trailing_stop_pct: 0.04
  max_hold_days: 20
  min_momentum_pct: 0.10
  min_alpha_pct: 0.0
  min_breadth_coverage_pct: 0.75
  volume_spike_ratio: 1.8
```

Recommended: enabled.

Momentum is the primary stock contributor. The stricter `top_n: 1`, `min_momentum_pct: 0.10`, `volume_spike_ratio: 1.8`, and `min_breadth_coverage_pct: 0.75` settings reduced churn, improved win rate, and cut drawdown in the validated default profile.

### RSI Reversion

```yaml
rsi_reversion:
  enabled: false
  rsi_period: 14
  oversold_threshold: 30
  overbought_threshold: 50
  hold_days: 10
```

Recommended: disabled.

Keep available for experiments, but it is not part of the recommended default based on the latest backtests.

### Gap-Up

```yaml
gap_up:
  enabled: false
  min_gap_pct: 0.03
  volume_multiplier: 1.5
  entry_window_minutes: 45
  intraday_exit: false
  hold_days: 2
```

Recommended: disabled.

Keep available for experiments, but it is not part of the recommended default based on the latest backtests.

### MA Crossover

```yaml
ma_crossover:
  enabled: true
  asset_class: crypto
  fast_ema: 9
  slow_ema: 21
  timeframe: "1Day"
  hold_days: 12
  max_loss_exit_pct: 0.01
```

Recommended: enabled.

This strategy contributed positively in the latest recommended 12-month backtest. The strategy-level max-loss exit closes the position when the latest daily close is at least 1% below entry, which reduced the largest observed 12-month MA Crossover loss in validation.

### Range Breakout

```yaml
range_breakout:
  enabled: true
  asset_class: crypto
  breakout_pct: 0.008
  max_breakout_extension_pct: 0.08
  volume_multiplier: 1.8
  volume_avg_period: 20
  timeframe: "1Day"
  hold_days: 3
  atr_period: 14
  atr_multiplier: 2.0
  risk_per_trade_pct: 0.01
  vol_filter_period: 10
  min_range_ratio: 0.5
  trend_ema_period: 50
  trend_slope_lookback: 5
  rsi_period: 14
  rsi_entry_max: 78
  rsi_exit_max: 82
  profit_floor_pct: 0.03
  breakdown_exit_pct: 0.02
  trend_exit_enabled: true
```

Recommended: enabled.

This strategy is the production crypto breakout sleeve. It now uses confirmed daily close breakouts, ranked signal selection, ATR-risk sizing, and explicit failed-breakout exits before the 3-day hold cap.

---

## Scheduling

```yaml
schedule:
  stock_scan_interval_min: 30
  crypto_scan_interval_min: 60
  risk_check_interval_min: 15
  daily_report_time: "16:30"
  weekly_report_day: "Monday"
  weekly_report_time: "08:00"
```

This section is reference metadata. See [scheduler/README.md](scheduler/README.md) for automation setup.

---

## Reporting

```yaml
reporting:
  trade_log_file: "data/trades.csv"
  performance_file: "data/performance.csv"
  reports_dir: "reports/"
  logs_dir: "logs/"
```

Runtime files under `data/`, `reports/`, and `logs/` are local artifacts and should not be committed.

---

## Backtest-Only Experiments

Use `--strategies` and repeated `--set` arguments to test configuration variants without editing `config/config.yaml`:

```bash
python3 scheduler/run_backtest.py --days 365 --fund 10000 --screener \
  --strategies momentum,ma_crossover,range_breakout \
  --set strategies.momentum.top_n=3 \
  --set strategies.momentum.min_momentum_pct=0.06
```

Run both screener and fixed-universe variants before adopting a change:

```bash
python3 scheduler/run_backtest.py --days 365 --fund 10000 --screener
python3 scheduler/run_backtest.py --days 365 --fund 10000 --no-screener
```
