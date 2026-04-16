param(
    [string]$ProjectDir = "C:\path\to\HawksTrade"
)

$ErrorActionPreference = "Stop"

$Runner = Join-Path $ProjectDir "scheduler\windows\run_hawkstrade_task.ps1"
$Weekdays = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")

function New-HawksAction {
    param([string]$Task)
    New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`" -Task $Task -ProjectDir `"$ProjectDir`""
}

function Register-HawksTask {
    param(
        [string]$Name,
        [string]$Task,
        [object]$Trigger
    )
    $Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable
    Register-ScheduledTask `
        -TaskName $Name `
        -Action (New-HawksAction -Task $Task) `
        -Trigger $Trigger `
        -Settings $Settings `
        -Description "HawksTrade scheduled task: $Task" `
        -Force
}

# Stock scan: once at 6:35 AM, weekdays (Pacific)
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Weekdays -At "06:35"
Register-HawksTask -Name "HawksTrade Stock Scan" -Task "stock-scan" -Trigger $Trigger

# Full scan: hourly from 7:00 AM, 6 runs through 12:00 PM, weekdays (Pacific)
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Weekdays -At "07:00"
$Trigger.Repetition.Interval = "PT1H"
$Trigger.Repetition.Duration = "PT6H"
Register-HawksTask -Name "HawksTrade Full Scan" -Task "full-scan" -Trigger $Trigger

# Risk check: every 15 min from 6:45 AM through 12:45 PM, weekdays (Pacific)
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Weekdays -At "06:45"
$Trigger.Repetition.Interval = "PT15M"
$Trigger.Repetition.Duration = "PT6H"
Register-HawksTask -Name "HawksTrade Risk Check" -Task "risk-check" -Trigger $Trigger

# Crypto scan: every hour, all day every day
$Trigger = New-ScheduledTaskTrigger -Daily -At "00:00"
$Trigger.Repetition.Interval = "PT1H"
$Trigger.Repetition.Duration = "P1D"
Register-HawksTask -Name "HawksTrade Crypto Scan" -Task "crypto-scan" -Trigger $Trigger

# Daily report: 1:30 PM, weekdays (Pacific)
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Weekdays -At "13:30"
Register-HawksTask -Name "HawksTrade Daily Report" -Task "daily-report" -Trigger $Trigger

# Weekly report: 5:00 AM Monday (Pacific)
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek @("Monday") -At "05:00"
Register-HawksTask -Name "HawksTrade Weekly Report" -Task "weekly-report" -Trigger $Trigger

Write-Host "Registered HawksTrade scheduled tasks for $ProjectDir"
