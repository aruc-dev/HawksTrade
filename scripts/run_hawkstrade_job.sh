#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/run_hawkstrade_job.sh <scheduler-script> [args...]

Examples:
  scripts/run_hawkstrade_job.sh scheduler/run_scan.py --crypto-only
  scripts/run_hawkstrade_job.sh scheduler/run_risk_check.py

Every job uses the project .venv when available, verifies Alpaca credentials and
connectivity before execution, and logs a RUNNER_END status. Scan and risk-check
jobs are protected by one shared flock lock. Full scans, stock scans, and risk
checks wait for the lock; crypto-only scans skip if another trade-mutating job is
already active.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

if [[ $# -lt 1 ]]; then
    usage >&2
    exit 64
fi

if ! command -v flock >/dev/null 2>&1; then
    echo "[hawkstrade-runner] ERROR: flock is required but was not found in PATH." >&2
    exit 69
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${HAWKSTRADE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
TARGET="$1"
shift

case "$TARGET" in
    scheduler/run_scan.py|scheduler/run_risk_check.py|scheduler/run_report.py)
        ;;
    *)
        echo "[hawkstrade-runner] ERROR: unsupported scheduler script: $TARGET" >&2
        exit 64
        ;;
esac

cd "$PROJECT_DIR"
mkdir -p logs local/locks

if [[ -x ".venv/bin/python3" ]]; then
    PYTHON_BIN=".venv/bin/python3"
elif [[ -x ".venv/bin/python" ]]; then
    PYTHON_BIN=".venv/bin/python"
else
    PYTHON_BIN="${PYTHON:-python3}"
fi

START_EPOCH="$(date +%s)"
START_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
LOCK_FILE="${HAWKSTRADE_TRADE_LOCK_FILE:-$PROJECT_DIR/local/locks/trade-mutating-jobs.lock}"
LOCK_TIMEOUT_SECONDS="${HAWKSTRADE_LOCK_TIMEOUT_SECONDS:-600}"
LOCK_REQUIRED=0
LOCK_MODE="none"
CRYPTO_ONLY=0

for arg in "$@"; do
    if [[ "$arg" == "--crypto-only" ]]; then
        CRYPTO_ONLY=1
    fi
done

case "$TARGET" in
    scheduler/run_scan.py|scheduler/run_risk_check.py)
        LOCK_REQUIRED=1
        LOCK_MODE="wait"
        if [[ "$TARGET" == "scheduler/run_scan.py" && "$CRYPTO_ONLY" -eq 1 ]]; then
            LOCK_MODE="skip_if_busy"
        fi
        ;;
esac

COMMAND=("$PYTHON_BIN" "$TARGET" "$@")
printf -v COMMAND_TEXT "%q " "${COMMAND[@]}"

echo "[hawkstrade-runner] RUNNER_START started_at=$START_UTC lock_required=$LOCK_REQUIRED lock_mode=$LOCK_MODE target=$TARGET command=${COMMAND_TEXT}"

echo "[hawkstrade-runner] PREFLIGHT_START target=$TARGET python=$PYTHON_BIN"
set +e
"$PYTHON_BIN" - "$TARGET" <<'PY'
import sys

target = sys.argv[1]
try:
    from core import alpaca_client as ac
    clock = ac.get_trading_client().get_clock()
except Exception as exc:
    print(
        "[hawkstrade-runner] ERROR: preflight failed "
        f"target={target} error_type={type(exc).__name__} error={exc}",
        file=sys.stderr,
    )
    raise SystemExit(70)

print(
    "[hawkstrade-runner] PREFLIGHT_OK "
    f"target={target} market_open={getattr(clock, 'is_open', 'unknown')}"
)
PY
PREFLIGHT_STATUS=$?
set -e

if [[ "$PREFLIGHT_STATUS" -ne 0 ]]; then
    END_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    END_EPOCH="$(date +%s)"
    DURATION_S=$((END_EPOCH - START_EPOCH))
    echo "[hawkstrade-runner] RUNNER_END ended_at=$END_UTC status=preflight_failed exit_code=$PREFLIGHT_STATUS duration_s=$DURATION_S target=$TARGET"
    exit "$PREFLIGHT_STATUS"
fi

set +e
if [[ "$LOCK_REQUIRED" -eq 1 ]]; then
    if [[ "$LOCK_MODE" == "skip_if_busy" ]]; then
        flock -n -E 75 "$LOCK_FILE" "${COMMAND[@]}"
    else
        flock -w "$LOCK_TIMEOUT_SECONDS" -E 75 "$LOCK_FILE" "${COMMAND[@]}"
    fi
else
    "${COMMAND[@]}"
fi
STATUS=$?
set -e

END_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
END_EPOCH="$(date +%s)"
DURATION_S=$((END_EPOCH - START_EPOCH))

if [[ "$STATUS" -eq 75 && "$LOCK_REQUIRED" -eq 1 ]]; then
    if [[ "$LOCK_MODE" == "skip_if_busy" ]]; then
        LOCK_STATUS="lock_busy_skip"
    else
        LOCK_STATUS="lock_timeout"
    fi
    echo "[hawkstrade-runner] RUNNER_END ended_at=$END_UTC status=$LOCK_STATUS duration_s=$DURATION_S target=$TARGET lock_file=$LOCK_FILE"
else
    echo "[hawkstrade-runner] RUNNER_END ended_at=$END_UTC status=$STATUS duration_s=$DURATION_S target=$TARGET"
fi

exit "$STATUS"
