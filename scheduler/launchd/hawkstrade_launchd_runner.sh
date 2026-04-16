#!/bin/zsh
set -euo pipefail

PROJECT_DIR="/Users/arunbabuchandrababu/Desktop/AIPROJECTS/HawksTrade"
LOG_DIR="$PROJECT_DIR/logs"
TASK="${1:-}"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

weekday="$(date +%u)"  # 1=Monday ... 7=Sunday
hhmm="$(date +%H%M)"

is_weekday() {
  [[ "$weekday" -ge 1 && "$weekday" -le 5 ]]
}

case "$TASK" in
  stock-scan)
    is_weekday || exit 0
    exec /usr/bin/env python3 scheduler/run_scan.py --stocks-only
    ;;
  full-scan)
    is_weekday || exit 0
    exec /usr/bin/env python3 scheduler/run_scan.py
    ;;
  risk-check)
    is_weekday || exit 0
    [[ "$hhmm" -ge 0645 && "$hhmm" -le 1245 ]] || exit 0
    exec /usr/bin/env python3 scheduler/run_risk_check.py
    ;;
  crypto-scan)
    exec /usr/bin/env python3 scheduler/run_scan.py --crypto-only
    ;;
  daily-report)
    is_weekday || exit 0
    exec /usr/bin/env python3 scheduler/run_report.py
    ;;
  weekly-report)
    [[ "$weekday" -eq 1 ]] || exit 0
    exec /usr/bin/env python3 scheduler/run_report.py --weekly
    ;;
  *)
    echo "Unknown HawksTrade launchd task: $TASK" >&2
    exit 2
    ;;
esac
