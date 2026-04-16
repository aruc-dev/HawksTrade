#!/usr/bin/env bash
# =============================================================================
# HawksTrade — Fetch secrets from AWS Secrets Manager into /dev/shm
# =============================================================================
#
# PURPOSE
#   Pull the HawksTrade secret from AWS Secrets Manager and write it to
#   /dev/shm/.hawkstrade.env (a RAM-only filesystem — never touches disk).
#   alpaca_client.py reads from there when secrets_source: shm is set in
#   config/config.yaml.
#
# WHEN TO RUN
#   At EC2 instance startup only — via the hawkstrade-secrets systemd unit
#   (see cloud-setup/aws-setup.md). Do NOT call this from cron or from inside
#   the bot. Secrets are written once at boot and remain in RAM until reboot.
#
# REQUIREMENTS
#   - AWS CLI v2 installed (aws)
#   - EC2 IAM Instance Role with secretsmanager:GetSecretValue on the secret
#   - jq installed for JSON parsing
#
# USAGE (manual test only)
#   bash scripts/fetch_secrets.sh
#
# =============================================================================

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────

# Name of the secret in AWS Secrets Manager (must match what you created)
SECRET_NAME="${HAWKSTRADE_SECRET_NAME:-hawkstrade/keys}"

# AWS region where the secret lives
AWS_REGION="${AWS_DEFAULT_REGION:-us-east-1}"

# Output file — /dev/shm is a RAM filesystem on Linux, cleared on reboot
OUTPUT_FILE="/dev/shm/.hawkstrade.env"

# ── Checks ────────────────────────────────────────────────────────────────────

if ! command -v aws &>/dev/null; then
    echo "ERROR: aws CLI not found. Install it: https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html" >&2
    exit 1
fi

if ! command -v jq &>/dev/null; then
    echo "ERROR: jq not found. Install it: sudo dnf install -y jq  (or apt-get install jq)" >&2
    exit 1
fi

# ── Fetch ─────────────────────────────────────────────────────────────────────

echo "[fetch_secrets] Fetching secret '${SECRET_NAME}' from AWS Secrets Manager (region: ${AWS_REGION})..."

SECRET_JSON=$(aws secretsmanager get-secret-value \
    --secret-id "${SECRET_NAME}" \
    --region "${AWS_REGION}" \
    --query "SecretString" \
    --output text)

if [[ -z "${SECRET_JSON}" || "${SECRET_JSON}" == "None" ]]; then
    echo "ERROR: Secrets Manager returned no SecretString for '${SECRET_NAME}'. Ensure the secret uses SecretString and contains valid JSON." >&2
    exit 1
fi

# ── Write to /dev/shm ─────────────────────────────────────────────────────────
# Expected secret JSON format:
# {
#   "ALPACA_PAPER_API_KEY":    "...",
#   "ALPACA_PAPER_SECRET_KEY": "...",
#   "ALPACA_LIVE_API_KEY":     "...",
#   "ALPACA_LIVE_SECRET_KEY":  "..."
# }
# Optional keys (also written if present):
#   NEWS_API_KEY

# Use umask 077 so the temp file is never readable by other users,
# then atomically rename into place to avoid a window of wrong permissions.
# Also refuse to write if OUTPUT_FILE is already a symlink (symlink clobbering).
if [[ -L "${OUTPUT_FILE}" ]]; then
    echo "ERROR: ${OUTPUT_FILE} is a symlink. Refusing to write secrets to a symlink target." >&2
    exit 1
fi

TEMP_FILE=$(umask 077 && mktemp /dev/shm/.hawkstrade.env.XXXXXX)
trap 'rm -f "${TEMP_FILE}"' EXIT

{
    echo "# HawksTrade secrets — auto-generated at $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo "# Source: AWS Secrets Manager / ${SECRET_NAME}"
    echo "# WARNING: Do not copy this file to disk. It lives in RAM only (/dev/shm)."
    echo ""
    for key in \
        ALPACA_PAPER_API_KEY \
        ALPACA_PAPER_SECRET_KEY \
        ALPACA_LIVE_API_KEY \
        ALPACA_LIVE_SECRET_KEY \
        NEWS_API_KEY; do
        # Use @sh to produce a safely shell-quoted value that dotenv can parse
        value=$(echo "${SECRET_JSON}" | jq -r --arg k "${key}" 'if (.[$k] // "") != "" then .[$k] | @sh else empty end')
        if [[ -n "${value}" ]]; then
            echo "${key}=${value}"
        fi
    done
} > "${TEMP_FILE}"

chmod 600 "${TEMP_FILE}"
mv "${TEMP_FILE}" "${OUTPUT_FILE}"
trap - EXIT

echo "[fetch_secrets] Secrets written to ${OUTPUT_FILE} (mode 600, RAM only)."
echo "[fetch_secrets] Done."
