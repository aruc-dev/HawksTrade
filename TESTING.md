# HawksTrade Testing and Validation

Run these checks before pushing code or deploying the bot.

## Unit and Import Checks

```bash
python3 -m unittest discover -v
python3 -W error::DeprecationWarning -m unittest discover
python3 -m compileall core strategies scheduler tracking tests
```

The unit tests mock Alpaca order paths where appropriate and use temporary trade-log files.
They should not place orders.

## Paper Account Read Checks

Use these checks after credentials are configured in `config/.env` or `.env`:

```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from core import alpaca_client as ac
a = ac.get_account()
print('mode', ac.MODE)
print('status', a.status)
print('portfolio_value', a.portfolio_value)
print('positions', len(ac.get_all_positions()))
print('open_orders', len(ac.get_open_orders()))
"
```

Expected:
- `mode` is `paper` unless the human explicitly approved live mode.
- Account status is active.
- Position and open-order counts are visible.

## Scheduler Dry Runs

```bash
python3 scheduler/run_scan.py --dry-run
python3 scheduler/run_scan.py --crypto-only --dry-run
python3 scheduler/run_risk_check.py --dry-run
python3 scheduler/run_report.py
python3 scheduler/run_report.py --weekly
```

`--dry-run` validates signal generation, risk checks, and order-intent logging without
submitting orders.

## Backtest Comparisons

Use these when validating momentum exit behavior:

```bash
python3 scheduler/run_backtest.py --days 365 --fund 10000 --exit-policy fixed_hold --no-screener
python3 scheduler/run_backtest.py --days 365 --fund 10000 --exit-policy profit_trailing --no-screener
python3 scheduler/run_backtest.py --days 365 --fund 10000 --exit-policy risk_only_baseline --no-screener
```

`risk_only_baseline` is for comparing against the old no-hold-exit behavior. It should
not be treated as the default live policy.

Use these for strategy and screener experiments without editing the runtime config:

```bash
python3 scheduler/run_backtest.py --days 365 --fund 10000 --screener \
  --strategies momentum,ma_crossover,range_breakout

python3 scheduler/run_backtest.py --days 365 --fund 10000 --screener \
  --strategies momentum,ma_crossover,range_breakout \
  --set strategies.momentum.top_n=3 \
  --set strategies.momentum.min_momentum_pct=0.06
```

Run sleeve-specific gates before scaling Gap-Up or Range Breakout:

```bash
python3 scheduler/run_validation_gate.py --profile gap
python3 scheduler/run_validation_gate.py --profile range
```

## Paper Order Lifecycle

Only run this when explicitly requested. It creates and closes a simulated Alpaca paper
position and can slightly change paper account cash due to spread or rounding.

Checklist:
- Confirm `config/config.yaml` still has `mode: paper`.
- Confirm open orders are zero before starting.
- Submit a small paper order above Alpaca's minimum notional requirement.
- Close using the actual position quantity returned by Alpaca, not the originally requested
  quantity, because crypto available balances can differ slightly after fills.
- Confirm open positions and open orders are both zero after closing.

## GitHub Hygiene

Do not commit:
- `config/.env` or root `.env`
- `logs/`
- `reports/`
- generated files in `data/`
- `__pycache__/`

These are covered by `.gitignore`, but if a file was already tracked in a repository,
remove it from the index before pushing.
