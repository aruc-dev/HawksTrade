#!/usr/bin/env sh
# Advisory quick walk-forward check for strategy/risk/config changes.
# This is intentionally non-blocking while the gate is stabilising.

set -u

if [ "${HAWKSTRADE_SKIP_WALKFORWARD_ADVISORY:-0}" = "1" ]; then
  echo "walk-forward advisory: skipped by HAWKSTRADE_SKIP_WALKFORWARD_ADVISORY=1"
  exit 0
fi

if ! command -v git >/dev/null 2>&1; then
  exit 0
fi

repo_root=$(git rev-parse --show-toplevel 2>/dev/null || true)
if [ -z "$repo_root" ]; then
  exit 0
fi

changed_files=$(git diff --cached --name-only --diff-filter=ACMRT 2>/dev/null || true)
if [ -z "$changed_files" ]; then
  exit 0
fi

needs_check=0
for path in $changed_files; do
  case "$path" in
    strategies/*.py|core/risk_manager.py|core/exit_policy.py|config/config.yaml)
      needs_check=1
      break
      ;;
  esac
done

if [ "$needs_check" -ne 1 ]; then
  exit 0
fi

tmp_output=$(mktemp "${TMPDIR:-/tmp}/hawkstrade_walkforward_advisory.XXXXXX")
trap 'rm -f "$tmp_output"' EXIT

echo "walk-forward advisory: running quick profile because staged files touch strategy/risk/config paths"
(
  cd "$repo_root" &&
  python3 scheduler/run_walkforward.py --quick --no-write-report --no-artifacts
) >"$tmp_output" 2>&1
status=$?

summary=$(awk '/^# /{printing=1} printing{print}' "$tmp_output")
if [ -n "$summary" ]; then
  printf '%s\n' "$summary"
else
  tail -n 80 "$tmp_output"
fi

if [ "$status" -ne 0 ]; then
  echo "walk-forward advisory: quick profile returned $status; commit is not blocked yet"
else
  echo "walk-forward advisory: quick profile passed"
fi

exit 0
