#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/run_hawkscapitol_refresh.sh [--dry-run]

Refresh HawksCapitol scored signal data from the HawksCapitol submodule before a
HawksTrade capitol_copy scan. By default this runs HawksCapitol ingest, score,
and scan entrypoints in order, then verifies data/signals/latest.json exists.

Environment overrides:
  HAWKSTRADE_CAPITOL_DIR              HawksCapitol checkout path
  HAWKSTRADE_CAPITOL_SIGNAL_PATH      Signal file HawksTrade will read
  HAWKSTRADE_CAPITOL_REFRESH_COMMAND  Custom shell command to run in the submodule
  HAWKSCAPITOL_PYTHON                 Python executable for HawksCapitol
EOF
}

DRY_RUN=0
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
elif [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=1
    shift
fi

if [[ $# -ne 0 ]]; then
    usage >&2
    exit 64
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HAWKSTRADE_DIR="${HAWKSTRADE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
CAPITOL_DIR="${HAWKSTRADE_CAPITOL_DIR:-$HAWKSTRADE_DIR/integrations/HawksCapitol}"
DEFAULT_SIGNAL_FILE="$CAPITOL_DIR/data/signals/latest.json"
SIGNAL_FILE="${HAWKSTRADE_CAPITOL_SIGNAL_PATH:-$DEFAULT_SIGNAL_FILE}"
LOCK_FILE="${HAWKSTRADE_CAPITOL_REFRESH_LOCK_FILE:-$HAWKSTRADE_DIR/local/locks/hawkscapitol-refresh.lock}"
LOCK_TIMEOUT_SECONDS="${HAWKSTRADE_CAPITOL_REFRESH_LOCK_TIMEOUT_SECONDS:-600}"

mkdir -p "$HAWKSTRADE_DIR/logs" "$HAWKSTRADE_DIR/local/locks"

if [[ ! -f "$CAPITOL_DIR/scheduler/run_scan.py" ]]; then
    echo "[hawkscapitol-refresh] ERROR: HawksCapitol submodule is not initialized at $CAPITOL_DIR." >&2
    echo "[hawkscapitol-refresh] Run: git submodule update --init --recursive" >&2
    exit 69
fi

if [[ -n "${HAWKSCAPITOL_PYTHON:-}" ]]; then
    PYTHON_BIN="$HAWKSCAPITOL_PYTHON"
elif [[ -x "$CAPITOL_DIR/.venv/bin/python3" ]]; then
    PYTHON_BIN="$CAPITOL_DIR/.venv/bin/python3"
elif [[ -x "$CAPITOL_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$CAPITOL_DIR/.venv/bin/python"
elif [[ -x "$HAWKSTRADE_DIR/.venv/bin/python3" ]]; then
    PYTHON_BIN="$HAWKSTRADE_DIR/.venv/bin/python3"
elif [[ -x "$HAWKSTRADE_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$HAWKSTRADE_DIR/.venv/bin/python"
else
    PYTHON_BIN="${PYTHON:-python3}"
fi

START_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "[hawkscapitol-refresh] REFRESH_START started_at=$START_UTC dry_run=$DRY_RUN capitol_dir=$CAPITOL_DIR signal_file=$SIGNAL_FILE python=$PYTHON_BIN"

refresh_once() {
    cd "$CAPITOL_DIR"
    if [[ -n "${HAWKSTRADE_CAPITOL_REFRESH_COMMAND:-}" ]]; then
        bash -lc "$HAWKSTRADE_CAPITOL_REFRESH_COMMAND"
    else
        local dry_run_arg=()
        if [[ "$DRY_RUN" -eq 1 ]]; then
            dry_run_arg=(--dry-run)
        fi
        "$PYTHON_BIN" scheduler/run_ingest.py "${dry_run_arg[@]}"
        "$PYTHON_BIN" scheduler/run_score.py "${dry_run_arg[@]}"
        "$PYTHON_BIN" scheduler/run_scan.py "${dry_run_arg[@]}"
    fi
}

export CAPITOL_DIR PYTHON_BIN DRY_RUN
set +e
if command -v flock >/dev/null 2>&1; then
    flock -w "$LOCK_TIMEOUT_SECONDS" -E 75 "$LOCK_FILE" bash -c "$(declare -f refresh_once); refresh_once"
    STATUS=$?
else
    LOCK_DIR="${LOCK_FILE}.d"
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        refresh_once
        STATUS=$?
        rmdir "$LOCK_DIR" 2>/dev/null || true
    else
        echo "[hawkscapitol-refresh] ERROR: refresh lock is busy: $LOCK_DIR" >&2
        STATUS=75
    fi
fi
set -e

if [[ "$STATUS" -ne 0 ]]; then
    END_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    echo "[hawkscapitol-refresh] REFRESH_END ended_at=$END_UTC status=$STATUS"
    exit "$STATUS"
fi

if [[ "$DRY_RUN" -eq 0 ]]; then
    if [[ "$SIGNAL_FILE" != "$DEFAULT_SIGNAL_FILE" && -s "$DEFAULT_SIGNAL_FILE" ]]; then
        mkdir -p "$(dirname "$SIGNAL_FILE")"
        cp "$DEFAULT_SIGNAL_FILE" "$SIGNAL_FILE"
    fi

    "$PYTHON_BIN" - "$SIGNAL_FILE" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists() or path.stat().st_size == 0:
    print(f"[hawkscapitol-refresh] ERROR: signal file missing or empty: {path}", file=sys.stderr)
    raise SystemExit(70)

try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"[hawkscapitol-refresh] ERROR: signal file is not valid JSON: {path}: {exc}", file=sys.stderr)
    raise SystemExit(70)

rows = payload.get("signals", []) if isinstance(payload, dict) else payload
if not isinstance(rows, list):
    print(f"[hawkscapitol-refresh] ERROR: signal file does not contain a signal list: {path}", file=sys.stderr)
    raise SystemExit(70)

print(f"[hawkscapitol-refresh] SIGNAL_FILE_OK path={path} signals={len(rows)}")
PY
fi

END_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "[hawkscapitol-refresh] REFRESH_END ended_at=$END_UTC status=ok dry_run=$DRY_RUN"
