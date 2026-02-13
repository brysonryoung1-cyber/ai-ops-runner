# OpenClaw Transfer Packet

## Purpose

This document captures the complete state of the OpenClaw control plane for handoff between operators, models, or sessions. It is a point-in-time snapshot; the canonical living state is in `docs/HANDOFF_CURRENT_STATE.md`.

## System Identity

- **Name**: OpenClaw
- **Role**: Private-only control plane for ai-ops-runner infrastructure
- **Primary Host**: aiops-1 (VPS, Tailscale-connected)
- **Repo**: `ai-ops-runner` (GitHub, single branch: `main`)
- **Access Model**: Tailscale mesh only — no public endpoints

## Components

### 1. Infrastructure Doctor (`ops/openclaw_doctor.sh`)

Health checker that verifies:
- Tailscale connectivity
- Docker Compose stack health
- Runner API healthz (127.0.0.1:8000)
- Public port audit (tailnet-aware; fail-closed)

Exit 0 = all pass. Exit 1 = failures. Runs hourly via `openclaw-doctor.timer`.

### 2. Regression Guard (`ops/openclaw_guard.sh`)

Continuous enforcement loop (10-minute timer):
- Runs doctor; if PASS → log + exit
- If FAIL → checks Tailscale up AND sshd publicly bound
- If both → safe remediation via `openclaw_fix_ssh_tailscale_only.sh`
- If Tailscale down → NEVER touches sshd (lockout prevention)

### 3. SSH Hardening (`ops/openclaw_fix_ssh_tailscale_only.sh`)

Locks sshd to Tailscale IP only:
- Detects/disables all socket-activation units
- Writes drop-in config with `ListenAddress <TAILSCALE_IP>`
- Validates with `sshd -T`; rollback on failure

### 4. Console (`apps/openclaw-console/`)

Next.js private management UI:
- Bound to 127.0.0.1:8787 only
- Token auth (Keychain-backed `X-OpenClaw-Token`)
- 7 allowlisted SSH actions (doctor, apply, guard, ports, timer, journal, artifacts)
- CSRF protection via origin validation
- Tailscale CGNAT IP validation on target host

### 5. Runner Stack (Docker Compose)

- Postgres + Redis (internal network, no published ports)
- API on 127.0.0.1:8000 only
- Worker processes jobs from Redis queue
- Job/repo allowlists enforced
- Read-only worktrees with mutation detection

### 6. Review Pipeline

- `review_bundle.sh` → bounded diff generation
- `review_auto.sh` → Codex-powered review (bundle or packet mode)
- `review_finish.sh` → baseline advance + push
- `ship_auto.sh` → full autopilot (test → review → heal → push)

### 7. Notifications (`ops/openclaw_notify.sh`)

Outbound-only Pushover alerts:
- Doctor failures → alert with check_id + remediation
- Guard regressions → rate-limited alerts
- Job failures / SIZE_CAP warnings

### 8. Heal Entrypoint (`ops/openclaw_heal.sh`)

One-command apply + verify + evidence:
- Pre-check private-only posture
- Optionally apply hardened fixes
- Run doctor (fail if not PASS)
- Capture evidence bundle to `./artifacts/evidence/`

## Secrets Management

| Secret | Storage | Resolution Order |
|--------|---------|-----------------|
| `OPENAI_API_KEY` | Keychain / env / Linux file | env → keychain → `/etc/ai-ops-runner/secrets/openai_api_key` |
| `OPENCLAW_CONSOLE_TOKEN` | Keychain | `ops/openclaw_console_token.py rotate` |
| `PUSHOVER_APP_TOKEN` | env / Keychain / file | env → keychain → `/etc/ai-ops-runner/secrets/pushover_app_token` |
| `PUSHOVER_USER_KEY` | env / Keychain / file | env → keychain → `/etc/ai-ops-runner/secrets/pushover_user_key` |

**Invariants**: Keys NEVER printed to human output. Doctor verifies presence + masked fingerprint only.

## Network Policy

| Allowed Bind | CIDR | Purpose |
|-------------|------|---------|
| Loopback | 127.0.0.0/8, ::1 | Local services |
| Tailscale | 100.64.0.0/10 | Mesh access |

Everything else is FAIL. Enforced by doctor, guard, and UFW.

## Deployment

From local Mac:
```bash
./ops/openclaw_apply_remote.sh          # sync + build + fix + verify
```

On VPS:
```bash
cd /opt/ai-ops-runner && git fetch origin && git reset --hard origin/main
docker compose up -d --build
sudo ./ops/openclaw_fix_ssh_tailscale_only.sh
./ops/openclaw_doctor.sh
```

## Out of Scope

- NT8 / NinjaTrader strategy code and deployment
- ORB C# strategy internals (ORB permitted only as runner use-case: review bundles, log audit, artifacts)
- Windows VPS mechanics
