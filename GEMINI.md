# HawksTrade — Gemini Agent Instructions

<!-- BEGIN MANDATORY WORKFLOW INTEGRATION -->
## 🚨 MANDATORY AGENT WORKFLOW 🚨

This project uses **beads (bd)** for issue tracking and has a strict session close protocol. You MUST follow these steps for **EVERY** change and **EVERY** session.

### 1. Start of Session
- [ ] Run `bd ready` to find available work.
- [ ] Claim an issue: `bd update <id> --claim`.
- [ ] If starting a new task, create an issue first: `bd create "Title" --body "Details"`.

### 2. During Development
- [ ] Use `bd` for ALL task tracking. Do NOT use markdown TODOs or comments.
- [ ] Run quality gates after every change: `python3 -m unittest discover -v`.

### 3. Session Close (MANDATORY - DO NOT SKIP)
Work is **NOT COMPLETE** until these steps are finished and `git push` succeeds:
1. **File issues** for remaining or discovered work.
2. **Run quality gates**: `python3 -m unittest discover -v`.
3. **Update issue status**: `bd close <id>`.
4. **PUSH TO REMOTE**:
   ```bash
   git pull --rebase
   bd dolt push
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Verify** all changes are committed AND pushed.

**CRITICAL RULES:**
- NEVER stop before pushing.
- NEVER say "ready to push when you are" — YOU must push.
- If push fails, resolve and retry until it succeeds.
<!-- END MANDATORY WORKFLOW INTEGRATION -->

## Derived from AGENTS.md

This file adapts the shared `AGENTS.md` operating instructions for Gemini.
Read `AGENTS.md` completely before executing commands, changing code, or running
any trading-related script. If this file and `AGENTS.md` disagree, follow
`AGENTS.md` unless a human explicitly instructs otherwise.

---

## Identity Check

Before proceeding, confirm you can answer YES to all of the following:

- [ ] I have read access to this project folder
- [ ] I have run `bd ready` and claimed/created an issue for my task
- [ ] I will use `bd` for ALL task tracking and will NOT stop until `git push` succeeds
- [ ] I can execute Python scripts via bash/shell
- [ ] I will NOT modify `config/config.yaml` risk parameters without human approval
- [ ] I will NOT switch `mode` from `paper` to `live` without explicit human instruction
- [ ] I understand I am executing real (or simulated) financial trades

---

## Quick-Start Commands

### 1. Install dependencies (first time only)
```bash
cd /path/to/HawksTrade
pip3 install -r requirements.txt --break-system-packages
```

### 2. Setup API keys (first time only)
```bash
cp config/.env.example config/.env
# Human fills in config/.env with their Alpaca API keys.
# A root .env is also supported and takes precedence.
```

### 3. Verify connection
```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from core.alpaca_client import get_account
a = get_account()
print('OK - Portfolio:', a.portfolio_value)
"
```

### 4. Run a scan
```bash
python3 scheduler/run_scan.py --dry-run
```

### 5. Run risk check
```bash
python3 scheduler/run_risk_check.py --dry-run
```

### 6. Generate report
```bash
python3 scheduler/run_report.py
python3 scheduler/run_report.py --weekly
```

---

## Scheduled Operations

When triggered by scheduler at a scan time slot:
```bash
cd /path/to/HawksTrade
python3 scheduler/run_scan.py
```

If it is after 4:00 PM ET or before 9:30 AM ET on a weekday, run crypto-only:
```bash
python3 scheduler/run_scan.py --crypto-only
```

Every 15 minutes during market hours, 9:30 AM to 4:00 PM ET, Monday through Friday:
```bash
cd /path/to/HawksTrade
python3 scheduler/run_risk_check.py
```

Run the daily report at 4:30 PM ET, Monday through Friday:
```bash
cd /path/to/HawksTrade
python3 scheduler/run_report.py
```

Run the weekly report at 8:00 AM ET every Monday:
```bash
cd /path/to/HawksTrade
python3 scheduler/run_report.py --weekly
```

---

## Decision Tree

```text
Is market open (9:30-4:00 ET, Mon-Fri)?
  YES ->
    Run: run_scan.py (every 30 min)
    Run: run_risk_check.py (every 15 min)
    At 4:30 PM: run_report.py
  NO ->
    Run: run_scan.py --crypto-only (every 60 min)
    Skip stock scans and risk checks

Is it Monday at 8:00 AM?
  YES -> Run: run_report.py --weekly

Did any script fail?
  YES -> Log error, retry once after 2 min, report to human if still failing
  NO  -> Continue normal schedule
```

---

## Absolute Rules

1. **Never switch `mode: paper` to `mode: live`** unless the human explicitly says so in chat.
2. **Never change risk parameters** such as stop-loss, position size, or daily loss limit without human approval.
3. **Never place a trade if Alpaca connection fails**. Log the error and skip.
4. **Never ignore a `DailyLossLimitExceeded` condition**. Stop all trading immediately.
5. **Always log every action** using the log files in `logs/`.
6. **Intraday trading is OFF by default**. Do not enable it.

---

## File Locations

| What | Where |
|------|-------|
| Configuration | `config/config.yaml` |
| API Keys | `config/.env` or `.env` created from `config/.env.example` |
| Trade Log | `data/trades.csv` |
| Performance | `data/performance.csv` |
| Reports | `reports/` |
| Logs | `logs/` |

---

## Environment Requirements

- Python 3.10+
- All packages in `requirements.txt`
- `config/.env` or `.env` file with valid Alpaca API keys
- Internet access to `api.alpaca.markets` and `data.alpaca.markets`

---

## Validation Before Push / Deployment

Run these checks before publishing code changes:
```bash
python3 -m unittest discover -v
python3 -W error::DeprecationWarning -m unittest discover
python3 -m compileall core strategies scheduler tracking tests
python3 scheduler/run_scan.py --dry-run
python3 scheduler/run_risk_check.py --dry-run
python3 scheduler/run_report.py
```

Only run a real paper-order lifecycle test when the human explicitly asks for it.

After every code change, run both checks before committing:
```bash
python3 -m unittest discover -v
python3 scheduler/run_backtest.py --days 30 --fund 10000
```

If either fails, fix the issue before proceeding.

---

## Documentation

Update `README.md`, strategy tables, or backtest reports immediately if changes
affect system behavior or performance.

---

## Reporting Back

After each run, summarize:

- Number of signals found
- Number of trades entered or exited
- Current portfolio value and open positions
- Any errors encountered

---
