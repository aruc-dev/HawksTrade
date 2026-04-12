# HawksTrade — AGENTS.md
## Universal AI Agent Instruction File
### Compatible with: Claude, OpenAI Codex, GPT-4o, Gemini, and any code-executing AI agent

---

## Purpose

This file tells any AI agent how to operate the HawksTrade automated trading bot.
Read this file completely before executing any commands or placing any trades.

The full operating manual is in `CLAUDE.md` — this file is a concise task-oriented
version for agents that need quick, structured instructions.

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
pip install -r requirements.txt --break-system-packages
```

### 2. Setup API keys (first time only)
```bash
cp config/.env.example config/.env
# Human fills in config/.env with their Alpaca API keys.
# A root .env is also supported and takes precedence.
```

### 3. Verify connection
```bash
python -c "
import sys; sys.path.insert(0, '.')
from core.alpaca_client import get_account
a = get_account()
print('OK - Portfolio:', a.portfolio_value)
"
```

### 4. Run a scan
```bash
python scheduler/run_scan.py --dry-run
```

### 5. Run risk check
```bash
python scheduler/run_risk_check.py --dry-run
```

### 6. Generate report
```bash
python scheduler/run_report.py
python scheduler/run_report.py --weekly
```

---

## Task: Run a Scheduled Scan

When triggered by scheduler at a scan time slot:
```bash
cd /path/to/HawksTrade
python scheduler/run_scan.py
```

If it's after 4:00 PM ET or before 9:30 AM ET on a weekday, run crypto-only:
```bash
python scheduler/run_scan.py --crypto-only
```

---

## Task: Run Risk Check

Every 15 minutes during market hours (9:30 AM – 4:00 PM ET, Mon–Fri):
```bash
cd /path/to/HawksTrade
python scheduler/run_risk_check.py
```

---

## Task: Generate Daily Report

Run at 4:30 PM ET, Mon–Fri:
```bash
cd /path/to/HawksTrade
python scheduler/run_report.py
```

---

## Task: Generate Weekly Report

Run at 8:00 AM ET every Monday:
```bash
cd /path/to/HawksTrade
python scheduler/run_report.py --weekly
```

---

## Decision Tree for AI Agents

```
Is market open (9:30-4:00 ET, Mon-Fri)?
  YES →
    Run: run_scan.py (every 30 min)
    Run: run_risk_check.py (every 15 min)
    At 4:30 PM: run_report.py
  NO →
    Run: run_scan.py --crypto-only (every 60 min)
    Skip stock scans and risk checks

Is it Monday at 8:00 AM?
  YES → Run: run_report.py --weekly

Did any script fail?
  YES → Log error, retry once after 2 min, report to human if still failing
  NO  → Continue normal schedule
```

---

## Absolute Rules (Never Break These)

1. **Never switch `mode: paper` to `mode: live`** unless the human explicitly says so in chat.
2. **Never change risk parameters** (stop-loss, position size, daily loss limit) without human approval.
3. **Never place a trade if Alpaca connection fails** — log the error and skip.
4. **Never ignore a `DailyLossLimitExceeded` condition** — stop all trading immediately.
5. **Always log every action** — use the log files in `logs/`.
6. **Intraday trading is OFF by default** — do not enable it.

---

## File Locations

| What | Where |
|------|-------|
| Configuration | `config/config.yaml` |
| API Keys | `config/.env` or `.env` (created from `config/.env.example`) |
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

---

## Quality & Documentation

You MUST adhere to these standards for every change:
1. **Unit Testing**: Implement or update unit tests in the `tests/` directory for ALL logic changes.
2. **Validation**: Run `python3 -m unittest discover` before committing to ensure zero regressions.
3. **Documentation**: Update `README.md`, strategy tables, or backtest reports immediately if your changes affect system behavior or performance.

---

## Reporting Back to the Human

After each run, summarise:
- Number of signals found
- Number of trades entered / exited
- Current portfolio value and open positions
- Any errors encountered

---

*See CLAUDE.md for the full operating manual.*

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

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

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
