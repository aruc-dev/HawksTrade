# Slippage Model

HawksTrade uses a liquidity-aware adverse slippage estimate for live marketable
limit offsets and optional backtest cost modelling.

Formula:

```text
slippage_bps = k * realised_volatility_bps * sqrt(order_size_usd / adv_usd)
```

The estimate is then adjusted by:

- time-of-day multipliers for the opening and closing windows
- buy-side asymmetry for long entries
- per-symbol multipliers when calibration has enough evidence
- configured min/max bps caps

Configuration lives under `slippage_model` in `config/config.yaml`. Production
validation enables the same model through `validation.cost_model` using
`slippage_model_enabled: true` and `slippage_multiplier`. Slippage sensitivity
runs use `sensitivity_multipliers` as model-output multipliers; legacy flat bps
stress levels remain available only when `slippage_model_enabled` is false.

## Calibration

Each trade-log fill can carry:

- `decision_price`
- `arrival_price`
- `expected_slippage_bps`
- `realised_slippage_bps`

Run the read-only calibration helper after enough paper fills accumulate:

```bash
python3 scripts/calibrate_slippage_model.py --since 2026-01-01 --min-fills 50
```

The script prints a proposed YAML snippet. Review the sample size and outliers
before applying any config change. Do not auto-apply calibration output.

## Operating Rule

If median realised slippage materially exceeds expected slippage in the weekly
TCA report, reduce allocation or widen execution safeguards before increasing
risk. Per-symbol overrides should require at least 20 fills for that symbol.
