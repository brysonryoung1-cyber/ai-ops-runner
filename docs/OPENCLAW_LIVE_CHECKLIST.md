# OpenClaw Live Checklist

**If it's live, ALL of these must be true.**

Last Updated: 2026-02-15

## Quick Verify (from Mac)

```bash
# One-command verify from local Mac:
./ops/openclaw_vps_deploy.sh --dry-run   # Preview steps
ssh root@100.123.61.57 'cd /opt/ai-ops-runner && ./ops/openclaw_doctor.sh'
```

## Checklist

### Infrastructure

- [ ] **VPS reachable**: `ssh root@100.123.61.57 true` exits 0
- [ ] **Repo synced**: `/opt/ai-ops-runner` is on `origin/main` HEAD
- [ ] **Docker stack healthy**: `docker compose ps` shows all services running

### Security (fail-closed)

- [ ] **No public ports**: `ss -tlnp` shows only 127.0.0.0/8, ::1, or 100.64.0.0/10 binds
- [ ] **sshd Tailscale-only**: sshd bound to Tailscale IP only (not 0.0.0.0 or [::])
- [ ] **Docker ports private**: `docker ps --format '{{.Ports}}'` shows only 127.0.0.1 binds
- [ ] **Console private**: Port 8787 bound to 127.0.0.1 only

### Doctor (all checks PASS)

- [ ] **Tailscale up**: `tailscale status` connected
- [ ] **Docker healthy**: All Compose services running
- [ ] **API healthz**: `curl -sf http://127.0.0.1:8000/healthz` returns OK
- [ ] **Public port audit**: PASS (tailnet-aware policy)
- [ ] **Docker port audit**: PASS (allowed CIDRs only)
- [ ] **Disk healthy**: Usage below critical threshold
- [ ] **OpenAI key present**: Masked fingerprint shows `sk-...xxxx`
- [ ] **Console bind OK**: 127.0.0.1:8787 only
- [ ] **Guard timer active**: `systemctl is-active openclaw-guard.timer` = active

### Guard

- [ ] **Timer active**: `systemctl is-active openclaw-guard.timer` returns "active"
- [ ] **Recent runs**: `tail -5 /var/log/openclaw_guard.log` shows recent PASS entries
- [ ] **Never locks out**: Guard skips sshd changes if Tailscale is down

### Console (phone access)

- [ ] **Running**: Console container running (`docker compose -f docker-compose.yml -f docker-compose.console.yml ps`)
- [ ] **Bound privately**: `ss -tlnp | grep :8787` shows 127.0.0.1 only
- [ ] **Tailscale Serve active**: `tailscale serve status` shows HTTPS:443 → http://127.0.0.1:8787
- [ ] **Phone URL works**: `https://aiops-1.tailc75c62.ts.net` accessible from Tailscale device

### Notifications

- [ ] **Pushover configured**: `python3 ops/openai_key.py status` (or check Pushover tokens)
- [ ] **Rate limiting active**: De-dupe window per check_id per 30 min
- [ ] **Events wired**: Doctor FAIL, Guard FAIL, Deploy FAIL/PASS, Nightly Job FAIL, SIZE_CAP WARN

### Keys

- [ ] **OpenAI key**: Present (env → keychain → file), never printed raw
- [ ] **Pushover tokens**: Present (app + user), never printed raw
- [ ] **Console token**: Present (`/etc/ai-ops-runner/secrets/openclaw_console_token`)

## Verification Commands (run on VPS)

```bash
# Full doctor
cd /opt/ai-ops-runner && ./ops/openclaw_doctor.sh

# Guard status
systemctl status openclaw-guard.timer --no-pager
tail -20 /var/log/openclaw_guard.log

# Port audit
ss -tlnp

# Docker containers
docker compose ps
docker ps --format 'table {{.Names}}\t{{.Ports}}\t{{.Status}}'

# Tailscale serve
tailscale serve status

# Console bind
ss -tlnp | grep :8787

# Key health (masked)
python3 ops/openai_key.py status
```

## One-Command Deploy

```bash
# From Mac (CSR/Cursor):
./ops/openclaw_vps_deploy.sh

# Dry run:
./ops/openclaw_vps_deploy.sh --dry-run
```

## Boundary: NOT installed

- openclaw.ai assistant is NOT installed on the ops plane
- See `docs/OPENCLAW_SUPPLY_CHAIN.md` for rationale
- "OpenClaw" in this project = private ops runner + doctor/guard + console only
