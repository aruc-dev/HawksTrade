#!/usr/bin/env bash
# ============================================================
# HawksTrade — Status UI background loop runner
# ============================================================
# Run this in a separate terminal (or detach with &) to keep
# the status.html refreshed without using cron.
#
# Usage:
#   bash status_ui/run_status_generator.sh
#   bash status_ui/run_status_generator.sh 120        # 120-second interval
#
# The script lives in status_ui/ inside the HawksTrade project root.
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
GENERATOR="$SCRIPT_DIR/generate_status.py"
INTERVAL="${1:-120}"   # default 120 seconds

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
