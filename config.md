# HawksTrade Configuration Guide

> **Updated:** May 2, 2026
> **Primary config file:** `config/config.yaml`
> **Local config:** `config/config.local.yaml` — if present, deep-merged over `config/config.yaml`. Include only the keys you want to override. Gitignored; use for per-machine settings without modifying the committed file.
> **Recommended profile:** tail-risk-hardened paper trading profile validated by the latest 12-month backtest.

This guide explains the available user-facing configuration sections and the currently recommended defaults. Do not switch `mode` to `live` or change risk parameters unless you explicitly intend to accept the added trading risk.

---

## Recommended Configuration

The latest validated default configuration is:

| Area | Recommended Setting | Reason |
|---|---|---|
| Trading mode | `mode: paper` | Paper trading should remain the default until live trading is explicitly approved. |
| Intraday trading | `intraday.enabled: false` | The system is validated as a swing-trading bot. |
| Screener | `screener.enabled: true` | The tightened screener improved 12-month return versus the old screener and recent fixed-universe test. |
| Momentum | enabled, `top_n: 2`, `min_momentum_pct: 0.08`, `volume_confirmation_mode: pace`, `volume_pace_ratio: 1.5`, `min_breadth_coverage_pct: 0.65` | Allows a second green-regime stock candidate while keeping yellow regimes capped at one signal and preserving sector/risk guards. |
| RSI Reversion | enabled, `oversold_threshold: 40`, `max_entry_atr_pct: 0.05`, `max_recent_drawdown_pct: 0.10` | Active mean-reversion stock sleeve with crash, realised-volatility, high-ATR, recent-waterfall, and max-loss guards. |
| Gap-Up | enabled, `require_prior_close_above_trend: true` | Opening-momentum sleeve with true-gap, opening-volume pace, completed-bar trend, and top-1 ranking guards. |
| MA Crossover | enabled, `hold_days: 16`, `max_loss_exit_pct: 0.02` | Positive crypto contribution with recent-window weakness reduced while avoiding the older large-loss tail seen with a 3% exit. |
| Range Breakout | enabled | Crypto Donchian breakout sleeve with volume, trend, RSI, 0.8%-8% extension, close-location, and failed-breakout guards. |
| Momentum exit policy | `profit_trailing` | Exits flat/losing trades after the minimum hold while allowing winners to run under trailing protection. |

Latest recommended 12-month result:

| Final Value | Return | Trades | Win Rate | Max Drawdown |
|---:|---:|---:|---:|---:|
| $12,070.22 | +20.70% | 112 | 53.6% | -1.92% |

These results enforce `trading.max_position_pct: 0.08` for all entries, including momentum/Kelly sizing, with all configured strategies enabled. Stop-loss, take-profit, daily-loss halt, and mode remain unchanged.

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
  max_position_pct: 0.08
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
| `max_position_pct` | Max portfolio allocation per trade | 8% |
| `stop_loss_pct` | Per-position stop-loss from entry | 3.5% |
| `take_profit_pct` | Per-position take-profit from entry | 12% |
| `daily_loss_limit_pct` | Daily account-level loss halt | 5% |
| `min_trade_value_usd` | Minimum order notional | $100 |
| `order_type` | `limit` or `market` | `limit` |
| `limit_slippage_pct` | Limit price offset for fast fills | 0.1% |

These are risk parameters. Keep them unchanged unless you are deliberately revalidating risk.

`order_type` remains the default for entries and ordinary strategy exits. Risk exits from
`scheduler/run_risk_check.py` (stop-loss, take-profit, and daily-loss emergency exits)
and momentum hold exits from `scheduler/run_scan.py` use market sell orders so exit
certainty is not dependent on DAY limit orders filling.

---

## Order Governor

```yaml
order_governor:
  enabled: true
  max_active_orders: 50
  max_orders_per_window: 60
  order_rate_window_seconds: 60
  max_daily_orders: 500
  max_notional_usd: null
  max_notional_pct: null
```

The order governor is a default-on broker-state safety gate that runs immediately
before order submission. It blocks duplicate active orders for the same symbol and
side, missing broker/account state, excessive active broker orders, order-rate
bursts, daily order-count breaches, and optional notional limits.

| Setting | Meaning | Current Default |
|---|---|---:|
| `enabled` | Enables the pre-submit safety gate. Disable only for controlled debugging. | `true` |
| `max_active_orders` | Blocks new entries when active broker orders meet or exceed this count. | 50 |
| `max_orders_per_window` | Blocks new entries if recent local order-intent history exceeds this count. | 60 |
| `order_rate_window_seconds` | Rolling window used with `max_orders_per_window`. | 60 |
| `max_daily_orders` | Blocks new entries after this many local order intents in the UTC day. | 500 |
| `max_notional_usd` | Optional hard dollar cap per submitted order. | `null` |
| `max_notional_pct` | Optional cap as a fraction of portfolio value per submitted order. | `null` |

Governor blocks are fail-closed. Operational lookup/history failures mark the scan
unhealthy so they are visible in health checks instead of looking like a clean no-trade run.

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
    - ETH/USD
    - SOL/USD
    - LINK/USD
    - XRP/USD
    - ADA/USD
    - AVAX/USD
    - DOGE/USD
    - LTC/USD
    - DOT/USD
    - UNI/USD
    - AAVE/USD
```

These pairs are used by the crypto strategies. Crypto scans can run 24/7.

---

## Strategies

### Momentum

```yaml
momentum:
  enabled: true
  asset_class: stocks
  top_n: 2
  hold_days: 4
  exit_policy: "profit_trailing"
  profit_floor_pct: 0.0
  trail_activation_pct: 0.06
  trailing_stop_pct: 0.04
  max_hold_days: 20
  min_momentum_pct: 0.08
  min_alpha_pct: 0.0
  min_breadth_coverage_pct: 0.65
  volume_confirmation_mode: "pace"
  volume_pace_ratio: 1.5
  volume_pace_timeframe: "1Min"
  session_minutes: 390
  volume_spike_ratio: 2.0
```

Recommended: enabled.

Momentum is the primary stock contributor. The moderate-growth profile uses `top_n: 2`, `min_momentum_pct: 0.08`, `volume_confirmation_mode: pace`, `volume_pace_ratio: 1.5`, and `min_breadth_coverage_pct: 0.65` to increase qualified opportunities while keeping yellow regimes capped at one position and sector concentration capped. If intraday bars are unavailable, pace mode falls back to the legacy `volume_spike_ratio: 2.0` full-day ratio.

### RSI Reversion

```yaml
rsi_reversion:
  enabled: true
  rsi_period: 14
  oversold_threshold: 40
  overbought_threshold: 50
  hold_days: 10
  vix_multiplier: 0.95
  atr_multiplier: 0.8
  max_entry_atr_pct: 0.05
  max_stop_loss_pct: 0.06
  max_loss_exit_pct: 0.06
  volume_spike_ratio: 0.7
  recent_drawdown_lookback_days: 5
  max_recent_drawdown_pct: 0.10
```

Recommended: enabled in the active profile, with continued monitoring through
`python3 scheduler/run_validation_gate.py --profile rsi` before scaling its
capital allocation.

The latest costed 12-month RSI-only backtest produced 29 trades, 62.1% win rate,
2.02 profit factor, +3.44% return, and -0.86% max drawdown. The high-ATR entry
ceiling, 5-day drawdown guard, and 6% max-loss exit reduced the observed worst
RSI trade while keeping the sleeve enabled. The forward paper-trading gate still
requires 60 paper days and 20 closed RSI trades before scaling allocation.

### Gap-Up

```yaml
gap_up:
  enabled: true
  min_gap_pct: 0.05
  max_gap_pct: 0.15
  require_true_gap: true
  volume_multiplier: 1.3
  volume_avg_period: 20
  min_breadth_pct: 0.65
  trend_sma_period: 200
  require_prior_close_above_trend: true
  max_trend_extension_pct: 0.35
  entry_window_minutes: 45
  opening_timeframe: "1Min"
  max_open_extension_pct: 0.03
  max_open_fade_pct: 0.005
  max_signals: 1
  intraday_exit: false
  hold_days: 2
```

Recommended: enabled in the all-strategy profile, with continued monitoring.

The implementation uses completed daily bars for trend/ATR/average volume and
current-session minute bars for the actual opening gap and volume pace, avoiding
current-day daily-bar lookahead in live scans. The prior completed close must
already be above SMA200, which avoids buying a gap that is only jumping into
long-term resistance. The latest dedicated Gap-Up gate improved to +1.45%
costed over 12 months and +0.52% in the recent 30-day watch window, but the
sample is still small, so do not scale it without rerunning the gap validation
profile.

### MA Crossover

```yaml
ma_crossover:
  enabled: true
  asset_class: crypto
  fast_ema: 6
  slow_ema: 18
  timeframe: "1Day"
  trend_return_lookback_days: 3
  min_trend_return_pct: -0.02
  min_price_above_slow_pct: 0.005
  hold_days: 16
  max_loss_exit_pct: 0.02
  rsi_entry_max: 75
  volume_spike_ratio: 1.0
```

Recommended: enabled.

This strategy contributed positively in the latest recommended 12-month backtest. The 16-day hold cap improved costed 12-month MA contribution versus 12 days without worsening the observed worst trade. The strategy-level max-loss exit closes the position when the latest daily close is at least 2% below entry; the 2% setting kept the recent crypto sleeve positive while avoiding the -18.65% tail observed with a looser 3% setting.

### Range Breakout

```yaml
range_breakout:
  enabled: true
  asset_class: crypto
  breakout_lookback_days: 20
  breakout_pct: 0.006
  min_breakout_extension_pct: 0.008
  max_breakout_extension_pct: 0.08
  min_close_location: 0.70
  volume_multiplier: 2.5
  volume_avg_period: 20
  timeframe: "1Day"
  hold_days: 14
  atr_period: 14
  atr_multiplier: 2.0
  risk_per_trade_pct: 0.01
  vol_filter_period: 10
  min_range_ratio: 0.45
  trend_ema_period: 50
  trend_slope_lookback: 5
  rsi_period: 14
  rsi_entry_max: 82
  rsi_exit_max: 82
  profit_floor_pct: 0.03
  breakdown_exit_pct: 0.02
  trend_exit_enabled: true
```

Recommended: enabled in the all-strategy profile, with continued monitoring.

The implementation uses confirmed daily 20-day Donchian high breakouts, ranked
signal selection, ATR-risk sizing, breakout-extension and close-location quality
guards, and explicit failed-breakout exits before the 14-day hold cap. Its
12-month all-enabled contribution was positive, but sample size remains low.

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
  --strategies momentum,rsi_reversion,gap_up,ma_crossover,range_breakout \
  --set strategies.momentum.top_n=1 \
  --set strategies.momentum.min_momentum_pct=0.10 \
  --set strategies.momentum.volume_spike_ratio=1.8 \
  --set strategies.momentum.min_breadth_coverage_pct=0.75
```

Run both screener and fixed-universe variants before adopting a change:

```bash
python3 scheduler/run_backtest.py --days 365 --fund 10000 --screener
python3 scheduler/run_backtest.py --days 365 --fund 10000 --no-screener
```

For execution-cost sensitivity, pass backtest-only slippage and fee assumptions:

```bash
python3 scheduler/run_backtest.py --days 365 --fund 10000 --screener \
  --slippage-bps 10 --fee-bps 5
```

---

## Production Validation Gates

`validation:` defines non-trading gates used before scaling live capital or
strategy sleeves. These settings do not change live order sizing,
stops, take-profits, or mode.

```yaml
validation:
  cost_model:
    slippage_bps: 10.0
    fee_bps: 5.0
    min_fee_usd: 0.0
```

Run the default production gate with:

```bash
python3 scheduler/run_validation_gate.py --profile production
```

The production profile validates the current costed production gate rather than
the older core-only subset. It includes the expanded strategy set used in
`validation.production_gate.windows[*].strategies`, including `gap_up` and
`range_breakout`, and applies the moderate-risk drawdown thresholds defined in
`config/config.yaml`: 6% for the 12-month default gate and 4% for the 6-month
default gate. The latest 30-day crypto sleeve is watch-only: it reports weak
short-window behavior without blocking the longer capital-preservation gates.

RSI Reversion has a separate monitoring gate:

```bash
python3 scheduler/run_validation_gate.py --profile rsi
```

Keep running this profile before scaling RSI Reversion allocation. It checks both
costed backtest requirements and the paper-trading criteria in
`validation.rsi_reversion_enablement`.

Range Breakout has a separate enablement gate:

```bash
python3 scheduler/run_validation_gate.py --profile range
```

This checks the breakout sleeve independently before it is scaled.

Gap-Up has a separate enablement gate:

```bash
python3 scheduler/run_validation_gate.py --profile gap
```

This checks the opening-momentum sleeve independently with the dynamic screener
enabled, matching the active profile before it is scaled.
