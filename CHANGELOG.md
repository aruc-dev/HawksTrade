# HawksTrade Changelog

## [Unreleased]

### Added
- Multi-regime walk-forward validation profiles and reports; capital does not scale unless `walkforward_master` passes at the configured stressed threshold.
- Walk-forward `blocking_levels` and duration-scaled OOS minimum trade counts so diagnostic cost levels do not block the stressed capital gate.
- Advisory pre-commit quick walk-forward check for staged strategy, risk, or validation config changes.
- Live-mode runtime interlock for explicit operator acknowledgement before live trading imports can proceed.
- Momentum ATR stop cap so wide ATR stops cannot silently exceed the configured maximum stop distance.
- Broker protective stop sync for missing broker-side protective sell orders.
- Strategy live-readiness gate for strategies that require paper-trade history before live entries.
- Slippage sensitivity checks in the validation gate.
- Rolling-window validation runner for stability checks across multiple historical windows.
- Crypto correlation guard to block highly correlated same-scan or existing crypto exposure.
- Statistical reliability warnings for low-trade-count validation records.
- Project version metadata in `core/version.py` and generated reports.
- Broker interface contracts in `core/broker_interface.py`.

## [v1.0.0] - 2026-05-16

### Initial Release
- 5 strategies: Momentum, RSI Reversion, Gap-Up, EMA Crossover, Range Breakout.
- Alpaca Markets integration for stocks and crypto.
- AWS EC2 deployment support with systemd.
- 12-month backtest: +20.70% return, -1.92% max drawdown.
