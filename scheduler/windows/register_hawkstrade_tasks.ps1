param(
    [string]$ProjectDir = "C:\path\to\HawksTrade"
)

$ErrorActionPreference = "Stop"

$Runner = Join-Path $ProjectDir "scheduler\windows\run_hawkstrade_task.ps1"
$Days = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")

function Register-HawksTradeTask {
    param(
        [string]$Name,
        [string]$Task,
        [DateTime]$At,
        [string[]]$DaysOfWeek = $Days
    )

    $Action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`" -Task $Task -ProjectDir `"$ProjectDir`""

    $Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $DaysOfWeek -At $At
    $Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable

    Register-ScheduledTask `
        -TaskName $Name `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Description "HawksTrade scheduled task: $Task" `
        -Force
}

Register-HawksTradeTask -Name "HawksTrade Stock Scan 0635" -Task "stock-scan" -At "06:35"

foreach ($hour in 7..12) {
    Register-HawksTradeTask -Name ("HawksTrade Full Scan {0:00}00" -f $hour) -Task "full-scan" -At ("{0:00}:00" -f $hour)
}

Register-HawksTradeTask -Name "HawksTrade Risk Check 0645" -Task "risk-check" -At "06:45"
foreach ($hour in 7..12) {
    foreach ($minute in 0, 15, 30, 45) {
        Register-HawksTradeTask -Name ("HawksTrade Risk Check {0:00}{1:00}" -f $hour, $minute) -Task "risk-check" -At ("{0:00}:{1:00}" -f $hour, $minute)
    }
}

foreach ($hour in 0..23) {
    Register-HawksTradeTask -Name ("HawksTrade Crypto Scan {0:00}00" -f $hour) -Task "crypto-scan" -At ("{0:00}:00" -f $hour) -DaysOfWeek @("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
}

Register-HawksTradeTask -Name "HawksTrade Daily Report 1330" -Task "daily-report" -At "13:30"
Register-HawksTradeTask -Name "HawksTrade Weekly Report Monday 0500" -Task "weekly-report" -At "05:00" -DaysOfWeek @("Monday")

Write-Host "Registered HawksTrade scheduled tasks for $ProjectDir"
