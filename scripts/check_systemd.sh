#!/usr/bin/env bash
# =============================================================================
# HawksTrade — systemd deployment diagnostic
# =============================================================================
#
# PURPOSE
#   End-to-end health check for a HawksTrade deployment running on a
#   systemd-based Linux host (typical EC2 setup). Validates:
#
#     1. systemd unit configuration (unit files load, [Install] sane,
#        ExecStart points at reachable paths, User= exists).
#     2. Timer schedule: next trigger time and last run result for every
#        hawkstrade-*.timer.
#     3. Last run status of every hawkstrade-*.service with failure reason
#        if the last invocation exited non-zero.
#     4. Secret availability in /dev/shm/.hawkstrade.env and that the keys
#        for the configured `mode:` (paper | live) are populated.
#     5. Live Alpaca connectivity using those keys.
#     6. Current open positions and unrealized P&L from Alpaca.
#
# USAGE
#   bash scripts/check_systemd.sh
#
# EXIT CODES
#   0  — everything green
#   1  — at least one check failed (details printed above)
#   2  — preconditions missing (systemctl unavailable, wrong OS, etc.)
#
# NOTES
#   Read-only. Does not modify any unit files, secrets, or positions.
#   Safe to run as ec2-user; some fields (e.g. full status of root-owned
#   units) are richer when run via sudo.
# =============================================================================

set -u
# Do NOT use `set -e` — we want the script to keep running and aggregate
# findings even when individual checks fail.

# ── Locate project root ───────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_FILE="${PROJECT_ROOT}/config/config.yaml"
SHM_FILE="${HAWKSTRADE_SHM_SECRET_FILE:-/dev/shm/.hawkstrade.env}"

# ── Colors (only when stdout is a TTY) ────────────────────────────────────────

if [[ -t 1 ]] && command -v tput &>/dev/null && [[ $(tput colors 2>/dev/null || echo 0) -ge 8 ]]; then
    C_RED="$(tput setaf 1)"
    C_GREEN="$(tput setaf 2)"
    C_YELLOW="$(tput setaf 3)"
    C_BLUE="$(tput setaf 4)"
    C_BOLD="$(tput bold)"
    C_RESET="$(tput sgr0)"
else
    C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_BOLD=""; C_RESET=""
fi

OK="${C_GREEN}[ OK ]${C_RESET}"
WARN="${C_YELLOW}[WARN]${C_RESET}"
FAIL="${C_RED}[FAIL]${C_RESET}"
INFO="${C_BLUE}[INFO]${C_RESET}"

# ── Aggregate status ──────────────────────────────────────────────────────────

FAIL_COUNT=0
WARN_COUNT=0

note_ok()   { echo "  ${OK}   $*"; }
note_warn() { echo "  ${WARN} $*"; WARN_COUNT=$((WARN_COUNT + 1)); }
note_fail() { echo "  ${FAIL} $*"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
note_info() { echo "  ${INFO} $*"; }

section() {
    echo ""
    echo "${C_BOLD}${C_BLUE}══ $* ══${C_RESET}"
}

# ── Preconditions ─────────────────────────────────────────────────────────────

section "0. Preconditions"

if ! command -v systemctl &>/dev/null; then
    note_fail "systemctl not found — this host is not systemd-based. Aborting."
    exit 2
fi
note_ok "systemctl available"

if [[ ! -f "${CONFIG_FILE}" ]]; then
    note_fail "config file not found: ${CONFIG_FILE}"
    exit 2
fi
note_ok "config file found: ${CONFIG_FILE}"

# ── Parse mode from config.yaml ───────────────────────────────────────────────
# We intentionally avoid requiring PyYAML / jq for config parsing so this
# script works even when the venv is broken. Simple grep is fine for
# `mode: paper` / `mode: live`.

MODE="$(grep -E '^[[:space:]]*mode:[[:space:]]*' "${CONFIG_FILE}" \
        | head -n1 \
        | sed -E 's/^[[:space:]]*mode:[[:space:]]*"?([a-zA-Z]+)"?.*/\1/' \
        | tr -d '[:space:]' || true)"

if [[ -z "${MODE}" ]]; then
    note_fail "Could not parse 'mode:' from ${CONFIG_FILE}"
    MODE="unknown"
else
    note_ok "Trading mode from config: ${C_BOLD}${MODE}${C_RESET}"
fi

SECRETS_SOURCE="$(grep -E '^[[:space:]]*secrets_source:[[:space:]]*' "${CONFIG_FILE}" \
        | head -n1 \
        | sed -E 's/^[[:space:]]*secrets_source:[[:space:]]*"?([a-zA-Z]+)"?.*/\1/' \
        | tr -d '[:space:]' || true)"
SECRETS_SOURCE="${SECRETS_SOURCE:-dotenv}"
note_info "secrets_source: ${SECRETS_SOURCE}"

# ── Discover hawkstrade-* units ───────────────────────────────────────────────

section "1. systemd unit discovery"

ALL_UNITS=()
while IFS= read -r unit; do
    [[ -n "${unit}" ]] && ALL_UNITS+=("${unit}")
done < <(
    systemctl list-unit-files --no-legend --no-pager 'hawkstrade-*' 2>/dev/null \
        | awk '{print $1}' \
        | sort -u
)

if [[ ${#ALL_UNITS[@]} -eq 0 ]]; then
    note_fail "No hawkstrade-* unit files found. Is the project installed?"
    echo ""
    echo "  Hint: unit files should live in /etc/systemd/system/ with the prefix"
    echo "        'hawkstrade-'. See scheduler/systemd/README.md for install steps."
    exit 1
fi

note_ok "Found ${#ALL_UNITS[@]} hawkstrade-* unit files:"
for u in "${ALL_UNITS[@]}"; do
    echo "        • ${u}"
done

SERVICES=()
TIMERS=()
for u in "${ALL_UNITS[@]}"; do
    case "${u}" in
        *.service) SERVICES+=("${u}") ;;
        *.timer)   TIMERS+=("${u}") ;;
    esac
done

# ── Unit file validation ──────────────────────────────────────────────────────

section "2. Unit configuration validation"

collapse_nonempty_lines() {
    awk 'NF {
        sub(/^[[:space:]]+/, "")
        sub(/[[:space:]]+$/, "")
        if (out) {
            out = out " ; " $0
        } else {
            out = $0
        }
    } END { print out }'
}

timer_schedule_for_unit() {
    local unit="$1"
    local calendar monotonic fallback

    # systemd exposes parsed timer schedules through these properties. Direct
    # OnCalendar/OnBootSec properties are not portable across systemd versions.
    calendar="$(systemctl show -p TimersCalendar --value "${unit}" 2>/dev/null | collapse_nonempty_lines)"
    monotonic="$(systemctl show -p TimersMonotonic --value "${unit}" 2>/dev/null | collapse_nonempty_lines)"

    [[ "${calendar}" == "n/a" ]] && calendar=""
    [[ "${monotonic}" == "n/a" ]] && monotonic=""

    if [[ -n "${calendar}" && -n "${monotonic}" ]]; then
        printf '%s ; %s\n' "${calendar}" "${monotonic}"
        return
    fi
    if [[ -n "${calendar}" ]]; then
        printf '%s\n' "${calendar}"
        return
    fi
    if [[ -n "${monotonic}" ]]; then
        printf '%s\n' "${monotonic}"
        return
    fi

    fallback="$(
        systemctl cat "${unit}" 2>/dev/null | awk '
            /^[[:space:]]*\[/ {
                in_timer = ($0 ~ /^[[:space:]]*\[Timer\][[:space:]]*$/)
                next
            }
            in_timer {
                line = $0
                sub(/[[:space:]]*[#;].*$/, "", line)
                sub(/^[[:space:]]+/, "", line)
                sub(/[[:space:]]+$/, "", line)
                if (line ~ /^(OnCalendar|OnActiveSec|OnBootSec|OnStartupSec|OnUnitActiveSec|OnUnitInactiveSec)=.+/) {
                    if (out) {
                        out = out " ; " line
                    } else {
                        out = line
                    }
                }
            }
            END { print out }
        '
    )"
    printf '%s\n' "${fallback}"
}

validate_unit() {
    local unit="$1"
    local unit_file
    unit_file="$(systemctl show -p FragmentPath --value "${unit}" 2>/dev/null)"

    if [[ -z "${unit_file}" || ! -r "${unit_file}" ]]; then
        note_fail "${unit}: fragment path missing or unreadable (${unit_file:-<empty>})"
        return
    fi

    # LoadState / unit syntax
    local load_state
    load_state="$(systemctl show -p LoadState --value "${unit}" 2>/dev/null)"
    if [[ "${load_state}" != "loaded" ]]; then
        note_fail "${unit}: LoadState=${load_state} (expected 'loaded')"
        return
    fi

    if [[ "${unit}" == *.service ]]; then
        # ExecStart path must exist and be executable
        local exec_start exec_bin
        exec_start="$(systemctl show -p ExecStart --value "${unit}" 2>/dev/null)"
        exec_bin="$(awk -F'argv\\[\\]=' 'NR==1{print $2}' <<<"${exec_start}" | awk '{print $1}')"

        if [[ -n "${exec_bin}" && ! -x "${exec_bin}" ]]; then
            note_fail "${unit}: ExecStart binary not executable: ${exec_bin}"
            return
        fi

        # User= must resolve on this host (this is the 217/USER failure mode)
        local user
        user="$(systemctl show -p User --value "${unit}" 2>/dev/null)"
        if [[ -n "${user}" ]]; then
            if ! id -u "${user}" &>/dev/null; then
                note_fail "${unit}: User=${user} does not exist on this system (would fail with status=217/USER)"
                return
            fi
        fi

        # WorkingDirectory must exist if specified
        local wd
        wd="$(systemctl show -p WorkingDirectory --value "${unit}" 2>/dev/null)"
        if [[ -n "${wd}" && "${wd}" != "[not set]" && ! -d "${wd}" ]]; then
            note_fail "${unit}: WorkingDirectory does not exist: ${wd}"
            return
        fi

        note_ok "${unit}: fragment OK, User=${user:-<default>} resolvable, ExecStart=${exec_bin}"
    else
        # Timer: must have at least one calendar or monotonic trigger.
        local schedule
        schedule="$(timer_schedule_for_unit "${unit}")"
        if [[ -z "${schedule}" ]]; then
            note_fail "${unit}: no calendar or monotonic timer schedule configured"
            return
        fi
        # Timer should point at a companion service that actually exists
        local activates
        activates="$(systemctl show -p Unit --value "${unit}" 2>/dev/null)"
        if [[ -z "${activates}" ]]; then
            note_warn "${unit}: 'Unit=' is empty (will default to same-basename .service)"
        elif ! systemctl cat "${activates}" &>/dev/null; then
            note_fail "${unit}: activates ${activates} but that unit does not exist"
            return
        fi
        note_ok "${unit}: schedule='${schedule}', activates=${activates}"
    fi
}

for u in "${ALL_UNITS[@]}"; do
    validate_unit "${u}"
done

# ── Timer schedule & last run status ──────────────────────────────────────────

section "3. Timer schedule (next run / last run)"

if [[ ${#TIMERS[@]} -eq 0 ]]; then
    note_warn "No .timer units found — nothing is scheduled."
else
    # Pretty columnar output. Use a pipe so systemctl output doesn't block.
    printf '  %-36s  %-26s  %-26s  %s\n' "TIMER" "NEXT RUN" "LAST RUN" "ACTIVATES"
    printf '  %-36s  %-26s  %-26s  %s\n' \
        "------------------------------------" \
        "--------------------------" \
        "--------------------------" \
        "---------"
    for t in "${TIMERS[@]}"; do
        local_next=""; local_last=""; local_act=""
        local_next="$(systemctl show -p NextElapseUSecRealtime --value "${t}" 2>/dev/null)"
        local_last="$(systemctl show -p LastTriggerUSec --value "${t}" 2>/dev/null)"
        local_act="$(systemctl show -p Unit --value "${t}" 2>/dev/null)"
        # Normalize empty / "n/a" markers
        [[ -z "${local_next}" || "${local_next}" == "n/a" ]] && local_next="<none scheduled>"
        [[ -z "${local_last}" || "${local_last}" == "n/a" ]] && local_last="<never on this boot>"
        printf '  %-36s  %-26s  %-26s  %s\n' "${t}" "${local_next}" "${local_last}" "${local_act}"
    done
fi

section "4. Service last-run status"

if [[ ${#SERVICES[@]} -eq 0 ]]; then
    note_warn "No .service units found."
else
    for s in "${SERVICES[@]}"; do
        active_state="$(systemctl show -p ActiveState --value "${s}" 2>/dev/null)"
        sub_state="$(systemctl show -p SubState --value "${s}" 2>/dev/null)"
        result="$(systemctl show -p Result --value "${s}" 2>/dev/null)"
        exec_status="$(systemctl show -p ExecMainStatus --value "${s}" 2>/dev/null)"
        exec_code="$(systemctl show -p ExecMainCode --value "${s}" 2>/dev/null)"
        inactive_enter="$(systemctl show -p InactiveEnterTimestamp --value "${s}" 2>/dev/null)"

        case "${result}" in
            success)
                if [[ "${active_state}" == "active" ]]; then
                    note_ok "${s}: currently ${active_state}/${sub_state} (running)"
                elif [[ -z "${inactive_enter}" || "${inactive_enter}" == "n/a" ]]; then
                    note_warn "${s}: has not run yet on this boot (inactive/dead, no history)"
                else
                    note_ok "${s}: last run succeeded — finished at ${inactive_enter}"
                fi
                ;;
            "" )
                note_warn "${s}: no Result recorded (never invoked)"
                ;;
            *)
                note_fail "${s}: last run failed (Result=${result}, ExecMainStatus=${exec_status}, Code=${exec_code})"
                echo ""
                echo "      ${C_BOLD}Last 10 log lines for ${s}:${C_RESET}"
                journalctl -u "${s}" --no-pager -n 10 2>&1 \
                    | sed "s|^|        |" \
                    || echo "        (journalctl unavailable — run with sudo for full logs)"
                echo ""
                ;;
        esac
    done
fi

# ── Secrets availability ──────────────────────────────────────────────────────

section "5. Secrets availability"

if [[ "${SECRETS_SOURCE}" != "shm" ]]; then
    note_info "secrets_source=${SECRETS_SOURCE} — shm file is not required by config. Skipping shm check."
else
    if [[ ! -e "${SHM_FILE}" ]]; then
        note_fail "${SHM_FILE} does not exist. Run: sudo systemctl start hawkstrade-secrets.service"
    elif [[ -L "${SHM_FILE}" ]]; then
        note_fail "${SHM_FILE} is a symlink — refuse to trust. Inspect it with: ls -la ${SHM_FILE}"
    elif [[ ! -r "${SHM_FILE}" ]]; then
        note_fail "${SHM_FILE} exists but is not readable by $(id -un) (need to run under the bot's user, e.g. sudo -u ec2-user)."
    else
        perms="$(stat -c '%a' "${SHM_FILE}" 2>/dev/null || stat -f '%OLp' "${SHM_FILE}")"
        owner="$(stat -c '%U:%G' "${SHM_FILE}" 2>/dev/null || stat -f '%Su:%Sg' "${SHM_FILE}")"
        size="$(stat -c '%s' "${SHM_FILE}" 2>/dev/null || stat -f '%z' "${SHM_FILE}")"
        mtime="$(stat -c '%y' "${SHM_FILE}" 2>/dev/null || stat -f '%Sm' "${SHM_FILE}")"
        note_ok "${SHM_FILE}: mode=${perms}, owner=${owner}, size=${size}B, mtime=${mtime}"

        case "${perms}" in
            600|0600|640|0640)
                ;;
            *)
                note_warn "Permissions are ${perms} (recommended: 600, or 640 for root-owned group-readable deployments)"
                ;;
        esac

        # Age check
        if [[ -n "${HAWKSTRADE_SHM_MAX_AGE_SECONDS:-}" ]]; then
            age=$(( $(date +%s) - $(stat -c '%Y' "${SHM_FILE}") ))
            if (( age > HAWKSTRADE_SHM_MAX_AGE_SECONDS )); then
                note_fail "shm file is ${age}s old (max allowed ${HAWKSTRADE_SHM_MAX_AGE_SECONDS}s) — will be rejected by fail-closed guard"
            else
                note_ok "shm file age ${age}s within HAWKSTRADE_SHM_MAX_AGE_SECONDS=${HAWKSTRADE_SHM_MAX_AGE_SECONDS}"
            fi
        fi

        # Verify the keys for the selected mode are present AND non-empty,
        # without ever printing the values themselves.
        case "${MODE}" in
            paper) required_keys=(ALPACA_PAPER_API_KEY ALPACA_PAPER_SECRET_KEY) ;;
            live)  required_keys=(ALPACA_LIVE_API_KEY  ALPACA_LIVE_SECRET_KEY)  ;;
            *)     required_keys=() ;;
        esac

        if [[ ${#required_keys[@]} -gt 0 ]]; then
            for k in "${required_keys[@]}"; do
                # Accept either `KEY=value` or `KEY='value'` (fetch_secrets.sh uses @sh quoting)
                line="$(grep -E "^${k}=" "${SHM_FILE}" | head -n1 || true)"
                if [[ -z "${line}" ]]; then
                    note_fail "shm file missing required key for mode=${MODE}: ${k}"
                    continue
                fi
                # Strip key= prefix and surrounding quotes; check emptiness
                val="${line#*=}"
                val="${val#\'}"; val="${val%\'}"
                val="${val#\"}"; val="${val%\"}"
                if [[ -z "${val}" ]]; then
                    note_fail "shm file has ${k} but value is empty"
                else
                    note_ok "shm file has ${k} (len=${#val})"
                fi
            done
        fi
    fi
fi

# ── Alpaca connectivity + portfolio snapshot ──────────────────────────────────

section "6. Alpaca connectivity & portfolio"

VENV_PY=""
if [[ -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
    VENV_PY="${PROJECT_ROOT}/.venv/bin/python"
elif command -v python3 &>/dev/null; then
    VENV_PY="$(command -v python3)"
fi

if [[ -z "${VENV_PY}" ]]; then
    note_fail "No python3 / .venv available. Cannot test Alpaca connectivity."
else
    note_info "Using Python: ${VENV_PY}"
    # Export HAWKSTRADE_REQUIRE_SHM=1 only if the config says shm. That way
    # we exercise the same fail-closed path the live bot uses, but we don't
    # force it when the operator deliberately uses dotenv.
    if [[ "${SECRETS_SOURCE}" == "shm" ]]; then
        export HAWKSTRADE_REQUIRE_SHM=1
    fi

    PY_OUTPUT=$(
        cd "${PROJECT_ROOT}" && "${VENV_PY}" - <<'PY' 2>&1
import json, sys, traceback
try:
    from core import alpaca_client as ac
    acct = ac.get_account()
    positions = ac.get_all_positions() or []
    pos = []
    tot_pl = 0.0
    for p in positions:
        try:
            pnl = float(p.unrealized_pl)
        except Exception:
            pnl = 0.0
        tot_pl += pnl
        pos.append({
            "symbol": p.symbol,
            "qty": float(p.qty),
            "avg_entry": float(p.avg_entry_price),
            "current":   float(p.current_price),
            "market_value": float(p.market_value),
            "pnl":     pnl,
            "pnl_pct": float(p.unrealized_plpc),
        })
    print(json.dumps({
        "ok": True,
        "mode_status": getattr(acct, "status", "UNKNOWN"),
        "portfolio_value": float(acct.portfolio_value),
        "cash":            float(acct.cash),
        "buying_power":    float(acct.buying_power),
        "positions":       pos,
        "total_pl":        tot_pl,
        "position_count":  len(pos),
    }))
except Exception as e:
    print(json.dumps({
        "ok": False,
        "error": f"{type(e).__name__}: {e}",
        "trace": traceback.format_exc(),
    }))
    sys.exit(1)
PY
    )
    PY_EXIT=$?

    if [[ ${PY_EXIT} -ne 0 ]]; then
        note_fail "Alpaca call raised an exception:"
        echo "${PY_OUTPUT}" | sed "s|^|        |"
    else
        # Parse the JSON we emitted, without pulling in jq.
        ok=$("${VENV_PY}" -c "import json,sys; print(json.loads(sys.stdin.read())['ok'])" <<<"${PY_OUTPUT}" 2>/dev/null || echo "False")
        if [[ "${ok}" != "True" ]]; then
            note_fail "Alpaca call returned an error payload:"
            echo "${PY_OUTPUT}" | sed "s|^|        |"
        else
            note_ok "Alpaca authenticated successfully (mode=${MODE})"

            # Account summary line
            "${VENV_PY}" - "${PY_OUTPUT}" <<'PY'
import json, sys
d = json.loads(sys.argv[1])
print(f"        Account status : {d['mode_status']}")
print(f"        Portfolio value: ${d['portfolio_value']:>14,.2f}")
print(f"        Cash           : ${d['cash']:>14,.2f}")
print(f"        Buying power   : ${d['buying_power']:>14,.2f}")
print(f"        Open positions : {d['position_count']}")
print(f"        Total unreal P/L: ${d['total_pl']:>+13,.2f}")
PY

            # Positions table (if any)
            HAS_POSITIONS=$("${VENV_PY}" -c "import json,sys; print(len(json.loads(sys.stdin.read())['positions']))" <<<"${PY_OUTPUT}")
            if [[ "${HAS_POSITIONS}" -gt 0 ]]; then
                echo ""
                echo "        ${C_BOLD}Positions${C_RESET}"
                "${VENV_PY}" - "${PY_OUTPUT}" <<'PY'
import json, sys
d = json.loads(sys.argv[1])
fmt = "        {sym:<12}{qty:>12}{entry:>14}{now:>14}{mv:>14}{pl:>14}{plp:>10}"
print(fmt.format(sym="SYMBOL", qty="QTY", entry="ENTRY",
                 now="PRICE", mv="MKT VALUE", pl="P/L $", plp="P/L %"))
print(fmt.format(sym="------", qty="---", entry="-----",
                 now="-----", mv="---------", pl="-----", plp="-----"))
for p in d["positions"]:
    print(fmt.format(
        sym=p["symbol"],
        qty=f"{p['qty']:.4f}".rstrip("0").rstrip("."),
        entry=f"${p['avg_entry']:,.4f}",
        now=f"${p['current']:,.4f}",
        mv=f"${p['market_value']:,.2f}",
        pl=f"${p['pnl']:+,.2f}",
        plp=f"{p['pnl_pct']*100:+.2f}%",
    ))
PY
            else
                echo "        (no open positions)"
            fi
        fi
    fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────

section "Summary"

if (( FAIL_COUNT == 0 && WARN_COUNT == 0 )); then
    echo "  ${C_GREEN}${C_BOLD}ALL GREEN${C_RESET} — deployment looks healthy."
    exit 0
elif (( FAIL_COUNT == 0 )); then
    echo "  ${C_YELLOW}${C_BOLD}${WARN_COUNT} warning(s)${C_RESET}, no failures."
    echo "  Warnings are not blocking; review the notes above."
    exit 0
else
    echo "  ${C_RED}${C_BOLD}${FAIL_COUNT} failure(s)${C_RESET}, ${WARN_COUNT} warning(s)."
    echo "  Scroll up for the specific notes marked ${FAIL}."
    exit 1
fi
