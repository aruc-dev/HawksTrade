#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/run_hawkscapitol_refresh.sh [--dry-run]

Refresh HawksCapitol scored signal data from the HawksCapitol submodule before a
HawksTrade capitol_copy scan. Production deployments should set
HAWKSTRADE_CAPITOL_REFRESH_COMMAND to a real-data signal export command. The
built-in HawksCapitol demo export is blocked unless explicitly allowed. Dry-runs
skip custom refresh commands and run HawksCapitol dry-run entrypoints only.

Environment overrides:
  HAWKSTRADE_CAPITOL_DIR              HawksCapitol checkout path
  HAWKSTRADE_CAPITOL_SIGNAL_PATH      Signal file HawksTrade will read
  HAWKSTRADE_CAPITOL_REFRESH_COMMAND  Custom shell command to run in the submodule
  HAWKSTRADE_CAPITOL_ALLOW_SAMPLE_DATA Allow built-in demo signal export (non-production)
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
LOG_FILE="${HAWKSTRADE_CAPITOL_REFRESH_LOG_FILE:-$HAWKSTRADE_DIR/logs/capitol_refresh_$(date +%Y%m%d).log}"
RUN_ID="hawkscapitol-refresh-$(date -u +"%Y%m%dT%H%M%SZ")-$$"

mkdir -p "$HAWKSTRADE_DIR/logs" "$HAWKSTRADE_DIR/local/locks"
touch "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1

quote_field() {
    printf "%q" "$1"
}

log_line() {
    local level="$1"
    shift
    printf '%s,000 [%s] hawkscapitol_refresh: %s\n' "$(date +"%Y-%m-%d %H:%M:%S")" "$level" "$*"
}

is_truthy() {
    case "${1:-}" in
        1|true|TRUE|yes|YES|on|ON|y|Y) return 0 ;;
        *) return 1 ;;
    esac
}

if [[ ! -f "$CAPITOL_DIR/scheduler/run_scan.py" ]]; then
    log_line ERROR "RUN_START script=hawkscapitol_refresh run_id=$(quote_field "$RUN_ID") dry_run=$DRY_RUN capitol_dir=$(quote_field "$CAPITOL_DIR") signal_file=$(quote_field "$SIGNAL_FILE")"
    log_line ERROR "HawksCapitol submodule is not initialized at $CAPITOL_DIR."
    log_line ERROR "Run: git submodule update --init --recursive"
    log_line ERROR "RUN_END script=hawkscapitol_refresh run_id=$(quote_field "$RUN_ID") status=error exit_code=69 dry_run=$DRY_RUN"
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

file_mtime_ns() {
    "$PYTHON_BIN" - "$1" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
print(path.stat().st_mtime_ns if path.exists() else -1)
PY
}

write_sample_signals() {
    local output_file="$1"
    "$PYTHON_BIN" - "$output_file" <<'PY'
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from analytics.member_score import compute_member_scores
from core.config_loader import load_config
from core.sample_data import sample_as_of, sample_sector_map, sample_transactions
from core.serialization import to_jsonable
from engine.copy_signal import build_copy_signals

output = Path(sys.argv[1])
cfg = load_config()
txs = sample_transactions()
as_of = sample_as_of()
scores = compute_member_scores(txs, as_of)
signals = build_copy_signals(txs, scores, cfg, sample_sector_map(), as_of)
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(to_jsonable(signals), indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps({"signals": len(signals), "output": str(output)}, sort_keys=True))
PY
}

START_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
SIGNAL_PRE_REFRESH_MTIME_NS="$(file_mtime_ns "$SIGNAL_FILE")"
DEFAULT_SIGNAL_PRE_REFRESH_MTIME_NS="$(file_mtime_ns "$DEFAULT_SIGNAL_FILE")"
log_line INFO "RUN_START script=hawkscapitol_refresh run_id=$(quote_field "$RUN_ID") started_at=$(quote_field "$START_UTC") dry_run=$DRY_RUN capitol_dir=$(quote_field "$CAPITOL_DIR") signal_file=$(quote_field "$SIGNAL_FILE") python=$(quote_field "$PYTHON_BIN")"

refresh_once() {
    cd "$CAPITOL_DIR"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        if [[ -n "${HAWKSTRADE_CAPITOL_REFRESH_COMMAND:-}" ]]; then
            log_line INFO "Skipping HAWKSTRADE_CAPITOL_REFRESH_COMMAND during dry-run to avoid refresh side effects."
        fi
        "$PYTHON_BIN" scheduler/run_ingest.py --dry-run
        "$PYTHON_BIN" scheduler/run_score.py --dry-run
        "$PYTHON_BIN" scheduler/run_scan.py --dry-run
        return
    fi

    if [[ -n "${HAWKSTRADE_CAPITOL_REFRESH_COMMAND:-}" ]]; then
        bash -lc "set -euo pipefail; $HAWKSTRADE_CAPITOL_REFRESH_COMMAND"
        return
    fi

    if ! is_truthy "${HAWKSTRADE_CAPITOL_ALLOW_SAMPLE_DATA:-}"; then
        log_line ERROR "Default HawksCapitol runners currently use demo sample data; set HAWKSTRADE_CAPITOL_REFRESH_COMMAND to a real-data signal export command, or set HAWKSTRADE_CAPITOL_ALLOW_SAMPLE_DATA=1 only for non-production demos."
        return 70
    fi

    local signal_dir
    local tmp_signal
    signal_dir="$(dirname "$DEFAULT_SIGNAL_FILE")"
    mkdir -p "$signal_dir"
    tmp_signal="$(mktemp "$signal_dir/latest.XXXXXX.json")"
    write_sample_signals "$tmp_signal"
    mv "$tmp_signal" "$DEFAULT_SIGNAL_FILE"
}

export CAPITOL_DIR DEFAULT_SIGNAL_FILE PYTHON_BIN DRY_RUN
export HAWKSTRADE_CAPITOL_ALLOW_SAMPLE_DATA="${HAWKSTRADE_CAPITOL_ALLOW_SAMPLE_DATA:-}"
export HAWKSTRADE_CAPITOL_REFRESH_COMMAND="${HAWKSTRADE_CAPITOL_REFRESH_COMMAND:-}"
export HAWKSTRADE_CAPITOL_SIGNAL_PATH="$SIGNAL_FILE"
set +e
if command -v flock >/dev/null 2>&1; then
    flock -w "$LOCK_TIMEOUT_SECONDS" -E 75 "$LOCK_FILE" bash -c "$(declare -f log_line); $(declare -f is_truthy); $(declare -f write_sample_signals); $(declare -f refresh_once); set -euo pipefail; refresh_once"
    STATUS=$?
else
    LOCK_DIR="${LOCK_FILE}.d"
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        (set -euo pipefail; refresh_once)
        STATUS=$?
        rmdir "$LOCK_DIR" 2>/dev/null || true
    else
        log_line ERROR "refresh lock is busy: $LOCK_DIR"
        STATUS=75
    fi
fi
set -e

if [[ "$STATUS" -ne 0 ]]; then
    END_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    log_line ERROR "RUN_END script=hawkscapitol_refresh run_id=$(quote_field "$RUN_ID") ended_at=$(quote_field "$END_UTC") status=error exit_code=$STATUS dry_run=$DRY_RUN"
    exit "$STATUS"
fi

if [[ "$DRY_RUN" -eq 0 ]]; then
    SIGNAL_POST_REFRESH_MTIME_NS="$(file_mtime_ns "$SIGNAL_FILE")"
    DEFAULT_SIGNAL_POST_REFRESH_MTIME_NS="$(file_mtime_ns "$DEFAULT_SIGNAL_FILE")"

    if [[ "$SIGNAL_FILE" != "$DEFAULT_SIGNAL_FILE" ]]; then
        if [[ "$SIGNAL_POST_REFRESH_MTIME_NS" -gt "$SIGNAL_PRE_REFRESH_MTIME_NS" ]]; then
            :
        elif [[ "$DEFAULT_SIGNAL_POST_REFRESH_MTIME_NS" -gt "$DEFAULT_SIGNAL_PRE_REFRESH_MTIME_NS" && -s "$DEFAULT_SIGNAL_FILE" ]]; then
            mkdir -p "$(dirname "$SIGNAL_FILE")"
            cp "$DEFAULT_SIGNAL_FILE" "$SIGNAL_FILE"
            SIGNAL_POST_REFRESH_MTIME_NS="$(file_mtime_ns "$SIGNAL_FILE")"
        fi
    fi

    if [[ "$SIGNAL_POST_REFRESH_MTIME_NS" -le "$SIGNAL_PRE_REFRESH_MTIME_NS" ]]; then
        log_line ERROR "RUN_END script=hawkscapitol_refresh run_id=$(quote_field "$RUN_ID") status=error exit_code=70 dry_run=$DRY_RUN reason=signal_file_not_updated signal_file=$(quote_field "$SIGNAL_FILE")"
        exit 70
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
log_line INFO "RUN_END script=hawkscapitol_refresh run_id=$(quote_field "$RUN_ID") ended_at=$(quote_field "$END_UTC") status=ok dry_run=$DRY_RUN"
