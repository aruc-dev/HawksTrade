param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("stock-scan", "full-scan", "risk-check", "crypto-scan", "daily-report", "weekly-report")]
    [string]$Task,

    [string]$ProjectDir = "C:\path\to\HawksTrade"
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectDir

switch ($Task) {
    "stock-scan"    { python scheduler/run_scan.py --stocks-only }
    "full-scan"     { python scheduler/run_scan.py }
    "risk-check"    { python scheduler/run_risk_check.py }
    "crypto-scan"   { python scheduler/run_scan.py --crypto-only }
    "daily-report"  { python scheduler/run_report.py }
    "weekly-report" { python scheduler/run_report.py --weekly }
}
