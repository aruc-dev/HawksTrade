# HawksTrade — AWS EC2 Production Setup Guide (systemd)

This guide covers a production-grade deployment of HawksTrade on AWS EC2 using
**systemd services and timers** instead of cron. Compared to cron, this approach
gives you proper dependency ordering (secrets before trading jobs, network before
secrets), journal-based logging with `journalctl`, clean restart policies, and
health monitoring built into the init system.

Steps 1–5 are identical to the cron-based guide. The scheduler setup diverges at
Step 6.

---

## Prerequisites

- An AWS account with permission to create EC2 instances, IAM roles, and Secrets Manager secrets
- Your Alpaca API keys (paper and/or live)
- The HawksTrade repo cloned or copied to the EC2 instance

---

## Step 1 — Store Your Secrets in AWS Secrets Manager

1. Open the [AWS Secrets Manager console](https://console.aws.amazon.com/secretsmanager)
2. Click **Store a new secret**
3. Choose **Other type of secret**
4. Add the following key/value pairs:

   | Key                       | Value                  |
   |---------------------------|------------------------|
   | `ALPACA_PAPER_API_KEY`    | your paper API key     |
   | `ALPACA_PAPER_SECRET_KEY` | your paper secret key  |
   | `ALPACA_LIVE_API_KEY`     | your live API key (or leave blank until ready) |
   | `ALPACA_LIVE_SECRET_KEY`  | your live secret key (or leave blank until ready) |

5. Click **Next**
6. Name the secret exactly: **`hawkstrade/keys`**
7. Leave rotation disabled
8. Click through to **Store**

> **Note:** The secret name `hawkstrade/keys` is referenced in `scripts/fetch_secrets.sh`
> as the default. If you use a different name, set `HAWKSTRADE_SECRET_NAME` in
> `/etc/hawkstrade/hawkstrade.env` (Step 7).

---

## Step 2 — Create an IAM Policy

This policy allows read-only access to the HawksTrade secret. Nothing else.

1. Open [IAM → Policies](https://console.aws.amazon.com/iam/home#/policies)
2. Click **Create policy** → **JSON** tab
3. Paste the following (replace `YOUR_ACCOUNT_ID` and region if needed):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "HawksTradeSecretsReadOnly",
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue"],
      "Resource": "arn:aws:secretsmanager:us-east-1:YOUR_ACCOUNT_ID:secret:hawkstrade/*"
    }
  ]
}
```

4. Click **Next**, name it **`HawksTradeSecretsPolicy`**, then **Create policy**

---

## Step 3 — Create an IAM Role and Attach the Policy

1. Open [IAM → Roles](https://console.aws.amazon.com/iam/home#/roles)
2. Click **Create role**
3. Trusted entity: **AWS service** → **EC2** → click **Next**
4. Search for and attach **`HawksTradeSecretsPolicy`**
5. Click **Next**, name the role **`HawksTradeEC2Role`**, then **Create role**

---

## Step 4 — Launch the EC2 Instance

**Recommended instance:**

| Setting         | Value                              |
|-----------------|------------------------------------|
| Instance type   | `t4g.small` (2 vCPU, 2 GB RAM, ARM Graviton2) |
| AMI             | Amazon Linux 2023 (arm64)          |
| Storage         | 20 GB gp3                          |
| IAM Role        | `HawksTradeEC2Role` (set under Advanced → IAM instance profile) |
| Security group  | SSH (port 22) from your IP only — no inbound needed for the bot |

> Attach the IAM role at launch time under **Advanced details → IAM instance profile**.
> If the instance is already running, go to EC2 → Instance → Actions → Security → Modify IAM Role.

---

## Step 5 — Install Dependencies on the Instance

SSH into the instance, then:

```bash
# Install system packages
sudo dnf update -y
sudo dnf install -y python3 python3-pip git jq

# Clone or copy HawksTrade
git clone https://github.com/YOUR_USERNAME/HawksTrade.git ~/HawksTrade
cd ~/HawksTrade

# Create a virtual environment (recommended for systemd deployments)
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# Verify AWS CLI is available (pre-installed on Amazon Linux 2023)
aws --version
```

> Using a `.venv` is strongly recommended over `--break-system-packages` for
> production. The `scripts/run_hawkstrade_job.sh` wrapper activates `.venv`
> automatically when it exists.

---

## Step 6 — Configure HawksTrade

Edit `config/config.yaml`:

```yaml
mode: paper              # keep as paper until you're ready for live
secrets_source: shm      # read credentials from /dev/shm, not .env files
```

Do **not** create a `config/.env` or `.env` file on EC2 — secrets come from
Secrets Manager via the secrets service. With `HAWKSTRADE_REQUIRE_SHM=1` set in
the environment file (Step 7), HawksTrade refuses to fall back to disk dotenv
files if the RAM secret is missing or unreadable.

---

## Step 7 — Create the systemd Environment File

The environment file at `/etc/hawkstrade/hawkstrade.env` is loaded by every
systemd unit via `EnvironmentFile=`. It controls paths, runtime guards, and
optional tuning knobs. It is **not** the secrets file — Alpaca keys go in
`/etc/hawkstrade/hawkstrade.secrets` (Step 8).

```bash
# Create the config directory
sudo install -d -m 0750 /etc/hawkstrade

# Install the example env file
sudo install -m 0600 \
  ~/HawksTrade/scheduler/systemd/hawkstrade.env.example \
  /etc/hawkstrade/hawkstrade.env

# Edit it — set the correct HAWKSTRADE_DIR and username
sudo nano /etc/hawkstrade/hawkstrade.env
```

Key settings to confirm or adjust:

```bash
# Absolute path to the HawksTrade project directory
HAWKSTRADE_DIR=/home/ec2-user/HawksTrade

# OS user and group that will run the trading jobs
HAWKSTRADE_USER=ec2-user
HAWKSTRADE_GROUP=ec2-user

# Source secrets file (credentials — see Step 8)
HAWKSTRADE_SECRET_SOURCE=/etc/hawkstrade/hawkstrade.secrets

# RAM destination written by the secrets service
HAWKSTRADE_SHM_SECRET_FILE=/dev/shm/.hawkstrade.env

# Fail closed if RAM secrets are missing or too old (mandatory on production)
HAWKSTRADE_REQUIRE_SHM=1
HAWKSTRADE_SHM_MAX_AGE_SECONDS=86400

# Lock timeout for overlapping scan/risk-check jobs (seconds)
HAWKSTRADE_LOCK_TIMEOUT_SECONDS=600

# Health check lookback window and snapshot retention
HAWKSTRADE_HEALTH_HOURS=4
HAWKSTRADE_HEALTH_SNAPSHOT_RETENTION_DAYS=14

# Optional: POST health alerts to a webhook URL (Slack, PagerDuty, etc.)
# HAWKSTRADE_HEALTH_ALERT_WEBHOOK_URL=https://hooks.slack.com/services/...
```

---

## Step 8 — Create the Secrets File

The secrets file holds your Alpaca API keys in `KEY=VALUE` format. It is read
once at boot by `hawkstrade-secrets.service` and written into `/dev/shm` — it
never touches the bot's working directory.

```bash
sudo nano /etc/hawkstrade/hawkstrade.secrets
```

Paste and fill in:

```bash
ALPACA_PAPER_API_KEY=your_paper_api_key_here
ALPACA_PAPER_SECRET_KEY=your_paper_secret_key_here
ALPACA_LIVE_API_KEY=
ALPACA_LIVE_SECRET_KEY=
```

Lock it down:

```bash
sudo chmod 0600 /etc/hawkstrade/hawkstrade.secrets
sudo chown root:root /etc/hawkstrade/hawkstrade.secrets
```

> Leave the live keys blank until you're ready for live trading. The file is
> owned by root and only readable by root; the secrets service copies it into
> `/dev/shm` as the bot's user at boot.

**Alternative: fetch from AWS Secrets Manager at boot**

If you prefer to keep credentials out of the instance filesystem entirely, use
`scripts/fetch_secrets.sh` in the secrets service instead of copying a local
file. See the note at the end of this section.

---

## Step 9 — Install and Customise the systemd Units

The unit templates in `scheduler/systemd/` use `/home/ec2-user/HawksTrade` and
`ec2-user` as placeholders. The commands below substitute your actual path and
username before installing.

```bash
cd ~/HawksTrade

# Set these to match your deployment
export PROJECT=/home/ec2-user/HawksTrade
export HT_USER=ec2-user
export HT_GROUP=ec2-user

# Copy units to a temp directory and apply substitutions
TMPDIR="$(mktemp -d)"
cp scheduler/systemd/*.service scheduler/systemd/*.timer "$TMPDIR"/

sudo sed -i \
  -e "s|/home/ec2-user/HawksTrade|$PROJECT|g" \
  -e "s|User=ec2-user|User=$HT_USER|g" \
  -e "s|Group=ec2-user|Group=$HT_GROUP|g" \
  "$TMPDIR"/*.service

# Install into systemd
sudo cp "$TMPDIR"/*.service "$TMPDIR"/*.timer /etc/systemd/system/
sudo systemctl daemon-reload

# Clean up
rm -rf "$TMPDIR"
```

### What gets installed

| Unit | Type | Purpose |
|------|------|---------|
| `hawkstrade-secrets.service` | oneshot | Copies secrets into `/dev/shm` at boot; runs before all trading units |
| `hawkstrade-stock-scan.service` | oneshot | Stock-only scan at 9:35 AM ET |
| `hawkstrade-stock-scan.timer` | timer | Fires at 13:35 UTC (Mon–Fri) |
| `hawkstrade-full-scan.service` | oneshot | Full scan (stocks + crypto) |
| `hawkstrade-full-scan.timer` | timer | Fires on the hour 14:00–19:00 UTC (Mon–Fri) |
| `hawkstrade-crypto-scan.service` | oneshot | Crypto-only scan |
| `hawkstrade-crypto-scan.timer` | timer | Fires every hour, 24/7 |
| `hawkstrade-risk-check.service` | oneshot | Stop-loss / take-profit enforcement |
| `hawkstrade-risk-check.timer` | timer | Fires every 15 min during market hours (13:45–19:45 UTC, Mon–Fri) |
| `hawkstrade-daily-report.service` | oneshot | End-of-day performance report |
| `hawkstrade-daily-report.timer` | timer | Fires at 20:30 UTC (Mon–Fri) |
| `hawkstrade-weekly-report.service` | oneshot | Weekly performance summary |
| `hawkstrade-weekly-report.timer` | timer | Fires at 12:00 UTC (Mon) |
| `hawkstrade-health-check.service` | oneshot | Writes HTML/JSON health snapshot; sends webhook alert if unhealthy |
| `hawkstrade-health-check.timer` | timer | Fires every 15 min |

> All schedules are in **UTC**. EC2 instances run UTC by default. The ET market
> session (9:30 AM–4:00 PM ET) maps to 13:30–20:00 UTC during EDT (summer) and
> 14:30–21:00 UTC during EST (winter). The unit timers use the EDT offsets — see
> `scheduler/systemd/README.md` for details on the seasonal offset.

---

## Step 10 — Start the Secrets Service

The secrets service must be running (and its shm file present) before any
trading unit can start.

```bash
# Enable so it starts automatically on every boot
sudo systemctl enable hawkstrade-secrets.service

# Start it now (no reboot required)
sudo systemctl start hawkstrade-secrets.service

# Confirm it succeeded
sudo systemctl status hawkstrade-secrets.service

# Confirm the secrets file landed in RAM (shows key names only — no values)
sudo -u ec2-user cut -d= -f1 /dev/shm/.hawkstrade.env
```

Expected output from the last command:

```
# HawksTrade secrets — auto-generated at ...
# Source: ...
# WARNING: ...

ALPACA_PAPER_API_KEY
ALPACA_PAPER_SECRET_KEY
```

If either of the first two commands fails, check the journal:

```bash
journalctl -u hawkstrade-secrets.service --no-pager
```

---

## Step 11 — Enable the Timers

```bash
sudo systemctl enable --now \
  hawkstrade-stock-scan.timer \
  hawkstrade-full-scan.timer \
  hawkstrade-crypto-scan.timer \
  hawkstrade-risk-check.timer \
  hawkstrade-daily-report.timer \
  hawkstrade-weekly-report.timer \
  hawkstrade-health-check.timer
```

Verify they are active and show expected next-trigger times:

```bash
systemctl list-timers 'hawkstrade-*'
```

You should see all seven timers with `NEXT` timestamps populated.

---

## Step 12 — Verify the Full Setup

Run these checks before leaving the instance:

```bash
cd ~/HawksTrade

# 1. Confirm secrets are in RAM (key names only)
cut -d= -f1 /dev/shm/.hawkstrade.env

# 2. Confirm Alpaca connection works
HAWKSTRADE_REQUIRE_SHM=1 .venv/bin/python -c "
import sys; sys.path.insert(0, '.')
from core.alpaca_client import get_account
a = get_account()
print('Connected! Portfolio value:', a.portfolio_value)
"

# 3. Dry-run the scanner
HAWKSTRADE_REQUIRE_SHM=1 .venv/bin/python scheduler/run_scan.py --dry-run

# 4. Dry-run the risk check
HAWKSTRADE_REQUIRE_SHM=1 .venv/bin/python scheduler/run_risk_check.py --dry-run

# 5. Run the health check manually
sudo systemctl start hawkstrade-health-check.service
journalctl -u hawkstrade-health-check.service -n 50 --no-pager

# 6. Run unit tests
.venv/bin/python -m unittest discover -v
```

All six checks must pass before relying on the bot to run unattended.

---

## Dependency Chain

The units are wired so that no trading job can run without secrets being
available and the network being up:

```
network-online.target
        │
        ▼
hawkstrade-secrets.service  ←── loads /dev/shm/.hawkstrade.env
        │
        ├──▶ hawkstrade-stock-scan.service   (via timer: 13:35 UTC Mon–Fri)
        ├──▶ hawkstrade-full-scan.service    (via timer: 14–19:00 UTC Mon–Fri)
        ├──▶ hawkstrade-crypto-scan.service  (via timer: hourly 24/7)
        ├──▶ hawkstrade-risk-check.service   (via timer: every 15 min market hours)
        ├──▶ hawkstrade-daily-report.service (via timer: 20:30 UTC Mon–Fri)
        └──▶ hawkstrade-weekly-report.service (via timer: 12:00 UTC Mon)

hawkstrade-health-check.service  (wants secrets but still runs if absent)
        └──▶ reports/health_snapshots/  +  optional webhook alert
```

Trading services use `Requires=hawkstrade-secrets.service` — they will not start
if the secrets service has failed. The health-check uses `Wants=` so it can still
run and report that secrets are missing.

---

## Day-to-Day Operations

### View active timers and next fire times

```bash
systemctl list-timers 'hawkstrade-*'
```

### Check the status of any unit

```bash
systemctl status hawkstrade-risk-check.service
systemctl status hawkstrade-secrets.service
```

### Tail live logs for a service

```bash
# Follow the risk check live
journalctl -u hawkstrade-risk-check.service -f

# Last 100 lines of today's scan
journalctl -u hawkstrade-full-scan.service -n 100 --no-pager

# All hawkstrade units together, newest first
journalctl -u 'hawkstrade-*' --no-pager | tail -200
```

### Run a job manually (without waiting for the timer)

```bash
# Trigger a full scan now
sudo systemctl start hawkstrade-full-scan.service

# Trigger a risk check now
sudo systemctl start hawkstrade-risk-check.service

# Generate today's report now
sudo systemctl start hawkstrade-daily-report.service
```

### Run the health check and view the HTML report

```bash
sudo systemctl start hawkstrade-health-check.service
journalctl -u hawkstrade-health-check.service -n 50 --no-pager

# The HTML report is written to:
ls -lh ~/HawksTrade/reports/health_check_linux.html
ls -lh ~/HawksTrade/reports/health_snapshots/
```

### Re-fetch secrets without rebooting

```bash
sudo systemctl restart hawkstrade-secrets.service
sudo systemctl status hawkstrade-secrets.service
```

### Temporarily stop all trading (keep infrastructure running)

```bash
# Stop and disable all timers — jobs already in progress finish cleanly
sudo systemctl stop 'hawkstrade-*.timer'
sudo systemctl disable 'hawkstrade-*.timer'
```

### Re-enable all timers

```bash
sudo systemctl enable --now \
  hawkstrade-stock-scan.timer \
  hawkstrade-full-scan.timer \
  hawkstrade-crypto-scan.timer \
  hawkstrade-risk-check.timer \
  hawkstrade-daily-report.timer \
  hawkstrade-weekly-report.timer \
  hawkstrade-health-check.timer
```

### Reload after editing unit files

```bash
sudo systemctl daemon-reload
# Then restart or re-enable the affected units
```

---

## Optional — Read-Only Web Dashboard

After the bot is running, you can optionally add a personal web dashboard for
viewing system health, open positions, today's realized P&L, session
unrealized P&L, daily-loss headroom, recent trades, and per-strategy win
rates from your laptop or phone.

The dashboard is **read-only** (no trades, no config changes), runs as a
dedicated `hawkstrade-dash` user with no access to the bot's secrets, and is
exposed via a Cloudflare Tunnel + Cloudflare Access (Google SSO + MFA, no
inbound port on EC2).

See **[`cloud-setup/dashboard-setup.md`](./dashboard-setup.md)** for the
full step-by-step guide.

---

## Webhook Alerts (Optional)

The health-check service can POST an alert to any webhook when overall health is
red (or yellow, if configured). To enable, set the URL in
`/etc/hawkstrade/hawkstrade.env`:

```bash
HAWKSTRADE_HEALTH_ALERT_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
```

Then reload the environment and restart the service to pick up the change:

```bash
sudo systemctl daemon-reload
sudo systemctl start hawkstrade-health-check.service
```

The payload is a JSON object with `overall_status`, `generated_at`, and an
`items` array of human-readable alert reasons. Alert files are also written
locally to `reports/alerts/` regardless of whether a webhook is configured.

---

## Security Notes

- **No keys on disk (option A).** Use `scripts/fetch_secrets.sh` in the secrets
  service to pull from AWS Secrets Manager — credentials are never written to the
  instance filesystem, only to `/dev/shm` (RAM).
- **Minimal footprint (option B).** If you store the secrets file on disk at
  `/etc/hawkstrade/hawkstrade.secrets`, keep it mode `0600` owned by `root` and
  ensure `/etc/hawkstrade/` is mode `0750`. The secrets service copies it into RAM.
- **RAM cleared on reboot.** `/dev/shm` is wiped when the instance stops or
  reboots. The secrets service re-loads credentials automatically on the next boot.
- **`HAWKSTRADE_REQUIRE_SHM=1` is mandatory.** This guard makes the bot fail
  closed if the RAM secret file is missing, a symlink, or older than
  `HAWKSTRADE_SHM_MAX_AGE_SECONDS`. Never unset it on production.
- **No duplicate orders on retry.** All trading services are `Type=oneshot` with
  `Restart=no`. If a job fails, the next timer firing is the retry boundary —
  systemd will not re-run a failed job automatically, preventing duplicate order
  submissions.
- **Principle of least privilege.** The IAM policy grants `secretsmanager:GetSecretValue`
  on `hawkstrade/*` only. The bot user has no AWS credentials of its own.
- **SSH access.** Restrict port 22 to your IP in the security group. Consider
  using AWS Systems Manager Session Manager as an alternative to avoid opening any
  inbound ports at all.

---

## Switching to Live Trading

Only do this after:
- At least 30 days of successful paper trading
- Win rate > 50% and positive total P&L
- You have explicitly decided to use real money

Steps:
1. Ensure `ALPACA_LIVE_API_KEY` and `ALPACA_LIVE_SECRET_KEY` are filled in
   `/etc/hawkstrade/hawkstrade.secrets` (or in Secrets Manager if using
   `fetch_secrets.sh`)
2. Edit `config/config.yaml` → `mode: live`
3. Re-fetch secrets: `sudo systemctl restart hawkstrade-secrets.service`
4. Verify connection:

```bash
HAWKSTRADE_REQUIRE_SHM=1 .venv/bin/python -c "
import sys; sys.path.insert(0, '.')
from core.alpaca_client import get_account
print(get_account().portfolio_value)
"
```

5. Confirm a real order appears in the Alpaca live dashboard after the next scan

---

## Troubleshooting

**Secrets service fails to start**

```bash
journalctl -u hawkstrade-secrets.service --no-pager
```

Check that `/etc/hawkstrade/hawkstrade.secrets` exists, is readable by the service
user, and contains valid `KEY=VALUE` lines. Also confirm `HAWKSTRADE_SECRET_SOURCE`
in `hawkstrade.env` points to the correct path.

**A trading service fails immediately with "preflight failed"**

The `run_hawkstrade_job.sh` wrapper checks Alpaca connectivity before acquiring
the lock. If the preflight fails, the job exits with code 70 and logs
`PREFLIGHT_FAILED`. Check Alpaca status and confirm `/dev/shm/.hawkstrade.env`
contains valid keys:

```bash
cut -d= -f1 /dev/shm/.hawkstrade.env
```

**Health check reports `[NOK]`**

```bash
journalctl -u hawkstrade-health-check.service -n 100 --no-pager
cat ~/HawksTrade/reports/health_check_linux.html   # or open in a browser via scp
ls ~/HawksTrade/reports/alerts/
```

**Timer not firing at expected time**

Confirm the instance timezone is UTC (`timedatectl`) and check `list-timers` for
the next scheduled time. If `NEXT` is blank, the timer may be disabled:

```bash
timedatectl
systemctl list-timers 'hawkstrade-*'
systemctl is-enabled hawkstrade-full-scan.timer
```

**Unit file changes not taking effect**

Always run `sudo systemctl daemon-reload` after editing any file in
`/etc/systemd/system/`. Then restart the affected unit or let the next timer
firing pick up the change.
