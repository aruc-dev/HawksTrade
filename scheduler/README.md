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
saving time. Convert times if the host machine runs in another timezone.

| Task | Command | Eastern Time | Pacific Time |
|------|---------|--------------|--------------|
| First stock scan | `python3 scheduler/run_scan.py --stocks-only` | 9:35 AM Mon-Fri | 6:35 AM Mon-Fri |
| Full scan | `python3 scheduler/run_scan.py` | 10:00 AM-3:00 PM hourly Mon-Fri | 7:00 AM-12:00 PM hourly Mon-Fri |
| Risk check | `python3 scheduler/run_risk_check.py` | 9:45 AM-3:45 PM every 15 min Mon-Fri | 6:45 AM-12:45 PM every 15 min Mon-Fri |
| Crypto scan | `python3 scheduler/run_scan.py --crypto-only` | Hourly, every day | Hourly, every day |
| Daily report | `python3 scheduler/run_report.py` | 4:30 PM Mon-Fri | 1:30 PM Mon-Fri |
| Weekly report | `python3 scheduler/run_report.py --weekly` | 8:00 AM Monday | 5:00 AM Monday |

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
