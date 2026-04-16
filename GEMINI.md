# HawksTrade — Gemini Agent Instructions
## Derived from AGENTS.md

This file adapts the shared `AGENTS.md` operating instructions for Gemini.
Read `AGENTS.md` completely before executing commands, changing code, or running
any trading-related script. If this file and `AGENTS.md` disagree, follow
`AGENTS.md` unless a human explicitly instructs otherwise.

---

## Identity Check

Before proceeding, confirm you can answer YES to all of the following:

- [ ] I have read access to this project folder
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

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules during AI-based development in this repo

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY DEVELOPMENT WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd dolt push
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
