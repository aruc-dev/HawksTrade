# HawksTrade — AI Agent Operating Manual
## For Claude (Anthropic) and Compatible AI Agents

> This file is the **single source of truth** for any AI agent running HawksTrade.
> Read this entire file before taking any action.

> **Timezone:** All schedule times in this file are given in **ET (Eastern Time)**, because
> US stock markets operate on ET. The system this was built on runs **PDT (UTC−7)**.
> If you are an AI agent running scheduled tasks, see `SCHEDULED_TASKS.md` for the
> correct cron expressions in ET, PDT, and UTC — and for full instructions on
> recreating these automations on any new system.

---

## 1. What Is HawksTrade?

HawksTrade is an automated trading bot for US stocks and major cryptocurrencies.
It uses the Alpaca brokerage API and runs a suite of technical trading strategies
on a scheduled basis. An AI agent (Claude, Codex, or similar) is responsible for
running the scheduled scripts at the correct times and handling any errors.

**The AI agent's role:**
- Execute the correct Python scripts at the correct times
- Monitor logs for errors and act on them
- Never modify core trading logic or risk parameters without explicit human instruction
- Always report trade activity and errors clearly

---

## 2. First-Time Setup (Do This Once)

### 2a. Check Python environment

```bash
cd /path/to/HawksTrade
python3 --version           # Must be 3.10+
pip3 install -r requirements.txt --break-system-packages
```

### 2b. Create your env file

```bash
cp config/.env.example config/.env
# Then edit config/.env and add your Alpaca API keys.
# A root .env file is also supported and takes precedence.
```

The env file must contain:
```
ALPACA_PAPER_API_KEY=...
ALPACA_PAPER_SECRET_KEY=...
ALPACA_LIVE_API_KEY=...      # Leave blank until ready for live trading
ALPACA_LIVE_SECRET_KEY=...   # Leave blank until ready for live trading
```

## Validation After Every Change

After EVERY code change, you MUST run both of the following before committing:

1. **Unit tests** — must pass with zero failures:
   ```bash
   python3 -m unittest discover -v
   ```

2. **1-month backtest** — must complete and produce a trades report (not "No trades executed."):
   ```bash
   python3 scheduler/run_backtest.py --days 30 --fund 10000
   ```

If either check fails, fix the issue before proceeding. Do not commit broken code.

## Output Persistence

### 2c. Verify the connection

```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from core.alpaca_client import get_account
a = get_account()
print('Connected! Portfolio value:', a.portfolio_value)
"
```

If this fails: check your `config/.env` or `.env` keys and the `mode` setting in `config/config.yaml`.

### 2d. Set trading mode

Open `config/config.yaml` and check:
```yaml
mode: paper    # "paper" = simulation (safe), "live" = real money
```

**Always start with `paper`. Only change to `live` after successful paper trading.**

---

## 3. Trading Mode Reference

| Setting | Meaning |
|---------|---------|
| `mode: paper` | Uses Alpaca paper trading endpoint. No real money. |
| `mode: live`  | Uses Alpaca live endpoint. **REAL MONEY. Use with care.** |

To switch: edit `config/config.yaml` → change `mode:` value → restart scripts.

---

## 4. Intraday Trading

```yaml
intraday:
  enabled: false   # DEFAULT: disabled
```

Intraday (same-day) trading is **disabled by default**.
All strategies operate as swing trades (hold 2–5 days).
Do NOT enable intraday unless the human owner explicitly requests it.

---

## 5. Daily Operating Schedule

The AI agent should run these scripts on this schedule:

### Weekdays (Mon–Fri)

| Time (ET) | Script | Purpose |
|-----------|--------|---------|
| 09:35 AM | `python3 scheduler/run_scan.py --stocks-only` | First stock scan after open |
| 10:00 AM | `python3 scheduler/run_scan.py` | Full scan (stocks + crypto) |
| 10:30 AM | `python3 scheduler/run_risk_check.py` | Risk check |
| 11:00 AM | `python3 scheduler/run_scan.py` | Full scan |
| 11:30 AM | `python3 scheduler/run_risk_check.py` | Risk check |
| 12:00 PM | `python3 scheduler/run_scan.py` | Full scan |
| 12:30 PM | `python3 scheduler/run_risk_check.py` | Risk check |
| 01:00 PM | `python3 scheduler/run_scan.py` | Full scan |
| 01:30 PM | `python3 scheduler/run_risk_check.py` | Risk check |
| 02:00 PM | `python3 scheduler/run_scan.py` | Full scan |
| 02:30 PM | `python3 scheduler/run_risk_check.py` | Risk check |
| 03:00 PM | `python3 scheduler/run_scan.py` | Full scan |
| 03:30 PM | `python3 scheduler/run_risk_check.py` | Risk check |
| 04:30 PM | `python3 scheduler/run_report.py` | Daily report |
| Every hour (00 min) | `python3 scheduler/run_scan.py --crypto-only` | Crypto-only scan (24/7) |
| Every 15 min (market hours) | `python3 scheduler/run_risk_check.py` | Stop-loss enforcement |
| Monday 08:00 AM | `python3 scheduler/run_report.py --weekly` | Weekly report |

### Weekends / After Hours
- Crypto scans continue every hour (crypto is 24/7).
- No stock scans. No risk checks for stock positions.

---

## 6. Script Reference

| Script | Description | Args |
|--------|-------------|------|
| `scheduler/run_scan.py` | Main scan: signals → entries → exits | `--stocks-only`, `--crypto-only` |
| `scheduler/run_risk_check.py` | Stop-loss / take-profit enforcement | none |
| `scheduler/run_report.py` | Performance & portfolio report | `--weekly` |
| `scheduler/run_backtest.py` | Historical strategy simulation | `--days`, `--fund`, `--exit-policy`, `--screener`, `--no-screener`, `--strategies`, `--set` |

---

## 7. Strategy Summary

All strategies are configured in `config/config.yaml` under `strategies:`.
Each can be individually enabled/disabled.

| Strategy | Asset | Logic |
|----------|-------|-------|
| `momentum` | Stocks | Buy top 3 by 5-day return (min 6%), exit flat/losing trades after 4 trading days, let profitable trades run with trailing protection |
| `rsi_reversion` | Stocks | Disabled by default; buy RSI < 38, sell RSI > 62 |
| `gap_up` | Stocks | Disabled by default; buy on >3% gap-up with 1.5x volume, hold 2 days |
| `ma_crossover` | Crypto | Buy on 9-EMA crossing above 21-EMA (daily bars) |
| `range_breakout` | Crypto | Buy on breakout above prior day high, 1.8x volume |

Momentum backtests can compare `--exit-policy fixed_hold`, `--exit-policy profit_trailing`, and `--exit-policy risk_only_baseline`. Use `risk_only_baseline` only as a benchmark for the old no-hold-exit behavior, not as the default live policy.
Use `--strategies momentum,ma_crossover,range_breakout` and repeated `--set key.path=value` arguments for backtest-only strategy experiments without editing `config/config.yaml`.

---

## 8. Risk Rules (Do Not Override Without Human Approval)

These are set in `config/config.yaml → trading:` and enforced by `core/risk_manager.py`:

| Rule | Value |
|------|-------|
| Max position size | 5% of portfolio per trade |
| Stop-loss | 3.5% below entry |
| Take-profit | 12% above entry |
| Daily loss limit | 5% of portfolio → halt all trading |
| Max open positions | 10 |
| Min trade value | $100 USD |

---

## 9. Files & Data

| Path | Contents |
|------|---------|
| `data/trades.csv` | Every trade (entry + exit) with timestamps, P&L |
| `data/performance.csv` | Periodic performance snapshots |
| `reports/daily_YYYY-MM-DD.txt` | Daily report files |
| `reports/weekly_YYYY-WNN.txt` | Weekly report files |
| `logs/scan_YYYYMMDD.log` | Scan logs per day |
| `logs/risk_YYYYMMDD.log` | Risk check logs per day |
| `logs/report_YYYYMMDD.log` | Report logs per day |

`data/`, `reports/`, and `logs/` are runtime artifacts and should not be committed.

---

## 10. Error Handling Protocol

If a script fails, the AI agent should:

1. **Read the error message** from stdout/stderr or the log file.
2. **Classify the error:**
   - `EnvironmentError` / missing keys → remind the human to fill in `config/.env` or `.env`
   - `ConnectionError` / `APIError` → retry after 2 minutes, max 3 attempts
   - `InsufficientFunds` → log warning, skip trade, continue
   - Any unrecognised exception → log to `logs/errors.log`, report to human
3. **Never silently ignore errors** — always log them.
4. **Do not place trades if the connection to Alpaca cannot be verified.**

---

## 11. Cloud / VM Deployment

To run HawksTrade on a Linux cloud VM (AWS, GCP, etc.):

```bash
# 1. Copy the HawksTrade folder to the VM
scp -r ./HawksTrade user@vm-ip:~/HawksTrade

# 2. SSH in and install dependencies
ssh user@vm-ip
cd ~/HawksTrade
pip3 install -r requirements.txt --break-system-packages

# 3. Create config/.env with your keys
cp config/.env.example config/.env
nano config/.env   # fill in your keys

# 4. Run with cron or a process manager (e.g. systemd, PM2, supervisor)
# Example cron entries:
# */30 9-16 * * 1-5  cd ~/HawksTrade && python3 scheduler/run_scan.py
# */15 9-16 * * 1-5  cd ~/HawksTrade && python3 scheduler/run_risk_check.py
# 0 * * * *          cd ~/HawksTrade && python3 scheduler/run_scan.py --crypto-only
# 30 16 * * 1-5      cd ~/HawksTrade && python3 scheduler/run_report.py
# 0 8 * * 1          cd ~/HawksTrade && python3 scheduler/run_report.py --weekly
```

---

## 12. Running Inside Claude (Anthropic Cowork)

Claude's scheduled tasks will automatically call the scripts above.
The scheduled task prompts are stored in this project as Claude tasks.
If you are Claude and you are reading this as part of a scheduled task:
  - Run the specified script for this time slot (see Section 5)
  - Report any errors or notable trades back in the session
  - Do not modify any trading parameters unless the user asks

---

## 13. Switching from Paper to Live

Only do this after:
- [ ] At least 30 days of paper trading reviewed
- [ ] Win rate > 50% and positive total P&L
- [ ] Human owner has explicitly said "switch to live"

Steps:
1. Edit `config/config.yaml` → `mode: live`
2. Ensure `ALPACA_LIVE_API_KEY` and `ALPACA_LIVE_SECRET_KEY` are in `config/.env` or `.env`
3. Fund your Alpaca live account
4. Run a manual test: `python3 scheduler/run_scan.py` and verify a real order appears in Alpaca dashboard

---

## 14. Stopping the Bot

To stop all trading immediately:
1. Set `mode: paper` in `config/config.yaml` (safest)
2. Or disable all strategies: set `enabled: false` under each strategy in `config.yaml`
3. Or cancel open orders via Alpaca dashboard

---

## 15. Recreating Automations on a New System

If you are setting up HawksTrade on a new machine (new laptop, cloud VM, or new AI agent),
refer to **`SCHEDULED_TASKS.md`** for:

- All 5 task IDs, cron expressions, and full prompts ready to copy-paste
- Cron expressions in **ET**, **PDT**, and **UTC** — pick the right one for the system
- Instructions for recreating in Claude desktop app, Linux cron, or other AI agents
- A checklist for migrating the entire project to a new system

The project folder is fully self-contained and portable. The only things to update when
moving to a new system are:
1. The **working directory path** in each scheduled task prompt
2. The **cron timezone** to match the new system's local time

---

## 16. Quality & Documentation Mandates

For every modification to the codebase:
1. **Unit Tests**: You MUST implement or update relevant unit tests in the `tests/` directory.
2. **Validation**: Always run the full test suite (`python3 -m unittest discover`) before pushing.
3. **Documentation**: If strategy parameters, risk rules, or core logic change, you MUST update `README.md` and `backtests.md` to reflect the new system state.

---

*HawksTrade — Built for automation. Respect the risk rules.*


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
