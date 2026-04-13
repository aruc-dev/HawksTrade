# HawksTrade — Status UI

A **read-only, zero-intrusion** dashboard generator for HawksTrade.  
It reads your existing config, trade log, and scan logs — never importing or modifying any trading code.

---

## What it shows

| Panel | Source |
|-------|--------|
| 🟡 PAPER / 🔴 LIVE mode badge | `config/config.yaml` → `mode` |
| Last run time | `logs/scan_YYYYMMDD.log` file modification time |
| Open positions (symbol, qty, entry price, strategy) | `data/trades.csv` → rows with `status=open` |
| Trade actions — BUY / SELL history | `data/trades.csv` — last 60 rows |
| Win rate, total P&L, closed trade count | `data/trades.csv` — aggregated |
| System config (strategies, intervals, screener) | `config/config.yaml` |
| Last run logs (collapsible, scrollable) | `logs/scan_YYYYMMDD.log` — last 150 lines |

---

## Files

```
status_ui/
├── generate_status.py     ← Main generator script (run this)
├── run_status_generator.sh ← Shell loop for continuous refresh
├── status.html            ← OUTPUT — open in any browser (git-ignored)
└── README.md              ← This file
```

Add `status_ui/status.html` to `.gitignore` (it's regenerated every run).

---

## Usage

### One-time generation (test it first)

```bash
cd /path/to/HawksTrade
python3 status_ui/generate_status.py
open status_ui/status.html     # macOS
```

### Options

```
--project-dir PATH   HawksTrade root (default: parent of status_ui/)
--output PATH        Output HTML path (default: status_ui/status.html)
--log-lines N        Log lines to show (default: 150)
--refresh N          Browser auto-refresh seconds (default: 60, 0=off)
```

### Continuous refresh — Option A: Shell loop

```bash
# Default: refresh every 120 seconds
bash status_ui/run_status_generator.sh

# Custom interval (e.g. 60 seconds)
bash status_ui/run_status_generator.sh 60
```

Run in a **separate terminal** from your trading system. It will keep running until Ctrl+C.

### Continuous refresh — Option B: cron

```bash
crontab -e
```

Add (adjust path):
```
# HawksTrade status dashboard — refresh every 2 minutes
*/2 * * * * /usr/bin/python3 /path/to/HawksTrade/status_ui/generate_status.py >> /path/to/HawksTrade/status_ui/generator.log 2>&1
```

---

## Requirements

- Python 3.8+
- `pyyaml` (already in HawksTrade's `requirements.txt`)
- No other dependencies — stdlib only (csv, os, re, datetime, pathlib)

---

## How the browser auto-refresh works

The generated HTML includes:
```html
<meta http-equiv="refresh" content="60">
```
If you leave the file open in a browser, it automatically reloads every 60 seconds (matching your generator interval). Pass `--refresh 0` to disable.

---

## Staleness detection

If the `logs/scan_*.log` file hasn't been modified in longer than 3× the expected interval, the Last Run time will appear visually stale. Check that the trading system's cron tasks are running.

---

## .gitignore

Add this to your project `.gitignore`:
```
status_ui/status.html
status_ui/generator.log
```
