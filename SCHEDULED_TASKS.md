# HawksTrade — Scheduled Tasks Reference
## How to Recreate Automations on Any System

This file documents every scheduled task that drives HawksTrade.
Use it to recreate automations when moving to a new laptop, cloud VM, or AI platform.

---

## Timezone Note

All times below are given in **both ET and PDT** because:
- US stock markets operate on **Eastern Time (ET)**
- The original system runs on **Pacific Daylight Time (PDT = ET − 3 hours)**

When recreating tasks on a different system, use the times appropriate for **that system's local timezone**.
The cron expressions in Section 2 are written for **PDT (UTC−7)**.
The cron expressions in Section 3 are written for **ET (UTC−4)** (for cloud VMs or systems in the Eastern timezone).

---

## Current Project Status

As of April 12, 2026, the validated default profile is:

- `mode: paper`
- `intraday.enabled: false`
- `screener.enabled: true`
- Enabled strategies: `momentum`, `ma_crossover`, `range_breakout`
- Disabled strategies: `rsi_reversion`, `gap_up`
- Momentum defaults: `top_n: 3`, `min_momentum_pct: 0.06`, `exit_policy: profit_trailing`
- Current crypto universe: `BTC/USD`, `SOL/USD`, `LINK/USD`, `DOGE/USD`, `LTC/USD`, `DOT/USD`
- Latest 12-month backtest: +26.53%, 274 trades, 34.7% win rate, -9.34% max drawdown

See `backtests.md` and `config.md` before changing strategy schedules or defaults.

---

## 1. Task Summary

| Task ID | Purpose | ET Schedule | PDT Schedule |
|---------|---------|-------------|--------------|
| `hawkstrade-stock-scan` | First stock-only scan, then full stock + crypto scans | 9:35 AM stocks-only, then every 30 min from 10:00 AM–3:30 PM Mon–Fri | 6:35 AM stocks-only, then every 30 min from 7:00 AM–12:30 PM Mon–Fri |
| `hawkstrade-risk-check` | Stop-loss / take-profit enforcement | Every 15 min, 9:45 AM–3:45 PM Mon–Fri | Every 15 min, 6:45 AM–12:45 PM Mon–Fri |
| `hawkstrade-crypto-scan` | Crypto-only scan (24/7) | Every hour, every day | Every hour, every day (same) |
| `hawkstrade-daily-report` | Daily performance report | 4:30 PM Mon–Fri | 1:30 PM Mon–Fri |
| `hawkstrade-weekly-report` | Weekly performance summary | 8:00 AM Monday | 5:00 AM Monday |

---

## 2. Recreating in Claude (Cowork / Desktop App)

If you are moving to a new machine with the **Claude desktop app**, recreate the tasks by
telling Claude:

> "I have moved my HawksTrade project to [new path]. Please recreate all 5 scheduled tasks
> from the SCHEDULED_TASKS.md file."

Or recreate them manually using the instructions below. Claude's scheduled tasks use
**your system's local timezone**, so adjust cron expressions to match.

### Task 1 — Stock + Crypto Scan (PDT cron)

```
Task ID:      hawkstrade-stock-scan
Description:  HawksTrade: First stock-only scan, then stock + crypto scan every 30 min
Cron (PDT):   35 6 * * 1-5 and 0,30 7-12 * * 1-5
Cron (ET):    35 9 * * 1-5 and 0,30 10-15 * * 1-5

Prompt:
You are the HawksTrade trading bot agent. Your job is to run the scheduled stock and crypto scan.

Working directory: /path/to/HawksTrade   ← UPDATE THIS PATH

Instructions:
1. Read CLAUDE.md in the working directory for the full operating manual.
2. If it is the first scan of the day (6:35 AM PDT / 9:35 AM ET), run stocks-only:
   cd /path/to/HawksTrade && python scheduler/run_scan.py --stocks-only
3. Otherwise run the full scan:
   cd /path/to/HawksTrade && python scheduler/run_scan.py
4. Current default stock strategy set: momentum only; RSI reversion and gap-up are disabled.
5. Report back: signals found, trades entered/exited, errors, open position count.
6. If the script errors due to missing keys, remind the user to fill in their
   Alpaca API keys in config/.env or .env (copy from config/.env.example).
7. Do NOT change config/config.yaml risk parameters or mode without explicit user instruction.
```

### Task 2 — Risk Check (PDT cron)

```
Task ID:      hawkstrade-risk-check
Description:  HawksTrade: Stop-loss / take-profit check every 15 min
Cron (PDT):   45 6 * * 1-5 and 0,15,30,45 7-12 * * 1-5
Cron (ET):    45 9 * * 1-5 and 0,15,30,45 10-15 * * 1-5

Prompt:
You are the HawksTrade risk enforcement agent.

Working directory: /path/to/HawksTrade   ← UPDATE THIS PATH

Instructions:
1. Run: cd /path/to/HawksTrade && python scheduler/run_risk_check.py
2. Report: positions hit stop-loss or take-profit (include entry vs exit price and P&L %),
   whether daily loss limit was triggered, positions still holding.
3. If daily loss limit is hit: warn the user all trading has been halted for the day.
4. If connection error: retry once after 60 seconds.
5. Do NOT modify risk thresholds in config/config.yaml.
```

### Task 3 — Crypto Scan (no timezone conversion needed)

```
Task ID:      hawkstrade-crypto-scan
Description:  HawksTrade: Crypto-only scan every hour, 24/7 including weekends
Cron:         0 * * * *    ← same in all timezones (every hour on the hour)

Prompt:
You are the HawksTrade crypto trading agent.

Working directory: /path/to/HawksTrade   ← UPDATE THIS PATH

Instructions:
1. Run: cd /path/to/HawksTrade && python scheduler/run_scan.py --crypto-only
2. Strategies: EMA Crossover and Range Breakout on BTC/USD, SOL/USD, LINK/USD, DOGE/USD, LTC/USD, DOT/USD.
3. Report: pairs scanned, buy signals, trades entered/exited, open crypto positions with P&L.
4. On connection error: retry once after 90 seconds.
```

### Task 4 — Daily Report (PDT cron)

```
Task ID:      hawkstrade-daily-report
Description:  HawksTrade: Daily performance report | 1:30 PM PDT (4:30 PM ET) weekdays
Cron (PDT):   30 13 * * 1-5
Cron (ET):    30 16 * * 1-5
Notify:       YES (user should be notified on completion)

Prompt:
You are the HawksTrade reporting agent.

Working directory: /path/to/HawksTrade   ← UPDATE THIS PATH

Instructions:
1. Run: cd /path/to/HawksTrade && python scheduler/run_report.py
2. Read the generated report from reports/daily_YYYY-MM-DD.txt
3. Present a clear summary: trading mode, portfolio value, cash, open positions with
   entry/current price and unrealised P&L %, today's closed trades and P&L,
   all-time win rate and cumulative P&L, any risk events today.
4. Flag anything needing attention: positions near stop-loss, underperforming strategies.
5. End with: "Bot is operating normally" or flag concerns.
```

### Task 5 — Weekly Report (PDT cron)

```
Task ID:      hawkstrade-weekly-report
Description:  HawksTrade: Weekly performance report | 5:00 AM PDT (8:00 AM ET) every Monday
Cron (PDT):   0 5 * * 1
Cron (ET):    0 8 * * 1
Notify:       YES (user should be notified on completion)

Prompt:
You are the HawksTrade weekly reporting agent.

Working directory: /path/to/HawksTrade   ← UPDATE THIS PATH

Instructions:
1. Run: cd /path/to/HawksTrade && python scheduler/run_report.py --weekly
2. Read from reports/weekly_YYYY-WNN.txt
3. Present: week date range, total trades (entries/exits/win-loss), weekly P&L %,
   strategy-by-strategy table, best and worst trade, monthly P&L so far,
   all-time totals.
4. Assess: which strategies are working, any risk events, patterns or anomalies.
5. Suggest reviewing config/config.yaml if strategy adjustment is warranted.
6. If mode=paper: remind user to review before switching to live.
```

---

## 3. Recreating with Cron on Linux / Cloud VM

If running on a Linux VM (AWS, GCP, etc.) **in the Eastern timezone**, add these to crontab:

```bash
crontab -e
```

Paste (update `/path/to/HawksTrade`):

```cron
# HawksTrade — all times in ET (Eastern Time)

# First scan of day: stocks only (9:35 AM ET)
35 9 * * 1-5       cd /path/to/HawksTrade && python scheduler/run_scan.py --stocks-only >> logs/cron.log 2>&1

# Full scan every 30 min from 10:00 AM-3:30 PM ET
0,30 10-15 * * 1-5 cd /path/to/HawksTrade && python scheduler/run_scan.py >> logs/cron.log 2>&1

# Risk check every 15 min from 9:45 AM-3:45 PM ET
45 9 * * 1-5        cd /path/to/HawksTrade && python scheduler/run_risk_check.py >> logs/cron.log 2>&1
0,15,30,45 10-15 * * 1-5 cd /path/to/HawksTrade && python scheduler/run_risk_check.py >> logs/cron.log 2>&1

# Crypto scan every hour, 24/7
0 * * * *          cd /path/to/HawksTrade && python scheduler/run_scan.py --crypto-only >> logs/cron.log 2>&1

# Daily report at 4:30 PM ET
30 16 * * 1-5      cd /path/to/HawksTrade && python scheduler/run_report.py >> logs/cron.log 2>&1

# Weekly report at 8:00 AM ET every Monday
0 8 * * 1          cd /path/to/HawksTrade && python scheduler/run_report.py --weekly >> logs/cron.log 2>&1
```

If your VM is in **UTC**, subtract 4 hours from ET times (or 7 from PDT):

```cron
# HawksTrade — all times in UTC (ET+4, PDT+7)

35 13 * * 1-5        cd /path/to/HawksTrade && python scheduler/run_scan.py --stocks-only >> logs/cron.log 2>&1
0,30 14-19 * * 1-5  cd /path/to/HawksTrade && python scheduler/run_scan.py >> logs/cron.log 2>&1
45 13 * * 1-5        cd /path/to/HawksTrade && python scheduler/run_risk_check.py >> logs/cron.log 2>&1
0,15,30,45 14-19 * * 1-5 cd /path/to/HawksTrade && python scheduler/run_risk_check.py >> logs/cron.log 2>&1
0 * * * *            cd /path/to/HawksTrade && python scheduler/run_scan.py --crypto-only >> logs/cron.log 2>&1
30 20 * * 1-5        cd /path/to/HawksTrade && python scheduler/run_report.py >> logs/cron.log 2>&1
0 12 * * 1           cd /path/to/HawksTrade && python scheduler/run_report.py --weekly >> logs/cron.log 2>&1
```

---

## 4. Recreating with Other AI Agents (Codex, GPT, Gemini)

Any AI agent with shell access can run HawksTrade by:
1. Reading `AGENTS.md` for the operating manual
2. Using the cron schedule above as a reference for when to run each script
3. Running the scripts via bash as documented

The project is fully self-contained. No external dependencies beyond Alpaca API keys and Python packages in `requirements.txt`.

---

## 5. Checklist: Moving to a New System

- [ ] Copy the entire `HawksTrade/` folder to the new system
- [ ] Install Python 3.10+ on the new system
- [ ] Run: `pip install -r requirements.txt --break-system-packages`
- [ ] Copy `config/.env` or `.env` from the old system (or create fresh from `config/.env.example`)
- [ ] Update the **working directory path** in each scheduled task prompt
- [ ] Determine local timezone of new system and pick correct cron expressions from above
- [ ] Recreate the 5 scheduled tasks (Claude desktop, cron, or agent framework)
- [ ] Test connection: `python -c "import sys; sys.path.insert(0,'.'); from core.alpaca_client import get_account; print('OK:', get_account().portfolio_value)"`
- [ ] Run a manual scan to confirm: `python scheduler/run_scan.py`
- [ ] Run unit tests before deployment: `python3 -m unittest discover -v`

---

*HawksTrade — portable by design.*
