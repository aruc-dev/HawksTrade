param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("stock-scan", "full-scan", "risk-check", "crypto-scan", "daily-report", "weekly-report")]
    [string]$Task,

    [string]$ProjectDir = "C:\path\to\HawksTrade"
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectDir

switch ($Task) {
    "stock-scan"    { py scheduler/run_scan.py --stocks-only }
    "full-scan"     { py scheduler/run_scan.py }
    "risk-check"    { py scheduler/run_risk_check.py }
    "crypto-scan"   { py scheduler/run_scan.py --crypto-only }
    "daily-report"  { py scheduler/run_report.py }
    "weekly-report" { py scheduler/run_report.py --weekly }
}
