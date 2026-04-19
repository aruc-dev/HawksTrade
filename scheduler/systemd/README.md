# HawksTrade systemd Deployment

These templates are for production Linux hosts, especially EC2 instances that
should run HawksTrade without cron. They use UTC schedules matching
`scheduler/cron/hawkstrade-utc.cron` during US daylight saving time.

The services are intentionally one-shot jobs. They do not auto-restart after a
failed trading command because retrying order-mutating jobs can create duplicate
intentions. The next timer run is the retry boundary.

## Templates

- `hawkstrade-secrets.service` loads `/etc/hawkstrade/hawkstrade.secrets` into
  `/dev/shm/.hawkstrade.env`. It is intentionally a non-persistent one-shot:
  dependent services start it before each job so the tmpfs secret is recreated
  if `/dev/shm` was cleared after boot. The generated file is `root`-owned and
  group-readable by the HawksTrade service group so `systemd-logind` does not
  remove it as `ec2-user` IPC when SSH/user sessions close.
- `hawkstrade-stock-scan.service` and `.timer` run the 9:35 AM ET stock-only scan.
- `hawkstrade-full-scan.service` and `.timer` run hourly full scans.
- `hawkstrade-crypto-scan.service` and `.timer` run hourly crypto-only scans.
- `hawkstrade-risk-check.service` and `.timer` run risk checks every 15 minutes
  during market hours.
- `hawkstrade-daily-report.service` and `.timer` run weekday daily reports.
- `hawkstrade-weekly-report.service` and `.timer` run Monday weekly reports.
- `hawkstrade-health-check.service` and `.timer` run the Linux health check every
  15 minutes.

## Install

Replace the project path and service user placeholders before installing:

```bash
cd /home/ec2-user/HawksTrade
export PROJECT=/home/ec2-user/HawksTrade
export HT_USER=ec2-user
export HT_GROUP=ec2-user

sudo install -d -m 0750 /etc/hawkstrade
sudo install -m 0600 scheduler/systemd/hawkstrade.env.example /etc/hawkstrade/hawkstrade.env
sudo editor /etc/hawkstrade/hawkstrade.env

# Create this file with Alpaca credentials only. Do not commit it.
sudo editor /etc/hawkstrade/hawkstrade.secrets
sudo chmod 0600 /etc/hawkstrade/hawkstrade.env /etc/hawkstrade/hawkstrade.secrets

tmpdir="$(mktemp -d)"
cp scheduler/systemd/*.service scheduler/systemd/*.timer "$tmpdir"/
sudo sed -i \
  -e "s|/home/ec2-user/HawksTrade|$PROJECT|g" \
  -e "s|User=ec2-user|User=$HT_USER|g" \
  -e "s|Group=ec2-user|Group=$HT_GROUP|g" \
  "$tmpdir"/*.service

sudo cp "$tmpdir"/*.service "$tmpdir"/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
```

Load secrets first:

```bash
sudo systemctl enable --now hawkstrade-secrets.service
sudo -u "$HT_USER" test -s /dev/shm/.hawkstrade.env
sudo -u "$HT_USER" test -r /dev/shm/.hawkstrade.env
```

Enable timers:

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

## Operate

View timers:

```bash
systemctl list-timers 'hawkstrade-*'
```

Run safe checks manually:

```bash
sudo systemctl start hawkstrade-health-check.service
sudo systemctl start hawkstrade-daily-report.service
```

Inspect logs:

```bash
journalctl -u hawkstrade-risk-check.service -n 100 --no-pager
journalctl -u hawkstrade-health-check.service -n 100 --no-pager
```

Disable timers:

```bash
sudo systemctl disable --now 'hawkstrade-*.timer'
```

## Notes

- Trading services require `hawkstrade-secrets.service` and
  `network-online.target`. The health-check service only wants the secret loader
  so it can still run and report missing or stale secrets.
- `hawkstrade-secrets.service` does not use `RemainAfterExit`; it should be
  allowed to return to `inactive (dead)` after copying secrets. This avoids a
  stale `active (exited)` state when `/dev/shm/.hawkstrade.env` disappears.
- The `/dev/shm/.hawkstrade.env` file is installed as `root:$HT_GROUP` with mode
  `0640`; do not change it to be owned by the login user, or logind may remove
  it when that user's sessions end.
- Scan, risk-check, and report services run through
  `scripts/run_hawkstrade_job.sh`, so they use the project `.venv`, Alpaca
  preflight checks, and the shared trade-mutation lock.
- The health-check service uses `.venv/bin/python`, then `.venv/bin/python3`,
  then `python3` as a fallback.
- Health checks write timestamped HTML/JSON snapshots to
  `reports/health_snapshots/`. Set `HAWKSTRADE_HEALTH_SNAPSHOT_RETENTION_DAYS`
  in `/etc/hawkstrade/hawkstrade.env` to adjust retention.
- Keep `HAWKSTRADE_REQUIRE_SHM=1` enabled on EC2 so missing RAM secrets fail
  closed instead of falling back to disk dotenv files.
