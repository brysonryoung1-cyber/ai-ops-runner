# VPS Deployment Guide — ai-ops-runner

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    VPS (Private-Only)                     │
│                                                          │
│  ┌──────────────────────────────────────────────┐        │
│  │  Docker Compose (/opt/ai-ops-runner)         │        │
│  │  ├─ postgres     (internal network only)     │        │
│  │  ├─ redis        (internal network only)     │        │
│  │  ├─ test_runner_api  (127.0.0.1:8000)       │        │
│  │  └─ test_runner_worker                       │        │
│  └──────────────────────────────────────────────┘        │
│                          │                               │
│  tailscale serve ────────┘                               │
│  (HTTPS on tailnet, proxies to 127.0.0.1:8000)          │
│                                                          │
│  UFW: deny all incoming except tailscale0 + UDP 41641   │
│  SSH: Tailscale SSH only (no public SSH)                 │
└──────────────────────────────────────────────────────────┘
         │
         │ Tailscale (WireGuard)
         │
┌────────┴─────────┐
│  Your Tailnet    │
│  (private mesh)  │
└──────────────────┘
```

## Prerequisites

- A Linux VPS with SSH access (Ubuntu/Debian recommended)
- A Tailscale account + auth key (ephemeral/reusable, prefer tagged)
- The VPS user (`runner`) must have sudo access

## Quick Deploy

```bash
# First-time deploy (with Tailscale auth)
VPS_SSH_TARGET=runner@<VPS_PUBLIC_IP> \
TAILSCALE_AUTHKEY=tskey-auth-... \
./ops/vps_bootstrap.sh

# Subsequent deploys (Tailscale already configured, use Tailscale IP)
VPS_SSH_TARGET=runner@<TAILSCALE_IP> \
./ops/vps_deploy.sh
```

## What the Bootstrap Does

1. **Prerequisites**: Installs Docker, Docker Compose plugin, git, UFW
2. **Tailscale**: Installs and connects Tailscale with SSH enabled
3. **UFW**: Denies all public incoming; allows only Tailscale interface + WireGuard UDP
4. **GitHub DNS**: Verifies DNS resolution for github.com, fixes if broken
5. **Repository**: Clones or pulls `/opt/ai-ops-runner` from origin/main
6. **Docker Compose**: Builds and starts the stack (`docker compose up -d --build`)
7. **Systemd**: Installs and enables services + timers (see below)
8. **Smoke Test**: Runs `./ops/runner_smoke.sh` to verify the stack works

## Systemd Units

| Unit | Type | Schedule | Purpose |
|------|------|----------|---------|
| `ai-ops-runner.service` | oneshot (RemainAfterExit) | boot | Main compose stack |
| `ai-ops-runner-update.service` | oneshot | — | Review-gated self-update |
| `ai-ops-runner-update.timer` | timer | every 15 min | Triggers update service |
| `ai-ops-runner-smoke.service` | oneshot | — | Smoke test runner |
| `ai-ops-runner-smoke.timer` | timer | daily 06:00 UTC | Triggers smoke test |

### Self-Update (Review-Gated)

The update service (`vps_self_update.sh`) enforces the review gate:

1. `git fetch origin main`
2. Check if `docs/LAST_REVIEWED_SHA.txt` on origin/main matches origin/main HEAD
3. If they match: the code was APPROVED by `ship_auto.sh` — proceed with update
4. If they don't match: **FAIL CLOSED** — skip the update
5. On docker compose failure: **ROLLBACK** to the previous known-good HEAD

This means:
- Only code that passed the Codex review pipeline reaches the VPS
- If Codex is unavailable (no review happened), the VPS stays on the last approved version
- If docker compose breaks after update, automatic rollback occurs

## Operator Commands

### Check health
```bash
VPS_SSH_TARGET=runner@<TAILSCALE_IP> ./ops/vps_doctor.sh
```

### View service status
```bash
ssh runner@<TAILSCALE_IP> "systemctl status ai-ops-runner.service"
ssh runner@<TAILSCALE_IP> "systemctl list-timers ai-ops-runner-*"
```

### View logs
```bash
# Docker compose logs
ssh runner@<TAILSCALE_IP> "cd /opt/ai-ops-runner && docker compose logs --tail=50"

# Update logs
ssh runner@<TAILSCALE_IP> "journalctl -u ai-ops-runner-update -n 50 --no-pager"

# Smoke test logs
ssh runner@<TAILSCALE_IP> "journalctl -u ai-ops-runner-smoke -n 50 --no-pager"
ssh runner@<TAILSCALE_IP> "ls -la /var/log/ai-ops-runner/"
```

### Trigger manual smoke test
```bash
ssh runner@<TAILSCALE_IP> "cd /opt/ai-ops-runner && API_BASE=http://127.0.0.1:8000 ./ops/runner_smoke.sh"
```

### Force update (bypass timer)
```bash
ssh runner@<TAILSCALE_IP> "sudo systemctl start ai-ops-runner-update.service"
```

### Restart stack
```bash
ssh runner@<TAILSCALE_IP> "cd /opt/ai-ops-runner && docker compose restart"
```

## Security Model

1. **No public ports**: Postgres/Redis have no published ports. API binds to `127.0.0.1` only.
2. **Tailscale-only access**: UFW denies all public incoming. Only Tailscale interface traffic is allowed.
3. **Tailscale SSH**: SSH access is via Tailscale SSH only (no public port 22).
4. **tailscale serve**: API is exposed to the tailnet via HTTPS, proxying to `127.0.0.1:8000`.
5. **No secrets in repo**: Auth keys and tokens live on the VPS only, never committed.
6. **Review-gated updates**: VPS only deploys code that passed the `ship_auto.sh` review pipeline.
7. **Rollback on failure**: If docker compose fails after update, automatic rollback to last-known-good.

## Secrets Management

Secrets NEVER go in the repo. On the VPS:

```bash
# Optional: create env file for additional secrets
sudo mkdir -p /etc/ai-ops-runner
sudo tee /etc/ai-ops-runner/env >/dev/null <<EOF
# Add secrets here if needed (e.g., GitHub deploy key for private repos)
# GITHUB_TOKEN=ghp_...
EOF
sudo chmod 600 /etc/ai-ops-runner/env
```

For private repo access (e.g., algo-nt8-orb), use a **read-only deploy key** or **fine-grained personal access token** with `contents:read` only. Never store tokens with write/push access.
