# HawksTrade Scheduler Setup

This directory contains OS-level scheduler templates for running HawksTrade without
creating a new Codex thread for every run.

Use OS schedulers for operational trading tasks:

- macOS: `launchd`
- Linux: `cron`
- Windows: Task Scheduler through PowerShell

Codex or Claude automations can still be useful for summaries, but stop-loss
enforcement should be owned by the OS scheduler so it runs independently of an AI app.

## Task Summary

All schedules below assume the original laptop runs in Pacific time during US daylight
saving time. Convert times if the host machine runs in another timezone. The
commands shown are the Linux cron commands; macOS and Windows runners are
documented in their own sections below.

| Task | Command | Eastern Time | Pacific Time |
|------|---------|--------------|--------------|
| First stock scan | `./scripts/run_hawkstrade_job.sh scheduler/run_scan.py --stocks-only` | 9:35 AM Mon-Fri | 6:35 AM Mon-Fri |
| Full scan | `./scripts/run_hawkstrade_job.sh scheduler/run_scan.py` | 10:00 AM-3:00 PM hourly Mon-Fri | 7:00 AM-12:00 PM hourly Mon-Fri |
| Risk check | `./scripts/run_hawkstrade_job.sh scheduler/run_risk_check.py` | 9:45 AM-3:45 PM every 15 min Mon-Fri | 6:45 AM-12:45 PM every 15 min Mon-Fri |
| Crypto scan | `./scripts/run_hawkstrade_job.sh scheduler/run_scan.py --crypto-only` | Hourly, every day | Hourly, every day |
| Daily report | `./scripts/run_hawkstrade_job.sh scheduler/run_report.py` | 4:30 PM Mon-Fri | 1:30 PM Mon-Fri |
| Weekly report | `./scripts/run_hawkstrade_job.sh scheduler/run_report.py --weekly` | 8:00 AM Monday | 5:00 AM Monday |

Before enabling schedules:

1. Confirm dependencies are installed: `pip3 install -r requirements.txt`.
2. Confirm Alpaca credentials exist in `.env` or `config/.env`.
3. Confirm `config/config.yaml` is still `mode: paper` unless live mode was explicitly approved.
4. Run dry checks:

```bash
python3 scheduler/run_scan.py --dry-run
python3 scheduler/run_risk_check.py --dry-run
python3 scheduler/run_report.py
```

## macOS Launchd

Template files:

- `scheduler/launchd/com.hawkstrade.stock-scan.plist`
- `scheduler/launchd/com.hawkstrade.full-scan.plist`
- `scheduler/launchd/com.hawkstrade.risk-check.plist`
- `scheduler/launchd/com.hawkstrade.crypto-scan.plist`
- `scheduler/launchd/com.hawkstrade.daily-report.plist`
- `scheduler/launchd/com.hawkstrade.weekly-report.plist`
- `scheduler/launchd/hawkstrade_launchd_runner.sh`

The templates use `/path/to/HawksTrade` as a placeholder.
Replace that placeholder with the actual project path before installing:

```bash
export PROJECT=/actual/path/to/HawksTrade
# macOS/BSD sed requires an empty backup-suffix argument.
# On Linux (GNU sed), use: sed -i "s|...|...|g" (no empty string).
sed -i '' "s|/path/to/HawksTrade|$PROJECT|g" \
    scheduler/launchd/com.hawkstrade.*.plist \
    scheduler/launchd/hawkstrade_launchd_runner.sh
```

Or set the `HAWKSTRADE_DIR` environment variable; the runner script reads it automatically.

Install:

```bash
cd /path/to/HawksTrade
chmod +x scheduler/launchd/hawkstrade_launchd_runner.sh
cp scheduler/launchd/com.hawkstrade.*.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hawkstrade.stock-scan.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hawkstrade.full-scan.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hawkstrade.risk-check.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hawkstrade.crypto-scan.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hawkstrade.daily-report.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hawkstrade.weekly-report.plist
```

Check loaded jobs:

```bash
launchctl print gui/$(id -u) | grep hawkstrade
```

Run a safe dry test manually:

```bash
python3 scheduler/run_risk_check.py --dry-run
```

Unload:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.hawkstrade.stock-scan.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.hawkstrade.full-scan.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.hawkstrade.risk-check.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.hawkstrade.crypto-scan.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.hawkstrade.daily-report.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.hawkstrade.weekly-report.plist
```

Logs:

- Script logs: `logs/scan_YYYYMMDD.log`, `logs/risk_YYYYMMDD.log`, `logs/report_YYYYMMDD.log`
- launchd stdout/stderr: `logs/launchd_*.log`, `logs/launchd_*.err`

macOS caveat: user `LaunchAgents` run while the user session is available. If the Mac
sleeps, executions may be missed or delayed.

## Linux Cron

Templates:

- `scheduler/cron/hawkstrade-eastern.cron`
- `scheduler/cron/hawkstrade-pacific.cron`
- `scheduler/cron/hawkstrade-utc.cron`

Edit `HAWKSTRADE_DIR` in the chosen file, then install:

```bash
crontab scheduler/cron/hawkstrade-eastern.cron
```

The Linux cron templates run scheduled jobs through
`scripts/run_hawkstrade_job.sh`, so scan, risk-check, and report jobs use the
same Python environment selection. The wrapper chooses `.venv` when available.
Before running the job, it verifies Alpaca credentials and connectivity with a
broker clock call; failures are logged as `status=preflight_failed` and the job
is not executed. It also uses a shared `flock` lock at
`local/locks/trade-mutating-jobs.lock` for trade-mutating scan and risk-check
jobs, so they cannot overlap while placing or closing orders. Full scans, stock
scans, and risk checks wait up to 10 minutes for the lock; redundant
`--crypto-only` runs skip when another trade-mutating job is already active.
Report jobs use the same wrapper but do not take the trade-mutation lock.

The Linux cron templates also set `HAWKSTRADE_REQUIRE_SHM=1`. When
`config/config.yaml` uses `secrets_source: shm`, that environment guard makes
cron jobs fail clearly instead of falling back to disk dotenv files if
`/dev/shm/.hawkstrade.env` is missing, unreadable, or rejected by
`HAWKSTRADE_SHM_MAX_AGE_SECONDS`.

View installed jobs:

```bash
crontab -l
```

Remove installed jobs:

```bash
crontab -r
```

For Linux servers, prefer a host timezone that matches the selected template. The UTC
template is written for US daylight saving time. Re-check offsets when the US switches
between daylight and standard time.

## Linux Health Check

Run the health dashboard script to inspect cron health, Alpaca connectivity, open
positions, and realized/unrealized P&L:

```bash
python3 scripts/check_health_linux.py
```

By default it inspects the last 4 hours of cron and log activity and writes
`reports/health_check_linux.html`. Use `--hours 8` to widen the health window,
`--cron-template` or `--cron-file` if the installed cron schedule differs from
the host timezone. Terminal output uses plain status tags like `[OK]`, `[WARN]`,
and `[NOK]` so it stays readable in cron logs and copied output. The HTML report
includes the generation time, the active lookback window, and troubleshooting
sections for the latest warnings and errors.

Risk checks persist consecutive latest-price failures in
`data/price_fetch_failures.json`. The health checker displays those failures in
the terminal and HTML reports, and marks the system `[NOK]` once a symbol reaches
the alert threshold. A successful later price fetch clears that symbol's failure
count.

Scans and risk checks reconcile `data/trades.csv` with broker positions after
non-dry-run execution. Reports and the Linux health checker reconcile before
building summaries when Alpaca is reachable, so local open rows stay aligned with
actual broker exposure without a separate manual reconciliation run.

The scheduled entrypoints now emit structured `RUN_START` / `RUN_END` markers
with unique `run_id` values. The health checker uses those markers first and
falls back to the older log-text parser for historical logs. The hourly full
scan and crypto scan are still evaluated as one combined cycle when both are
scheduled, which avoids false missed-run alerts from overlapping cron slots.

## Windows Task Scheduler

Templates:

- `scheduler/windows/run_hawkstrade_task.ps1`
- `scheduler/windows/register_hawkstrade_tasks.ps1`

Install from an elevated PowerShell session. Replace the project path first.

If you have not already allowed locally-authored scripts to run, set the execution policy once (per user):

```powershell
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Then register the tasks:

```powershell
cd C:\path\to\HawksTrade
powershell -ExecutionPolicy RemoteSigned -File scheduler\windows\register_hawkstrade_tasks.ps1 -ProjectDir "C:\path\to\HawksTrade"
```

The Windows script registers Pacific-time tasks. If the Windows host runs Eastern or
UTC, edit the `-At` times in `register_hawkstrade_tasks.ps1` before installing.

Check tasks:

```powershell
Get-ScheduledTask -TaskName "HawksTrade*"
```

Run a safe dry test manually:

```powershell
py scheduler/run_risk_check.py --dry-run
```

Remove tasks:

```powershell
Get-ScheduledTask -TaskName "HawksTrade*" | Unregister-ScheduledTask -Confirm:$false
```

## AI App Automations

AI app automations are not recommended for frequent risk enforcement because each run
can create a separate app thread. If you still recreate them, use exact one-time
schedules for mixed hour/minute patterns. Do not collapse risk checks into a single
RRULE such as:

```text
FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=6,7,8,9,10,11,12;BYMINUTE=0,15,30,45
```

Some schedulers interpret that as only the first matching occurrence.

## Moving to a New System

1. Copy the full `HawksTrade/` folder.
2. Install Python 3.10+ and dependencies.
3. Copy `.env` or `config/.env`, or recreate it from `config/.env.example`.
4. Update scheduler template paths.
5. Install the scheduler for the host OS.
6. Run:

```bash
python3 -c "import sys; sys.path.insert(0,'.'); from core.alpaca_client import get_account; print('OK:', get_account().portfolio_value)"
python3 scheduler/run_scan.py --dry-run
python3 scheduler/run_risk_check.py --dry-run
python3 -m unittest discover -v
```
