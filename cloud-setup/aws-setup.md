# HawksTrade — AWS EC2 Setup Guide

This guide covers everything needed to run HawksTrade on an AWS EC2 instance, including
IAM setup, secrets management via AWS Secrets Manager, and scheduling via Linux cron.

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
> as the default. If you use a different name, set the `HAWKSTRADE_SECRET_NAME` environment
> variable in the systemd unit (Step 7).

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

# Install Python dependencies
pip3 install -r requirements.txt --break-system-packages

# Verify AWS CLI is available (pre-installed on Amazon Linux 2023)
aws --version
```

---

## Step 6 — Configure HawksTrade for EC2

Edit `config/config.yaml`:

```yaml
mode: paper              # keep as paper until you're ready for live

secrets_source: shm      # tells the bot to read from /dev/shm (RAM), not .env files
```

Do **not** create a `config/.env` or `.env` file on EC2 — secrets come from Secrets Manager.

> **Tip — machine-local config without touching the committed file:**
> Instead of editing `config/config.yaml` directly on EC2, you can create
> `config/config.local.yaml`. When present, this file is deep-merged over
> `config/config.yaml`, so it only needs the keys you want to change. It is
> gitignored, so it will never be accidentally committed or overwritten by a
> `git pull`.

The Linux cron templates set `HAWKSTRADE_REQUIRE_SHM=1`. With that guard enabled,
HawksTrade refuses to fall back to local dotenv files when `secrets_source: shm`
is configured and `/dev/shm/.hawkstrade.env` is missing, unreadable, or rejected
by the optional age check. If you rotate secrets frequently, you can also set
`HAWKSTRADE_SHM_MAX_AGE_SECONDS=<seconds>` in the cron file to reject stale RAM
secret files.

---

## Step 7 — Set Up the Secrets Fetch Systemd Unit

This systemd unit runs `scripts/fetch_secrets.sh` once at boot, before cron starts.
It writes secrets to `/dev/shm/.hawkstrade.env` (RAM only — cleared on reboot).

Create the unit file:

```bash
sudo nano /etc/systemd/system/hawkstrade-secrets.service
```

Paste the following (update `YOUR_USERNAME` and path if needed):

```ini
[Unit]
Description=Fetch HawksTrade secrets from AWS Secrets Manager into /dev/shm
Before=cron.service crond.service
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=YOUR_USERNAME
Environment=AWS_DEFAULT_REGION=us-east-1
Environment=HAWKSTRADE_SECRET_NAME=hawkstrade/keys
ExecStart=/home/YOUR_USERNAME/HawksTrade/scripts/fetch_secrets.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

Enable and test it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable hawkstrade-secrets

# Test it runs correctly right now (without rebooting)
sudo systemctl start hawkstrade-secrets
sudo systemctl status hawkstrade-secrets

# Confirm the file was written
ls -la /dev/shm/.hawkstrade.env
```

From this point on, it will run automatically on every boot before cron starts.

---

## Step 8 — Install the Cron Schedule

HawksTrade includes ready-made cron templates. Use the UTC template on EC2
(Amazon Linux runs UTC by default):

```bash
# Edit HAWKSTRADE_DIR in the cron file first
nano ~/HawksTrade/scheduler/cron/hawkstrade-utc.cron

# Keep HAWKSTRADE_REQUIRE_SHM=1 enabled on EC2 so missing RAM secrets fail closed.

# Install
crontab ~/HawksTrade/scheduler/cron/hawkstrade-utc.cron

# Verify
crontab -l
```

---

## Step 9 — Verify the Full Setup

Run these checks before leaving the instance:

```bash
cd ~/HawksTrade

# 1. Confirm secrets are in RAM
cut -d= -f1 /dev/shm/.hawkstrade.env   # shows only key names, not values

# 2. Confirm Alpaca connection works using the same fail-closed shm guard as cron
HAWKSTRADE_REQUIRE_SHM=1 python3 -c "
import sys; sys.path.insert(0, '.')
from core.alpaca_client import get_account
a = get_account()
print('Connected! Portfolio value:', a.portfolio_value)
"

# 3. Dry-run the scanner
HAWKSTRADE_REQUIRE_SHM=1 python3 scheduler/run_scan.py --dry-run

# 4. Dry-run the risk check
HAWKSTRADE_REQUIRE_SHM=1 python3 scheduler/run_risk_check.py --dry-run

# 5. Run unit tests
python3 -m unittest discover -v
```

All five checks must pass before relying on the bot to run unattended.

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

## Security Notes

- **No keys on disk.** Secrets Manager + `/dev/shm` means your Alpaca keys are never written to the filesystem.
- **No hardcoded credentials.** The EC2 instance authenticates to Secrets Manager using its IAM role — no AWS access keys anywhere.
- **Principle of least privilege.** The IAM policy grants read access to `hawkstrade/*` only — nothing else in your AWS account.
- **RAM cleared on reboot.** `/dev/shm` is wiped when the instance stops or reboots. The systemd unit re-fetches secrets automatically on the next boot.
- **SSH access.** Restrict port 22 to your IP in the security group. Consider using AWS Systems Manager Session Manager as an alternative to avoid opening any inbound ports at all.

---

## Switching to Live Trading

Only do this after:
- At least 30 days of successful paper trading
- Win rate > 50% and positive total P&L
- You have explicitly decided to use real money

Steps:
1. Ensure `ALPACA_LIVE_API_KEY` and `ALPACA_LIVE_SECRET_KEY` are filled in your Secrets Manager secret
2. Set `mode: live` — either edit `config/config.yaml` directly, or (preferred on EC2) create `config/config.local.yaml` containing just `mode: live`, so the change is not overwritten by a `git pull`
3. Reboot the instance (so secrets re-fetch) or run `sudo systemctl restart hawkstrade-secrets`
4. Verify connection: `python3 -c "from core.alpaca_client import get_account; print(get_account().portfolio_value)"`

---

## Useful Commands

```bash
# View cron schedule
crontab -l

# Check systemd secrets unit
systemctl status hawkstrade-secrets

# View today's scan log
tail -f ~/HawksTrade/logs/scan_$(date +%Y%m%d).log

# View today's risk check log
tail -f ~/HawksTrade/logs/risk_$(date +%Y%m%d).log

# Re-fetch secrets manually (without rebooting)
sudo systemctl restart hawkstrade-secrets

# Run a manual scan
cd ~/HawksTrade && python3 scheduler/run_scan.py

# Run a manual risk check
cd ~/HawksTrade && python3 scheduler/run_risk_check.py
```
