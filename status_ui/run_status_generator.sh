#!/usr/bin/env bash
# ============================================================
# HawksTrade — Status UI background loop runner
# ============================================================
# Usage:
#   bash status_ui/run_status_generator.sh
#   bash status_ui/run_status_generator.sh 120    # 120-second interval
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
GENERATOR="$SCRIPT_DIR/generate_status.py"
INTERVAL="${1:-120}"

# Validate INTERVAL is a positive integer
case "$INTERVAL" in
    ''|*[!0-9]*)
        echo "Error: interval must be a positive integer (seconds)." >&2
        echo "Usage: bash status_ui/run_status_generator.sh [positive-integer-seconds]" >&2
        exit 1
        ;;
esac

if [ "$INTERVAL" -le 0 ]; then
    echo "Error: interval must be greater than 0." >&2
    echo "Usage: bash status_ui/run_status_generator.sh [positive-integer-seconds]" >&2
    exit 1
fi

echo "[HawksTrade Status] Starting generator loop (interval=${INTERVAL}s)"
echo "[HawksTrade Status] Project dir: $PROJECT_DIR"
echo "[HawksTrade Status] Output:      $SCRIPT_DIR/status.html"
echo "[HawksTrade Status] Press Ctrl+C to stop."
echo ""

while true; do
    python3 "$GENERATOR" --project-dir "$PROJECT_DIR"
    echo "[HawksTrade Status] Sleeping ${INTERVAL}s …"
    sleep "$INTERVAL"
done
