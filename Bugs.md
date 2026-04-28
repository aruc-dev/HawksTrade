# HawksTrade â€” Bug List (ma-crossover branch)
**Repo:** `aruc-dev/HawksTrade` | **Audited:** 2026-04-28

---

## ðŸ”´ Critical Bugs

### BUG-001 Â· RSI Reversion â€” Hardcoded SMA200 Lower Bound
**File:** `strategies/rsi_reversion.py` | **Line:** 272

**Problem:**  
The SMA200 band check uses a **hardcoded `0.85` lower multiplier** while the upper multiplier is correctly config-driven. If you change `sma200_upper_buffer_pct` in `config.yaml` (e.g., to `0.20`), the upper bound widens to `+20%` but the lower stays permanently at `-15%`, creating a silent asymmetric filter.

**Current code:**
```python
in_sma200_band = sma200 * 0.85 < price < sma200 * (1 + sma200_upper)
```

**Fix:**  
Add a `sma200_lower_buffer_pct` config key and use it:
```yaml
# config/config.yaml â€” under rsi_reversion:
sma200_lower_buffer_pct: 0.15   # Entry blocked if price < SMA200 Ã— (1 - this)
```
```python
sma200_lower = float(SCFG.get("sma200_lower_buffer_pct", 0.15))
in_sma200_band = sma200 * (1 - sma200_lower) < price < sma200 * (1 + sma200_upper)
```

---

### BUG-002 Â· Momentum â€” `breadth_yellow_threshold` Config Key Is Dead
**File:** `strategies/momentum.py` | **Lines:** 107â€“122 | **Config:** `config/config.yaml` line 135

**Problem:**  
`config.yaml` defines `breadth_yellow_threshold: 0.40`, but `momentum.py` never reads it. The Yellow/Green boundary is entirely determined by `breadth_green_threshold`. Any operator who edits `breadth_yellow_threshold` expecting a different Yellow zone will see no effect.

**Current code (momentum.py):**
```python
green_thresh = float(SCFG.get("breadth_green_threshold", 0.50))
red_thresh   = float(SCFG.get("breadth_red_threshold", 0.25))
# breadth_yellow_threshold is NEVER read â€” Yellow = anything between red and green
```

**Fix (Option A):** Remove the unused key from config:
```yaml
# Remove this line from config.yaml:
# breadth_yellow_threshold: 0.40
```

**Fix (Option B):** Actually use it as the Green entry threshold:
```python
green_thresh  = float(SCFG.get("breadth_yellow_threshold", 0.40))  # Yellow â†’ Green boundary
full_green    = float(SCFG.get("breadth_green_threshold", 0.50))   # Full-green (optional future use)
```

---

### BUG-003 Â· RSI Reversion â€” Inconsistent `close` Attribute Access in Scan Loop
**File:** `strategies/rsi_reversion.py` | **Line:** 260

**Problem:**  
Helper functions (`_in_severe_crash`, `_in_high_volatility_regime`, `_calc_atr`) use a defensive pattern for bar attribute access:
```python
float(b.close) if hasattr(b, "close") else float(b["close"])
```
But the **main per-symbol scan loop** uses a raw attribute access with no fallback:
```python
closes = pd.Series([b.close for b in bars])  # â† no hasattr guard
```
If the Alpaca SDK ever returns bar objects as dict-like structures (which has occurred across SDK versions), this raises `AttributeError`, silently skipping the symbol.

**Fix:**
```python
closes = pd.Series([
    float(b.close) if hasattr(b, "close") else float(b["close"])
    for b in bars
])
```

---

## ðŸŸ¡ Moderate Issues

### BUG-004 Â· MA Crossover â€” Slope Filter `iloc[-5]` Lacks Explicit Length Guard
**File:** `strategies/ma_crossover.py` | **Line:** 119

**Problem:**  
```python
is_trending_up = slow.iloc[-1] > slow.iloc[-5]
```
The minimum bar guard is `max(slow_span, atr_period) + 5 = max(21, 14) + 5 = 26`. With exactly 26 bars, `iloc[-5]` accesses index 21 â€” safe by a one-element margin. If any future change reduces the minimum bar guard (e.g., adjusting config defaults), this raises an `IndexError` silently caught by the outer `except Exception` block, incorrectly blocking valid signals.

**Fix:**
```python
is_trending_up = (len(slow) >= 5) and (slow.iloc[-1] > slow.iloc[-5])
```

---

### BUG-005 Â· MA Crossover `should_exit` â€” No Take-Profit or RSI Overbought Exit
**File:** `strategies/ma_crossover.py` | **Lines:** 180â€“200

**Problem:**  
`should_exit` only triggers on a bearish EMA crossover. There is no:
- RSI overbought exit threshold
- Strategy-level price take-profit target
- Trailing stop at the strategy level

In fast-moving crypto markets, price can surge and retrace fully before a bearish EMA cross occurs. The only upper protection is the global `take_profit_pct: 12%` in the risk manager â€” a blunt instrument for a crypto strategy.

**Fix:** Add a configurable RSI overbought exit:
```yaml
# config/config.yaml â€” under ma_crossover:
rsi_exit_max: 70   # Exit if RSI rises above this after entry
```
```python
def should_exit(self, symbol: str, entry_price: float) -> tuple:
    ...
    rsi_exit_max = int(SCFG.get("rsi_exit_max", 70))
    rsi_val = _calc_rsi(closes, 14)

    if cross == "bearish":
        return True, f"EMA {fast_span} crossed below EMA {slow_span}"
    if rsi_val > rsi_exit_max:
        return True, f"RSI overbought: {rsi_val:.1f} > {rsi_exit_max}"
```

---

### BUG-006 Â· RSI Reversion â€” Scan Loop `b.close` Access Also Lacks `hasattr` in `should_exit`
**File:** `strategies/rsi_reversion.py` | **Line:** 367

**Problem:**  
Same pattern as BUG-003, but in the `should_exit` method:
```python
closes = pd.Series([b.close for b in bars])  # â† no hasattr guard in should_exit
```

**Fix:** Apply the same defensive pattern:
```python
closes = pd.Series([
    float(b.close) if hasattr(b, "close") else float(b["close"])
    for b in bars
])
```

---

### BUG-007 Â· Momentum â€” Raw `bars[-1].volume` Access Without None/Type Guard
**File:** `strategies/momentum.py` | **Line:** 189

**Problem:**  
```python
curr_vol = float(bars[-1].volume)
```
If `bars[-1].volume` is `None` (possible for thinly traded symbols or data gaps), `float(None)` raises `TypeError`. The surrounding `except Exception` handler catches it, but the symbol is silently skipped.

**Fix:**
```python
curr_vol = float(getattr(bars[-1], "volume", 0) or 0)
```

---

### BUG-008 Â· `_calc_rsi` â€” Silent `NaN` RSI in Flat Markets
**File:** `strategies/rsi_reversion.py` | **Lines:** ~135â€“145

**Problem:**  
```python
with np.errstate(divide="ignore", invalid="ignore"):
    rs = avg_g / avg_l
rsi = 100 - (100 / (1 + rs))
```
When `avg_g = 0 AND avg_l = 0` (completely flat price series), `rs = NaN`, and `rsi = NaN`. The `np.errstate` suppresses the numpy warning. A `NaN` RSI value then silently passes or fails numerical comparisons (`rsi < 30`, `rsi > 50`) unpredictably depending on numpy's NaN comparison behavior.

**Fix:**
```python
rs = np.nan_to_num(avg_g / avg_l, nan=1.0, posinf=np.inf)
rsi = 100 - (100 / (1 + rs))
if np.isnan(rsi):
    return 50.0  # Neutral RSI for flat/insufficient data
```

---

## ðŸŸ¢ Low-Severity / Style Issues

### BUG-009 Â· Unused `BASE_DIR` Import in `rsi_reversion.py` and `ma_crossover.py`
**Files:** `strategies/rsi_reversion.py` line 42, `strategies/ma_crossover.py` line 24

**Problem:**  
Both files define `BASE_DIR = Path(__file__).resolve().parent.parent` but never reference it anywhere in the file.

**Fix:** Remove the unused import from both files.

---

### BUG-010 Â· `run_backtest.py` â€” Module-Level Imports After `sys.path` Manipulation (E402)
**File:** `scheduler/run_backtest.py` | **Lines:** 27â€“47

**Problem:**  
All project module imports occur after `sys.path.insert(0, str(BASE_DIR))`, triggering flake8 `E402` warnings. While functionally necessary, this pattern can cause subtle issues if the path manipulation is ever made conditional.

**Fix:** Wrap the `sys.path` manipulation in a guard, or add `# noqa: E402` to the import lines if the pattern is intentional:
```python
import sys
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core import alpaca_client as ac  # noqa: E402
```

---

### BUG-011 Â· `reconcile_trade_log.py` â€” Module-Level Imports After Path Manipulation (E402)
**File:** `scheduler/reconcile_trade_log.py` | **Lines:** 20â€“22

**Problem:** Same as BUG-010. Project module imports follow `sys.path` manipulation.

**Fix:** Same approach â€” add `# noqa: E402` or restructure path setup.

---

### BUG-012 Â· Flake8 Whitespace/Alignment Warnings in Core Files
**Files:** `core/alpaca_client.py`, `core/risk_manager.py`, `core/sector_lookup.py`

**Problem:**  
Multiple `W293` (blank lines containing whitespace) and `E221` (multiple spaces before operator for alignment) violations. These do not affect runtime behavior but indicate inconsistent formatting and would fail a strict CI flake8 gate.

Key locations:
- `alpaca_client.py`: lines 308â€“310, 356â€“357, 520â€“654 (multiple W293)
- `risk_manager.py`: lines 346, 367, 376 (W293)
- `sector_lookup.py`: line 4 (W291 trailing whitespace)

**Fix:** Run `autopep8 --in-place --aggressive core/alpaca_client.py core/risk_manager.py` or configure your editor to trim trailing whitespace on save.

---

### BUG-013 Â· MA Crossover â€” `avg_range` Window Is Hardcoded (Not Config-Driven)
**File:** `strategies/ma_crossover.py` | **Line:** 126

**Problem:**  
```python
avg_range = ranges.iloc[-11:-1].mean()  # hardcoded 10-period window
```
The volatility filter's averaging window is hardcoded at 10 periods and cannot be tuned via config. This is inconsistent with other configurable parameters.

**Fix:**
```yaml
# config/config.yaml â€” under ma_crossover:
vol_filter_period: 10   # Period for volatility (range) filter average
```
```python
vol_filter_period = int(SCFG.get("vol_filter_period", 10))
avg_range = ranges.iloc[-(vol_filter_period + 1):-1].mean()
```

---

### BUG-014 Â· MA Crossover â€” No Volume Confirmation Filter
**File:** `strategies/ma_crossover.py` | **Scan loop**

**Problem:**  
Unlike the Momentum and RSI Reversion strategies â€” both of which require a volume spike for entry confirmation â€” the MA Crossover strategy has **no volume confirmation**. EMA crossovers on thin crypto volume are more likely to be noise.

**Fix:**
```yaml
# config/config.yaml â€” under ma_crossover:
volume_spike_ratio: 1.2   # Entry bar volume must exceed this multiple of the N-day average
volume_avg_period: 20
```
```python
vol_spike_ratio = float(SCFG.get("volume_spike_ratio", 1.2))
vol_avg_period  = int(SCFG.get("volume_avg_period", 20))
volumes = pd.Series([b.volume for b in bars])
avg_vol = volumes.iloc[-(vol_avg_period + 1):-1].mean()
curr_vol = float(bars[-1].volume)
if avg_vol > 0 and curr_vol < vol_spike_ratio * avg_vol:
    log.debug(f"[MACross] {symbol} skipped: volume confirmation failed")
    continue
```

---

## Priority Summary

| Bug | Severity | File | Fix Effort |
|-----|----------|------|------------|
| BUG-001 | ðŸ”´ Critical | `rsi_reversion.py:272` | Low â€” add config key + 1 line |
| BUG-002 | ðŸ”´ Critical | `momentum.py:107` + `config.yaml:135` | Low â€” remove or read key |
| BUG-003 | ðŸ”´ Critical | `rsi_reversion.py:260` | Low â€” defensive list comprehension |
| BUG-004 | ðŸŸ¡ Moderate | `ma_crossover.py:119` | Trivial â€” add `len(slow) >= 5` guard |
| BUG-005 | ðŸŸ¡ Moderate | `ma_crossover.py:180` | Medium â€” add RSI exit logic |
| BUG-006 | ðŸŸ¡ Moderate | `rsi_reversion.py:367` | Low â€” same fix as BUG-003 |
| BUG-007 | ðŸŸ¡ Moderate | `momentum.py:189` | Trivial â€” `getattr(..., 0) or 0` |
| BUG-008 | ðŸŸ¡ Moderate | `rsi_reversion.py:~140` | Low â€” `nan_to_num` + NaN check |
| BUG-009 | ðŸŸ¢ Low | `rsi_reversion.py:42`, `ma_crossover.py:24` | Trivial â€” delete unused import |
| BUG-010 | ðŸŸ¢ Low | `run_backtest.py:27` | Trivial â€” `# noqa: E402` |
| BUG-011 | ðŸŸ¢ Low | `reconcile_trade_log.py:20` | Trivial â€” `# noqa: E402` |
| BUG-012 | ðŸŸ¢ Low | `alpaca_client.py`, `risk_manager.py` | Low â€” `autopep8` |
| BUG-013 | ðŸŸ¢ Low | `ma_crossover.py:126` | Low â€” add config key |
| BUG-014 | ðŸŸ¢ Low | `ma_crossover.py` scan | Medium â€” add volume filter block |