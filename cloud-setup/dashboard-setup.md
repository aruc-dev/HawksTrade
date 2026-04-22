# HawksTrade — Dashboard Setup Guide (Optional)

This guide adds a personal, **read-only** web dashboard to a HawksTrade EC2
deployment. It shows system health, open positions, today's realized P&L,
session unrealized P&L, daily-loss headroom, recent trades, and per-strategy
win rates. It is intended for one user (you) on a laptop or phone.

> **This is an optional extension to the main setup.** Complete
> `cloud-setup/aws-setup-systemd.md` (or `cloud-setup/aws-setup.md`) first.
> The dashboard does not place trades, cancel orders, or change config — it
> only reads.

---

## Architecture

```
Phone / Laptop
     │ HTTPS (Cloudflare-managed TLS)
     ▼
Cloudflare Edge
     │ Cloudflare Access — identity check (Google SSO + email OTP MFA)
     ▼
Cloudflared tunnel (outbound connection only — NO inbound port on EC2)
     │ loopback
     ▼
FastAPI app on 127.0.0.1:8080    (dedicated `hawkstrade-dash` system user)
     │ read-only
     ▼
data/trades.csv, data/daily_loss_baseline.json,
logs/, scripts/check_systemd.sh, Alpaca (read-only key)
```

The dashboard never opens an inbound port. Cloudflared makes an outbound TLS
connection to Cloudflare's edge, and Cloudflare Access enforces identity
before any request reaches the tunnel. The FastAPI app validates the
Cloudflare JWT on every request as a defense-in-depth check.

---

## Non-negotiable security rules

These are baked into the code and unit files. Do not undo them.

| Rule | Where it lives |
|------|----------------|
| FastAPI binds to `127.0.0.1` only — never `0.0.0.0` | `hawkstrade-dashboard.service` `ExecStart=` |
| Dashboard runs as dedicated `hawkstrade-dash` user (no shell, no root) | Step 4 below |
| Dashboard cannot read the bot's `/dev/shm/.hawkstrade.env` or `/etc/hawkstrade/hawkstrade.env` | `InaccessiblePaths=` in the unit |
| HawksTrade project tree is read-only to the dashboard except `logs/` | `ReadOnlyPaths=` + `ReadWritePaths=` |
| Read-only Alpaca API key, separate from the bot's trading keys | `/etc/hawkstrade-dash/env` |
| Auth defaults to `cloudflare` — fail closed if misconfigured | `dashboard/security.py::assert_production_auth_safe()` |
| Every authenticated request is logged with email, path, IP, status | `dashboard/security.py::AccessLogMiddleware` |
| `/docs`, `/redoc`, `/openapi.json` disabled | `dashboard/app.py::create_app()` |
| Pinned dependencies, separate file | `requirements-dashboard.txt` |

---

## Phase 1 — Local-only dashboard (SSH tunnel)

Phase 1 runs the dashboard with auth disabled (`DASHBOARD_AUTH_MODE=local`)
behind an SSH tunnel from your laptop. This lets you verify the app end-to-end
before adding the Cloudflare layer. **Do not skip Phase 2 if you intend to
access the dashboard from a phone or anywhere outside an SSH tunnel.**

### 1.1 Install dashboard dependencies

```bash
cd ~/HawksTrade

# Reuse the bot's venv (recommended) — installs FastAPI / Uvicorn / Jinja2 / PyJWT
.venv/bin/pip install -r requirements-dashboard.txt
```

### 1.2 Create the `hawkstrade-dash` system user

```bash
# Locked-down service account — no login shell, no home dir
sudo useradd --system --no-create-home --shell /usr/sbin/nologin hawkstrade-dash

# Allow it to read the project tree and write to logs/
sudo chgrp -R hawkstrade-dash ~/HawksTrade
sudo chmod -R g+rX ~/HawksTrade
sudo chmod -R g+rwX ~/HawksTrade/logs

# Also allow the service user to traverse /home/ec2-user so systemd can chdir
# into /home/ec2-user/HawksTrade. Prefer ACL over loosening the whole home dir.
sudo setfacl -m u:hawkstrade-dash:--x /home/ec2-user

# If setfacl is unavailable, the fallback is:
# sudo chmod 711 /home/ec2-user
```

> The dashboard service uses `ProtectSystem=strict` + `ReadOnlyPaths=` +
> `ReadWritePaths=` to enforce filesystem access at the kernel level. The
> group-permission grants above are the minimum needed for the user to read
> the tree at all. The extra execute bit / ACL on `/home/ec2-user` is required
> so the `hawkstrade-dash` user can traverse the parent directory; otherwise
> systemd fails the unit with `status=200/CHDIR` before Uvicorn even starts.

### 1.3 Install the env file (Phase 1 settings)

```bash
sudo install -d -m 0750 -o root -g hawkstrade-dash /etc/hawkstrade-dash
sudo install -m 0640 -o root -g hawkstrade-dash \
  ~/HawksTrade/scheduler/systemd/hawkstrade-dash.env.example \
  /etc/hawkstrade-dash/env

sudo nano /etc/hawkstrade-dash/env
```

For Phase 1, set:

```bash
DASHBOARD_AUTH_MODE=local
# Leave CF_ACCESS_TEAM_DOMAIN / CF_ACCESS_AUD / DASHBOARD_ALLOWED_EMAILS as placeholders.
ALPACA_PAPER_API_KEY=<your read-only paper key>
ALPACA_PAPER_SECRET_KEY=<your read-only paper secret>
```

> **Get a dedicated read-only Alpaca key.** In the Alpaca dashboard, generate a
> *new* API key pair under Paper (or Live, if the bot is in live mode). Do
> **not** reuse the bot's trading keys. Store these only in
> `/etc/hawkstrade-dash/env` — never in the repo.

Lock the file:

```bash
sudo chmod 0640 /etc/hawkstrade-dash/env
sudo chown root:hawkstrade-dash /etc/hawkstrade-dash/env
```

### 1.4 Install the dashboard systemd unit

```bash
cd ~/HawksTrade

# Copy the unit file and substitute the project path / user if you customised them
TMPDIR="$(mktemp -d)"
cp scheduler/systemd/hawkstrade-dashboard.service "$TMPDIR/"

# (Skip this sed if your install matches /home/ec2-user/HawksTrade)
sudo sed -i \
  -e "s|/home/ec2-user/HawksTrade|$HOME/HawksTrade|g" \
  "$TMPDIR/hawkstrade-dashboard.service"

sudo install -m 0644 "$TMPDIR/hawkstrade-dashboard.service" \
  /etc/systemd/system/hawkstrade-dashboard.service
sudo systemctl daemon-reload
rm -rf "$TMPDIR"

sudo systemctl enable --now hawkstrade-dashboard.service
sudo systemctl status hawkstrade-dashboard.service
```

Install log rotation for dashboard access logs:

```bash
sudo install -m 0644 \
  ~/HawksTrade/cloud-setup/logrotate/hawkstrade-dashboard \
  /etc/logrotate.d/hawkstrade-dashboard

# Dry-run the policy; it should parse cleanly and retain 30 daily rotations.
sudo logrotate -d /etc/logrotate.d/hawkstrade-dashboard
```

Confirm it bound to loopback only (the dashboard MUST NOT show on `0.0.0.0`):

```bash
sudo ss -tlnp | grep 8080
```

Expected output:

```
LISTEN 0 2048 127.0.0.1:8080 0.0.0.0:* users:(("uvicorn",pid=...,fd=3))
```

If you see `0.0.0.0:8080` or `*:8080`, **stop and fix the unit** before
continuing. There is no firewall rule between you and the public internet
otherwise.

### 1.5 Open an SSH tunnel from your laptop and visit the dashboard

From your laptop (not the EC2):

```bash
# -N = no remote command, -L = local port forward, -i = EC2 PEM key
ssh -i /path/to/your-key.pem -N -L 8080:127.0.0.1:8080 ec2-user@<your-ec2-ip>
```

Then in your browser visit `http://localhost:8080/`. You should see the
HawksTrade dashboard with live data.

If your key file is too open, SSH will reject it. Fix that first:

```bash
chmod 400 /path/to/your-key.pem
```

End of Phase 1. If everything renders, proceed to Phase 2 to make it reachable
from your phone.

---

## Phase 2 — Cloudflare Tunnel + Cloudflare Access

Phase 2 adds:

- A Cloudflare Tunnel (`cloudflared`) so the dashboard is reachable from the
  internet without opening any inbound port on EC2
- Cloudflare Access for identity (Google SSO + MFA) at the edge
- Defense-in-depth JWT validation in the FastAPI app

### Prerequisites

- A domain on Cloudflare (any TLD, including `.us`, works fine)
- A free Cloudflare Zero Trust account ("Free" tier covers up to 50 users — one
  user is comfortably free)
- Google account for SSO (Cloudflare Access supports email OTP as a fallback)

### 2.1 Add your domain to Cloudflare (skip if already active)

Skip this step if the domain was purchased directly from Cloudflare, or if it
already appears as an active domain in the same Cloudflare account where you
will create the tunnel and Access policy. Cloudflare-purchased domains already
use Cloudflare DNS, so there is no external registrar nameserver update to do.

Only do this step if the domain was bought somewhere else (GoDaddy, Namecheap,
AWS Route 53, etc.) or is not already active in this Cloudflare account:

1. In the Cloudflare dashboard, click **Add a site** and enter your domain
2. Choose the **Free** plan
3. Cloudflare gives you two nameservers — go to your domain registrar and
   replace the existing NS records with these two
4. Wait for propagation (usually < 1 hour)

Before continuing, confirm the domain is visible and active under Cloudflare
**Websites**. You still need the later tunnel and Access steps even when this
domain-add step is skipped.

### 2.2 Create a Cloudflare Tunnel

1. Open [Cloudflare Zero Trust → Networks → Tunnels](https://one.dash.cloudflare.com/)
2. Click **Create a tunnel**, choose **Cloudflared**, name it `hawkstrade`
3. Cloudflare shows an install command — **don't use the all-in-one installer**.
   We'll install `cloudflared` manually under systemd. Copy the **tunnel token**
   shown on screen instead. (You can also copy the tunnel UUID and credentials
   JSON for the file-based config flow used below.)
4. Under **Public Hostname**, add:

   | Field | Value |
   |-------|-------|
   | Subdomain | `hawks` (or any name you like) |
   | Domain | your `.us` (or other) domain |
   | Type | `HTTP` |
   | URL | `127.0.0.1:8080` |

   Save. Cloudflare auto-creates the DNS CNAME at the edge.

### 2.3 Install `cloudflared` on EC2

Cloudflare publishes separate RPMs for x86_64 and arm64. Detect the instance
architecture first, then download the matching build:

```bash
# Detect architecture and pick the matching cloudflared build
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64)  CF_RPM="cloudflared-linux-x86_64.rpm"  ;;
  aarch64) CF_RPM="cloudflared-linux-aarch64.rpm" ;;
  *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac

curl -L --output /tmp/cloudflared.rpm \
  "https://github.com/cloudflare/cloudflared/releases/latest/download/$CF_RPM"
sudo rpm -i /tmp/cloudflared.rpm
rm /tmp/cloudflared.rpm

cloudflared --version
```

> If you already downloaded the wrong RPM (`rpm -i` reported "intended for a
> different architecture"), just delete `/tmp/cloudflared.rpm` and re-run the
> block above — it's safe.

### 2.4 Authenticate and write the tunnel config

```bash
# Browser-based login — opens a URL you visit on your laptop
cloudflared tunnel login

# This drops the certificate at ~/.cloudflared/cert.pem.
# Move it to /etc/cloudflared so the systemd unit can read it.
sudo install -d -m 0750 /etc/cloudflared
sudo mv ~/.cloudflared/cert.pem /etc/cloudflared/cert.pem

# Find your tunnel UUID (or use the one Cloudflare showed in 2.2)
cloudflared tunnel list

# Move the tunnel credentials JSON into /etc/cloudflared (named after UUID)
sudo mv ~/.cloudflared/<UUID>.json /etc/cloudflared/<UUID>.json
sudo chown -R root:cloudflared /etc/cloudflared
sudo chmod 0640 /etc/cloudflared/*.json /etc/cloudflared/cert.pem
```

Create `/etc/cloudflared/config.yml`:

```bash
sudo tee /etc/cloudflared/config.yml > /dev/null <<'YAML'
tunnel: <UUID>
credentials-file: /etc/cloudflared/<UUID>.json

ingress:
  - hostname: hawks.<yourdomain>.us
    service: http://127.0.0.1:8080
  - service: http_status:404
YAML
```

Replace `<UUID>` and `<yourdomain>.us` with your real values.

### 2.5 Create the `cloudflared` system user and install its unit

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin cloudflared
sudo chown -R root:cloudflared /etc/cloudflared
sudo chmod 0750 /etc/cloudflared

sudo install -m 0644 \
  ~/HawksTrade/scheduler/systemd/hawkstrade-cloudflared.service \
  /etc/systemd/system/hawkstrade-cloudflared.service
sudo systemctl daemon-reload

sudo systemctl enable --now hawkstrade-cloudflared.service
sudo systemctl status hawkstrade-cloudflared.service
```

Tail the journal to confirm a clean connection:

```bash
journalctl -u hawkstrade-cloudflared.service -n 50 --no-pager
```

You should see `Registered tunnel connection` lines (typically four — one per
Cloudflare edge region).

### 2.6 Add a Cloudflare Access policy

This is the identity gate. Without it the tunnel would be world-reachable.

1. In Cloudflare Zero Trust, go to **Access → Applications → Add an
   application** → **Self-hosted**
2. Application name: `HawksTrade Dashboard`
3. Application domain: `hawks.<yourdomain>.us`
4. Session Duration: `24 hours` (or shorter if you prefer)
5. Identity providers: enable **Google** (set up via **Settings → Authentication
   → Login methods** if not already configured) and **One-time PIN** as a
   fallback
6. Add a policy:
   - Name: `Allow only me`
   - Action: **Allow**
   - Include → **Emails** → enter your email (e.g. `arunbabuc.dev@gmail.com`)
7. Under the application's **Overview** tab, copy the **Application Audience
   (AUD) Tag** — you'll paste it into the env file below

Optional but recommended: enable **WARP** or require **MFA** under the
identity provider settings so any login requires a second factor (Google SSO
already provides this if your Google account has 2FA on, which it should).

### 2.7 Update the dashboard env file with Cloudflare values

```bash
sudo nano /etc/hawkstrade-dash/env
```

Set:

```bash
DASHBOARD_AUTH_MODE=cloudflare
CF_ACCESS_TEAM_DOMAIN=<yourteam>.cloudflareaccess.com
CF_ACCESS_AUD=<paste the AUD tag from Step 2.6>
DASHBOARD_ALLOWED_EMAILS=arunbabuc.dev@gmail.com

ALPACA_PAPER_API_KEY=<read-only paper key>
ALPACA_PAPER_SECRET_KEY=<read-only paper secret>
```

`DASHBOARD_ALLOWED_EMAILS` is a defense-in-depth check inside the FastAPI app
itself — even if a Cloudflare misconfiguration ever let a different email
through, the app would still reject it.

Restart the dashboard to pick up the new env:

```bash
sudo systemctl restart hawkstrade-dashboard.service
sudo systemctl status hawkstrade-dashboard.service
```

### 2.8 Verify end-to-end

From your phone or laptop (anywhere — not on EC2):

1. Visit `https://hawks.<yourdomain>.us`
2. Cloudflare Access prompts you to log in with Google → MFA
3. After login, the dashboard renders with live data

From the EC2, confirm the JWT validation is happening:

```bash
sudo tail -f ~/HawksTrade/logs/dashboard_access_*.log
```

You should see one line per request with your email, path, response code.

---

## What the dashboard shows

| Panel | Data source |
|-------|-------------|
| Account: portfolio value, cash, buying power | Alpaca read-only key (`get_account()`) |
| Daily-loss headroom (ok / warn / critical / tripped) | `data/daily_loss_baseline.json` + current portfolio value |
| Today's realized P&L | `data/trades.csv` rows with `status=closed`, `side=sell`, NY-session date == today |
| Open positions with unrealized P&L, strategy, hold days | Alpaca `get_all_positions()` + open rows in `data/trades.csv` |
| Last 30 trades | `data/trades.csv` |
| Per-strategy 30-day win rate | `data/trades.csv` aggregated |
| Health (each `hawkstrade-*.service` / `.timer` status) | `scripts/check_systemd.sh` output |

The client polls every 15 seconds. Each panel shows a stale-data warning if
its data is older than 60 seconds.

---

## Day-to-day operations

### Tail the dashboard logs

```bash
journalctl -u hawkstrade-dashboard.service -f
journalctl -u hawkstrade-cloudflared.service -f
tail -f ~/HawksTrade/logs/dashboard_access_*.log
```

### Restart after config or code changes

```bash
sudo systemctl restart hawkstrade-dashboard.service
# Cloudflared rarely needs a restart; restart only if you change /etc/cloudflared/config.yml
sudo systemctl restart hawkstrade-cloudflared.service
```

### Disable the dashboard temporarily

```bash
sudo systemctl stop hawkstrade-dashboard.service hawkstrade-cloudflared.service
sudo systemctl disable hawkstrade-dashboard.service hawkstrade-cloudflared.service
```

The trading bot continues to run — these are independent services.

### Rotate the read-only Alpaca key

Every 90 days (or sooner if you suspect compromise):

1. In Alpaca, generate a new key pair under the same endpoint (paper or live)
2. Edit `/etc/hawkstrade-dash/env` with the new key + secret
3. `sudo systemctl restart hawkstrade-dashboard.service`
4. Revoke the old key in Alpaca

### Revoke dashboard access (lost device, etc.)

1. In Cloudflare Zero Trust → **Logs → Access** → review recent logins
2. Under **My Team → Users**, find the user and revoke active sessions
3. Optionally tighten the Access policy (require Country == US, require WARP,
   shorten session duration)

---

## Security notes

- **No inbound ports.** Verify with `sudo ss -tlnp | grep 8080` (loopback only)
  and your EC2 security group should still expose only port 22 to your IP.
- **The dashboard cannot read the bot's secrets.** systemd
  `InaccessiblePaths=` blocks the `hawkstrade-dash` user from `/dev/shm/.hawkstrade.env`,
  `/etc/hawkstrade/hawkstrade.env`, and `/etc/hawkstrade/hawkstrade.secrets`.
- **Defense-in-depth JWT validation.** Even though Cloudflare Access enforces
  identity at the edge, the FastAPI app independently verifies the
  `Cf-Access-Jwt-Assertion` header on every request (signed by Cloudflare's
  JWKS, audience-checked, email-allowlisted).
- **Fail closed on misconfiguration.** If `DASHBOARD_AUTH_MODE=cloudflare` but
  the team domain / AUD / allowlist are missing, the app refuses to start.
- **Pinned dependencies.** `requirements-dashboard.txt` uses `==` for every
  package. Bump them deliberately, not opportunistically.
- **Dashboard access log rotation.** `/etc/logrotate.d/hawkstrade-dashboard`
  retains 30 daily rotations of `logs/dashboard_access_*.log`.
- **No write actions, ever.** There are no buttons, forms, or POST endpoints
  in the app. Adding one requires re-architecting the auth layer.

---

## Troubleshooting

**Dashboard service won't start**

```bash
journalctl -u hawkstrade-dashboard.service -n 100 --no-pager
```

Common causes: missing `requirements-dashboard.txt` install in `.venv`, the
`hawkstrade-dash` user can't read `~/HawksTrade` (re-run the `chgrp`/`chmod`
in §1.2), the user cannot traverse `/home/ec2-user`, or
`/etc/hawkstrade-dash/env` is mode-locked away from the user.

If the journal shows:

```text
Changing to the requested working directory failed: Permission denied
Failed at step CHDIR ...
```

then apply the parent-directory fix from §1.2:

```bash
sudo setfacl -m u:hawkstrade-dash:--x /home/ec2-user
# fallback if ACL tools are unavailable:
# sudo chmod 711 /home/ec2-user
sudo systemctl restart hawkstrade-dashboard.service
```

**Cloudflared can't connect**

```bash
journalctl -u hawkstrade-cloudflared.service -n 100 --no-pager
```

Check `/etc/cloudflared/config.yml` UUID matches the credentials file, and
confirm the EC2 has outbound TCP 443 to `*.cloudflare.com` (default for any
EC2 with NAT/IGW).

**Cloudflare Access page shows but dashboard returns 401**

The FastAPI JWT validation is rejecting the token. Verify `CF_ACCESS_AUD`
matches the AUD tag in Zero Trust → Access → Applications → your app →
Overview. The issuer (`https://<team>.cloudflareaccess.com`) must also match
exactly — no trailing slash.

**Phone shows the page but data is stale / red banner**

The dashboard polls every 15s. Check the EC2 has internet egress and the
read-only Alpaca key is correct:

```bash
sudo systemctl restart hawkstrade-dashboard.service
journalctl -u hawkstrade-dashboard.service -f
```

You should see no Alpaca authentication errors. If you do, re-check the key
in `/etc/hawkstrade-dash/env`.

**You see traffic from unfamiliar IPs in `dashboard_access_*.log`**

This should be impossible (Cloudflare Access blocks unauthenticated requests
at the edge). If it happens, check Cloudflare Access policy hasn't been
weakened, and treat as an incident: stop both services, audit the Access logs
in Cloudflare Zero Trust, and rotate the read-only Alpaca key.

---

## Uninstalling the dashboard

```bash
sudo systemctl disable --now hawkstrade-cloudflared.service hawkstrade-dashboard.service
sudo rm /etc/systemd/system/hawkstrade-cloudflared.service /etc/systemd/system/hawkstrade-dashboard.service
sudo systemctl daemon-reload

sudo rm -rf /etc/hawkstrade-dash /etc/cloudflared
sudo userdel hawkstrade-dash
sudo userdel cloudflared

# Optionally also remove cloudflared and the dashboard deps
sudo rpm -e cloudflared
.venv/bin/pip uninstall -y -r requirements-dashboard.txt
```

In Cloudflare Zero Trust, also delete the tunnel and the Access application
to free their slots.
