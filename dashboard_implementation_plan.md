# HawksTrade Dashboard — Implementation Plan

**Goal:** expose a personal, read-only dashboard showing system health, open positions, realized/unrealized P&L, and recent trades. Accessible from laptop and phone. Zero inbound ports on EC2. MFA-enforced identity at the edge.

**Audience:** a single user (Arun). Plan is written for another agent to implement end-to-end.

**Architecture:** FastAPI app on the EC2 binds to `127.0.0.1:8080` only. Cloudflare Tunnel (`cloudflared`) runs as an outbound-only daemon on the same EC2 and connects the local app to Cloudflare's edge. Cloudflare Access enforces identity (Google SSO + email OTP fallback) before any request reaches the tunnel.

```
Phone / Laptop
     │ HTTPS (Cloudflare-managed TLS)
     ▼
Cloudflare Edge
     │ Cloudflare Access — identity check (Google SSO + MFA)
     ▼
Cloudflared tunnel (outbound connection only — NO inbound port on EC2)
     │ loopback
     ▼
FastAPI app on 127.0.0.1:8080
     │ read-only
     ▼
data/trades.csv, data/daily_loss_baseline.json,
logs/, check_systemd.sh output, Alpaca (read-only key)
```

## Non-negotiable security principles

These rules must hold through the entire implementation. If the implementing agent cannot satisfy one, it must stop and surface the issue, not work around it.

1. **Read-only dashboard.** No buttons, forms, or API endpoints that place orders, cancel orders, change config, restart services, or mutate files on disk. Any future write capability must be a separate service behind a separate auth path.
2. **FastAPI binds to `127.0.0.1` only.** Never `0.0.0.0`. Verified by `ss -tlnp | grep 8080` showing only loopback. No direct inbound port from the internet, ever.
3. **No Alpaca trading keys in the dashboard.** The dashboard uses a dedicated *read-only* Alpaca API key pair stored separately from the bot's trading keys. See §6.
4. **Dashboard process runs as a dedicated non-root user** (`hawkstrade-dash`) with read-only filesystem access to `data/` and `logs/`, and no access to `/dev/shm/.hawkstrade.env` or the trading key env vars.
5. **Every request logged.** Identity, path, IP, timestamp, response code. Stored in `logs/dashboard_access_YYYYMMDD.log`. Reviewed periodically.
6. **TLS only.** Cloudflare terminates TLS with its own cert; the tunnel is encrypted. No HTTP fallback, no self-signed certs anywhere in the chain.
7. **Pinned dependencies.** `requirements-dashboard.txt` uses `==` for every package. Separate from the bot's `requirements.txt`.
8. **No secrets in the code or in the repo.** Cloudflare tunnel credentials, read-only Alpaca key, and any session secret live in `/etc/hawkstrade-dash/env` (mode 600, owned by `hawkstrade-dash`), loaded at systemd unit start.

## Scope

### In scope
- System health panel (status of each hawkstrade-*.timer and .service, last run, next run, failure reason)
- Open positions with realized/unrealized P&L, entry price, current price, strategy, hold days
- Today's realized P&L (from closed trades in `data/trades.csv` with today's NY-session exit date)
- Session-to-date unrealized P&L (from Alpaca `get_all_positions()` — `position.unrealized_pl` summed)
- Portfolio value, cash, buying power
- Daily loss baseline vs current value (shows remaining headroom before the 5% kill switch trips)
- Last 30 closed trades table
- Per-strategy trade count and win rate (last 30 days)
- Staleness indicators on every panel (when was this data last refreshed)
- Health-check aggregate status (red/yellow/green) driven by `scripts/check_systemd.sh` + recent error log presence

### Out of scope for this iteration
- Any write actions (trade, cancel, restart, config change)
- Historical charting beyond a simple equity-curve sparkline
- Multi-user support, roles, shared access
- Mobile-native app (the dashboard is a responsive web page; phone use is via Safari/Chrome)
- Real-time push (WebSockets/SSE) — dashboard uses polling every 15s client-side

## Implementation phases

Phase 1 is the dashboard app running behind SSH tunnel only. Phase 2 layers Cloudflare Tunnel + Access on top. This ordering lets Phase 1 be tested end-to-end before introducing the Cloudflare dependency.

## Phase 1 — FastAPI dashboard app (local-only)

### 1.1 Project layout

Create a new top-level `dashboard/` directory:

```
dashboard/
  __init__.py
  app.py                 # FastAPI app factory, routes mounted here
  config.py              # Dashboard-specific config loader (reads config/config.yaml + /etc/hawkstrade-dash/env)
  security.py            # Access logging middleware, Cloudflare JWT validation (Phase 2)
  alpaca_readonly.py     # Read-only Alpaca client wrapper — imports safe functions only from core.alpaca_client
  data_sources.py        # Functions: read_trades(), read_daily_baseline(), read_health_check()
  pnl.py                 # Realized/unrealized P&L calculators (pure functions, heavily unit tested)
  templates/
    base.html            # Shared layout (Tailwind via CDN, no build step)
    dashboard.html       # Main view
  static/
    app.js               # ~100 lines; polls /api/state every 15s, updates DOM
  tests/
    __init__.py
    test_pnl.py
    test_data_sources.py
    test_security.py
    test_app.py
requirements-dashboard.txt
scheduler/systemd/
  hawkstrade-dashboard.service   # Runs the FastAPI app
  hawkstrade-cloudflared.service # Phase 2: runs cloudflared tunnel
```

### 1.2 Dependencies (`requirements-dashboard.txt`)

```
fastapi==0.115.0
uvicorn[standard]==0.32.0
jinja2==3.1.4
python-multipart==0.0.12
pydantic==2.9.2
pyjwt[crypto]==2.9.0
cryptography==43.0.1
```

Separate from `requirements.txt` — the bot doesn't need these; the dashboard doesn't need Alpaca-SDK if it imports from the bot's existing `core/alpaca_client.py`.

### 1.3 Data sources

The dashboard reads from three places, in order of preference:

**Local files (already produced by the bot — zero added load on Alpaca):**
- `data/trades.csv` — all trades, open and closed
- `data/daily_loss_baseline.json` — today's session start value
- `data/performance.csv` — periodic snapshots
- `logs/scan_YYYYMMDD.log`, `logs/risk_YYYYMMDD.log`, `logs/errors.log` — most recent 50 lines each for the health panel
- `scripts/check_systemd.sh` output — run it with a subprocess call, parse the sections

**Alpaca (read-only key):**
- `get_all_positions()` — needed for live unrealized P&L
- `get_portfolio_value()`, `get_cash()`, `get_buying_power()` — account summary

**No mutation endpoints touched.** The implementing agent must audit every call and confirm it maps to a `GET` on Alpaca, not a `POST`/`DELETE`.

### 1.4 Endpoints

All endpoints are GET. All return JSON except `/` which returns HTML.

| Path | Returns | Notes |
|---|---|---|
| `/` | HTML dashboard page | Server-rendered shell; client polls `/api/state` |
| `/api/state` | Full JSON snapshot of everything below | Single round-trip for the polling client |
| `/api/health` | systemd timer/service status + recent errors | Calls `check_systemd.sh`, parses output |
| `/api/positions` | Open positions with entry, current, P&L | From Alpaca |
| `/api/pnl/today` | Realized today, unrealized now, headroom vs daily-loss limit | |
| `/api/trades/recent` | Last 30 closed trades | From `data/trades.csv` |
| `/api/strategies/summary` | Per-strategy win rate + trade count (30d) | From `data/trades.csv` |
| `/healthz` | `{"status": "ok"}` with 200, or 503 if Alpaca unreachable | For the tunnel's own health check; no auth |

`/healthz` is the **one** unauthenticated endpoint — needed for the tunnel to verify the app is alive. It must return no sensitive information (no portfolio value, no symbol list). Just liveness.

### 1.5 P&L calculation (heavily tested)

This is the single hardest part to get right. Mistakes here show wrong numbers on a trusted dashboard, which is worse than showing nothing.

**Realized P&L (today):**
- Read `data/trades.csv`
- Filter to rows where `exit_timestamp` is non-null AND `exit_timestamp` converted to `America/New_York` date equals today's session date
- Sum `realized_pnl` column (or compute from entry/exit/qty if the column doesn't exist — verify what the bot writes)

**Unrealized P&L (now):**
- Call `ac.get_all_positions()`
- Sum `position.unrealized_pl` across all positions (Alpaca computes this server-side)
- Display stock and crypto subtotals separately — they behave very differently on weekends

**Session headroom:**
- Read `data/daily_loss_baseline.json` for the NY session's start value
- Compute `current_portfolio_value - baseline`
- Compare to `baseline * trading.daily_loss_limit_pct` (5%)
- Display as a progress bar: green → yellow at 50% of limit → red at 80% of limit

All three computations get unit tests with fixture CSVs covering:
- Empty trades file
- Today's trades only
- Today + prior days (filter must not include prior days)
- Same-symbol multiple entries (realized sum must be correct)
- Crypto pair symbols with `/`
- Timezone boundary (trade at 23:50 ET vs 00:10 ET next day — correct session assignment)

### 1.6 Templates and frontend

Single-page server-rendered shell with a small JS polling loop. No build step, no npm, no React — it's a personal dashboard.

- Tailwind via CDN (`https://cdn.tailwindcss.com`) for styling — accept the runtime JIT tradeoff for zero build tooling
- `static/app.js`: ~100 lines. Fetches `/api/state` every 15s, patches DOM via `innerText` updates to specific elements
- Layout: four panels stacked on mobile, 2×2 grid on desktop
  1. **Health** (top-left) — green/yellow/red summary + per-timer list
  2. **P&L today** (top-right) — realized, unrealized, headroom progress bar
  3. **Open positions** (bottom-left) — table with symbol, side, qty, entry, current, unrealized P&L%, strategy, hold days
  4. **Recent trades** (bottom-right) — last 30 closed, sortable columns
- Prominent "last refresh: HH:MM:SS" footer. Turns red if >60s stale.
- Dark mode by default (you'll be staring at it; less eye strain)

Explicitly **no action buttons**. If the implementing agent feels the urge to add a "refresh" or "cancel" button, they must stop and flag.

### 1.7 Access logging middleware

FastAPI middleware that writes one line per request to `logs/dashboard_access_YYYYMMDD.log`:

```
2026-04-20T14:32:11Z identity=arun@gmail.com ip=1.2.3.4 method=GET path=/api/state status=200 duration_ms=42
```

In Phase 1 (SSH tunnel), identity is always `local-ssh`. In Phase 2 (Cloudflare Access), identity comes from the `Cf-Access-Authenticated-User-Email` header, which the middleware verifies against the decoded Cf-Access-Jwt-Assertion — see §2.3.

Log rotation: `logrotate` config in the implementation, keeping 30 days.

### 1.8 systemd unit: `hawkstrade-dashboard.service`

```ini
[Unit]
Description=HawksTrade Dashboard (read-only)
After=network.target
# Does NOT depend on hawkstrade-secrets.service — dashboard uses its own keys.

[Service]
Type=simple
User=hawkstrade-dash
Group=hawkstrade-dash
WorkingDirectory=/home/ec2-user/HawksTrade
EnvironmentFile=/etc/hawkstrade-dash/env
ExecStart=/home/ec2-user/HawksTrade/.venv/bin/uvicorn dashboard.app:app \
    --host 127.0.0.1 --port 8080 --log-level info --access-log
Restart=on-failure
RestartSec=10

# Hardening
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=read-only
ReadOnlyPaths=/home/ec2-user/HawksTrade
ReadWritePaths=/home/ec2-user/HawksTrade/logs
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
PrivateDevices=yes
RestrictSUIDSGID=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
RestrictRealtime=yes
SystemCallArchitectures=native
RestrictNamespaces=yes
# Block access to the shm secrets file by path.
InaccessiblePaths=/dev/shm/.hawkstrade.env

[Install]
WantedBy=multi-user.target
```

The `User=hawkstrade-dash` account must be created as a system user (`useradd --system --shell /sbin/nologin hawkstrade-dash`) with no home dir and no login. It needs read access to `data/`, `logs/`, `config/config.yaml`, and execute on `scripts/check_systemd.sh`.

### 1.9 Phase 1 verification

Before declaring Phase 1 complete, run:

1. `systemctl start hawkstrade-dashboard.service` — service starts cleanly
2. `ss -tlnp | grep 8080` — shows **only** `127.0.0.1:8080`, NOT `0.0.0.0:8080` or `*:8080`. If this shows public binding, stop and fix.
3. From your laptop: `ssh -L 8080:localhost:8080 ec2-user@<ec2-ip> -i <key>`, then `curl http://localhost:8080/healthz` returns 200
4. Open `http://localhost:8080` in browser — dashboard renders, all four panels populated
5. `sudo -u hawkstrade-dash cat /dev/shm/.hawkstrade.env` — **permission denied** (confirms isolation)
6. `sudo -u hawkstrade-dash python3 -c "from core.alpaca_client import place_market_order; print('oops')"` — this import succeeds (same codebase), but the user should not have the trading keys in its environment, so any actual trade call would fail. Verify by checking `printenv` under `hawkstrade-dash` shows no `ALPACA_*` keys except the read-only ones from `/etc/hawkstrade-dash/env`.
7. `python3 -m unittest dashboard.tests -v` — all dashboard tests pass
8. Full project test suite still passes: `python3 -m unittest discover -v`

## Phase 2 — Cloudflare Tunnel + Cloudflare Access

Only start this after Phase 1 is verified working end-to-end over SSH.

### 2.1 Cloudflare account prerequisites

User (Arun) does these in the Cloudflare dashboard before the agent starts Phase 2:
- Cloudflare account exists and has a domain on it (can buy a cheap `.dev` domain for this or use existing)
- Cloudflare Zero Trust enabled (free tier)
- Google SSO identity provider configured in Zero Trust → Settings → Authentication

Agent should confirm these exist by asking the user, not by trying to set them up.

### 2.2 Install and configure `cloudflared`

On the EC2:

```bash
# Install
sudo dnf install -y https://pkg.cloudflare.com/cloudflared-latest.x86_64.rpm

# Authenticate (opens a browser URL; user completes on their laptop)
cloudflared tunnel login

# Create the named tunnel
cloudflared tunnel create hawkstrade-dashboard

# Creates credentials file at ~/.cloudflared/<UUID>.json — must be moved to /etc/cloudflared/ and chowned to the cloudflared user
```

Tunnel config at `/etc/cloudflared/config.yml`:

```yaml
tunnel: <UUID>
credentials-file: /etc/cloudflared/<UUID>.json

ingress:
  - hostname: dashboard.<your-domain>
    service: http://127.0.0.1:8080
    originRequest:
      # Health check — cloudflared will mark tunnel healthy only when this returns 200
      noTLSVerify: false
      httpHostHeader: dashboard.<your-domain>
  - service: http_status:404
```

DNS: `cloudflared tunnel route dns hawkstrade-dashboard dashboard.<your-domain>` — creates a CNAME automatically.

### 2.3 Cloudflare Access policy

In Cloudflare Zero Trust → Access → Applications:

1. **Add application → Self-hosted**
2. **Application domain:** `dashboard.<your-domain>`
3. **Session duration:** 24 hours (re-auth daily — reasonable for a personal dashboard on mobile)
4. **Identity providers:** Google, One-time PIN (email fallback for Google outages)
5. **Policy — Allow:**
   - Emails: `arunbabuc.dev@gmail.com` (exact match, no wildcards, no domain-wide rules)
6. **Require** — add at least one:
   - MFA (if Google account has it enforced, this is automatic)
   - Country match (your country only) — optional additional layer
7. **CORS:** default (same-origin). No `*`.
8. **Log all requests:** enabled.

**Critical:** the policy **must be set to Allow with exact email match, not Bypass.** Bypass removes auth — the opposite of what you want.

### 2.4 Dashboard verifies Cloudflare JWT (defense in depth)

Cloudflare Access already enforces auth at the edge, but the dashboard also verifies the signed JWT on every request. This way, if someone ever misconfigures the tunnel to bypass Access, or chains past it, the app still rejects them.

In `dashboard/security.py`:

```python
# Pseudocode — implementing agent writes the real version
import jwt
from jwt import PyJWKClient

CF_TEAM_DOMAIN = os.environ["CF_ACCESS_TEAM_DOMAIN"]  # e.g., yourname.cloudflareaccess.com
CF_AUD = os.environ["CF_ACCESS_AUD"]  # Application Audience tag from Cloudflare

jwks_client = PyJWKClient(f"https://{CF_TEAM_DOMAIN}/cdn-cgi/access/certs")

async def require_cloudflare_access(request: Request) -> str:
    token = request.headers.get("Cf-Access-Jwt-Assertion")
    if not token:
        raise HTTPException(401, "missing Cf-Access-Jwt-Assertion header")
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=CF_AUD,
            issuer=f"https://{CF_TEAM_DOMAIN}",
        )
    except Exception as e:
        raise HTTPException(401, f"invalid Cloudflare Access JWT: {e}")
    return claims["email"]
```

Wired into every route via `Depends(require_cloudflare_access)` except `/healthz`.

In Phase 1 (local SSH), a `DASHBOARD_AUTH_MODE=local` env flag bypasses JWT verification and returns `"local-ssh"` as the identity. This flag must be absent or `cloudflare` in production — documented and asserted at startup.

### 2.5 systemd unit: `hawkstrade-cloudflared.service`

```ini
[Unit]
Description=Cloudflare Tunnel for HawksTrade Dashboard
After=network.target hawkstrade-dashboard.service
Requires=hawkstrade-dashboard.service

[Service]
Type=simple
User=cloudflared
Group=cloudflared
ExecStart=/usr/bin/cloudflared tunnel --config /etc/cloudflared/config.yml run
Restart=on-failure
RestartSec=10

NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
ReadOnlyPaths=/etc/cloudflared
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
```

### 2.6 Phase 2 verification

1. `systemctl start hawkstrade-cloudflared.service` — active, no restart loop
2. `cloudflared tunnel info hawkstrade-dashboard` — shows 1+ active connections to Cloudflare edge
3. On laptop, in an incognito window, open `https://dashboard.<your-domain>` — redirects to Google login, prompts MFA, then loads dashboard
4. On phone, same — loads cleanly after Google auth
5. In Cloudflare Access → Logs, see the successful login event
6. From a different Google account (if possible) or incognito with wrong email — denied with explicit "You are not authorized" page
7. `curl https://dashboard.<your-domain>/api/state` without cookies — returns 401/redirect, never leaks data
8. `curl https://dashboard.<your-domain>/healthz` — returns 200 (unauthenticated liveness, fine)
9. `ss -tlnp | grep 8080` on EC2 — still only loopback. `sudo lsof -i -P -n | grep LISTEN` — no unexpected public ports. **Confirm no inbound ports were added by the tunnel.**
10. In Cloudflare dashboard → Zero Trust → Access → Audit logs — all accesses from step 3-6 visible with identity, IP, country
11. Attempt to directly resolve the EC2 public IP at port 8080/80/443 from a laptop off-network — times out or refused. The EC2 security group should still block all inbound except 22 (SSH) from your IP.

## Section 6 — Read-only Alpaca key

The dashboard uses a **dedicated read-only API key pair**, stored separately from the bot's trading keys.

### How to generate

Alpaca currently doesn't expose a "read-only" key type in the UI — both paper and live keys have read+write scope by default. Two workable approaches, pick one:

**Option A (simpler): use the paper account's keys for the dashboard even in production.** Rationale: the bot might be trading live, but for *viewing* P&L, you're querying positions and account value — the paper account doesn't have the live P&L you want to see. So this option doesn't actually work for a live bot. Skip.

**Option B (correct): generate a second key pair on the live account and trust-but-rotate.** Alpaca allows multiple keys per account. Generate a second pair in the Alpaca dashboard, label it `dashboard-readonly`, and commit to never using it to place orders from code. Store in `/etc/hawkstrade-dash/env`:

```
ALPACA_API_KEY=PKXXXXXXXXXXXXXX         # "read-only" key — dashboard only
ALPACA_SECRET_KEY=XXXXXXXXXXXXXXXXXXXX
ALPACA_BASE_URL=https://api.alpaca.markets  # live or paper matching config.mode
```

Rotate this key every 90 days. Log the rotation in the repo's `cloud-setup/` notes (not the key itself).

The dashboard's `alpaca_readonly.py` wrapper:

```python
# Hard fail if any of these appear in code paths that reach this module.
FORBIDDEN_FUNCTIONS = {
    "place_market_order",
    "place_limit_order",
    "cancel_order",
    "close_position",
    "close_all_positions",
}
# Runtime guard — call this in every method to enforce that only read functions are invoked.
```

The wrapper exposes only: `get_account`, `get_portfolio_value`, `get_cash`, `get_buying_power`, `get_all_positions`, `get_position`. Importing anything else from `core.alpaca_client` is forbidden. A unit test enforces this by inspecting the wrapper's imports.

## Failure modes and runbook

### Dashboard shows stale numbers
Check the "last refresh" timestamp. If >60s, check `systemctl status hawkstrade-dashboard.service`. If the service is up but not refreshing, check `logs/dashboard_access_*.log` for the polling pattern and `journalctl -u hawkstrade-dashboard -n 100` for errors.

### Dashboard shows 502 via Cloudflare
Tunnel is up but the backend isn't responding. Check `hawkstrade-dashboard.service` first, then `hawkstrade-cloudflared.service`.

### Cloudflare Access login loop
Session cookie from Cloudflare isn't being accepted. Usually means the `CF_ACCESS_AUD` env var in `/etc/hawkstrade-dash/env` doesn't match the Application Audience tag in Cloudflare. Get the current tag from Cloudflare Zero Trust → Access → Applications → Edit → overview.

### Dashboard shows wrong P&L
**Do not edit `data/trades.csv` to fix the display.** That breaks the bot's source of truth. If a test exposes a calculator bug, fix the calculator in `dashboard/pnl.py` and add a regression test. If the CSV itself is wrong (e.g., a trade wasn't logged), that's a bot bug, not a dashboard bug.

### Alpaca read-only key leaked / unexpected activity
1. Revoke the key in Alpaca dashboard immediately
2. Rotate trading keys too (precaution)
3. Review Cloudflare Access audit logs for unexpected logins
4. Review `logs/dashboard_access_*.log` for unusual paths/frequencies
5. Generate new read-only key, update `/etc/hawkstrade-dash/env`, restart `hawkstrade-dashboard.service`

### EC2 security group accidentally opened to the internet on 8080
The dashboard would still require Cloudflare Access in the browser flow, but direct curl to the EC2:8080 would bypass it. This is the exact failure mode Phase 2 is designed to prevent. The fix: remove the SG rule. The prevention: verify §2.6 step 11 after every SG change.

## Testing strategy

### Unit tests (fast, CI-friendly)
- `test_pnl.py` — realized/unrealized P&L with fixture CSVs, timezone-boundary cases, empty-file cases
- `test_data_sources.py` — CSV parsing, baseline JSON parsing, log parsing, check_systemd subprocess parsing (mocked)
- `test_security.py` — JWT verification with a local RSA key pair (no network), reject missing/expired/wrong-aud tokens, accept valid tokens, local-mode bypass works
- `test_app.py` — FastAPI TestClient, all endpoints return 401 without auth when in cloudflare mode, return 200 with valid JWT, `/healthz` always 200

### Integration tests (manual, checklist in Phase 1 §1.9 and Phase 2 §2.6)
Full end-to-end verification. Can't be automated because they require real Cloudflare edge and real EC2.

### Security checklist before go-live

- [ ] FastAPI binds only to 127.0.0.1 (verified with `ss -tlnp`)
- [ ] `hawkstrade-dash` user cannot read `/dev/shm/.hawkstrade.env`
- [ ] `hawkstrade-dash` user has no `ALPACA_PAPER_*` or `ALPACA_LIVE_*` env vars (only the read-only key)
- [ ] Dashboard wrapper's unit test confirms no mutation functions imported
- [ ] All endpoints except `/healthz` require auth (TestClient verifies)
- [ ] `DASHBOARD_AUTH_MODE=cloudflare` in production; startup asserts this
- [ ] Cloudflare Access policy is Allow (not Bypass) with exact email match
- [ ] Cloudflare Access session duration ≤ 24h
- [ ] EC2 security group has no inbound rule for 8080/80/443
- [ ] Cloudflare audit logs enabled
- [ ] Dashboard access log rotation configured (30d retention)
- [ ] Read-only Alpaca key rotation reminder set for 90 days out
- [ ] `requirements-dashboard.txt` pinned with `==`
- [ ] Full test suite passes: `python3 -m unittest discover -v` (current baseline: 228 tests)
- [ ] Dashboard test suite passes: `python3 -m unittest dashboard.tests -v`

## Open decisions for the implementer

These are small enough not to block the plan, but must be decided during implementation:

1. **Polling interval:** plan says 15s; if this generates too many Alpaca calls, back off to 30s. Alpaca's rate limits are 200 req/min; dashboard should use <1% of that.
2. **Stock vs. crypto P&L segmentation:** whether to show them as subtotals or separate cards. Recommend separate cards — they behave very differently at market close.
3. **Staleness threshold:** plan says 60s triggers red. For crypto this might be fine on weekends too; for stocks during market hours, 60s is aggressive. Consider two thresholds: 60s during market hours, 5m otherwise. Not critical for v1.
4. **Domain choice:** user to choose. Recommend a dedicated domain (`hawkstrade.<something>`) not shared with any public website. Cheap `.dev` domain is ideal.

## Estimated effort

| Phase | Effort | What you get |
|---|---|---|
| Phase 1 | 1-1.5 days of focused work | Working dashboard accessible via SSH tunnel |
| Phase 2 | 3-4 hours once Phase 1 is solid | Public HTTPS URL with MFA, no EC2 inbound ports |
| Hardening + tests | 0.5 day | Confidence it won't regress or leak |

**Total: ~2-3 days for a single implementer.**

## What the implementing agent must NOT do

1. Do not add any endpoint that mutates state. If asked to "just add a cancel button," refuse and file an issue.
2. Do not bind FastAPI to anything other than 127.0.0.1.
3. Do not skip the `hawkstrade-dash` system user — do not run as `ec2-user` or `root`.
4. Do not embed the Alpaca key in code, config.yaml, or any file that gets committed to git.
5. Do not disable the Cloudflare JWT check "temporarily for testing." Use `DASHBOARD_AUTH_MODE=local` when SSH-tunneling; never disable auth in cloudflare mode.
6. Do not weaken the Cloudflare Access policy (e.g., allow `*@gmail.com`, enable Bypass). Single-email allowlist only.
7. Do not store any secret in the repo. `/etc/hawkstrade-dash/env` is outside the repo by design.
8. Do not commit `.cloudflared/` credentials. Add to `.gitignore` if it isn't already.
9. Do not add a "download trades.csv" endpoint. If a download is needed later, add it with an explicit audit log event and a second MFA prompt.
10. Do not increase Cloudflare Access session duration beyond 24h without explicit user approval.
