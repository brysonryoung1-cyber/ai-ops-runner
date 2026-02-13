# OpenClaw V1 — Architecture

## Overview

OpenClaw is an orchestration layer that drives `ai-ops-runner` lanes from a
single nightly/hourly cadence.  It does **not** replace the runner — it
coordinates jobs, health checks, and audit trails on top of it.

```
┌──────────────────────────────────────────────────────┐
│                    OpenClaw Orchestrator              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ │
│  │  Infra   │ │   ORB    │ │ Content  │ │ Browser │ │
│  │  Lane    │ │  Lane    │ │  Lane    │ │  Lane   │ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬────┘ │
│       │            │            │             │      │
└───────┼────────────┼────────────┼─────────────┼──────┘
        │            │            │             │
        ▼            ▼            ▼             ▼
   Tailscale    ai-ops-runner   (future)    (future)
   + Docker       Job API
   + systemd     127.0.0.1:8000
```

## Lane Details

### Infrastructure Lane (ON by default)

**Trigger**: `openclaw_doctor.sh` — hourly via systemd timer.

Checks:
1. **Tailscale up**: `tailscale status` exits 0.
2. **Docker stack healthy**: `docker compose ps` shows all services running.
3. **API healthz**: `curl -sf http://127.0.0.1:8000/healthz` succeeds.
4. **No public ports**: `ss -tlnp` shows no unexpected bindings on `0.0.0.0`
   or `[::]`.  Fail-closed if any non-loopback binding is found outside the
   allowlist (currently empty — no public ports policy).

### ORB Analysis Lane (ON by default)

**Trigger**: `openclaw_nightly.sh` — daily at 02:00 local via systemd timer.

Jobs submitted via the runner API (127.0.0.1:8000):
- `orb_doctor` — repository health check
- `orb_review_bundle` — bounded-diff review bundle

All jobs are **read-only**: ephemeral worktree, chmod a-w, mutation detection,
worktree deleted after execution.

### Content Lane (OFF — future V2)

A generation/QC queue for content production:
1. **Generation**: AI-driven content creation (text, video scripts, thumbnails).
2. **QC**: Automated quality checks (grammar, tone, brand alignment).
3. **Approval**: Human-in-the-loop approval gate before publishing.
4. **Publishing**: Scheduled posting to configured platforms (SoraWorld, AI-ASMR, etc.).

V1 scaffolding includes project placeholders in `configs/openclaw/projects.yaml`
but no publishing logic.

### Browser Lane (OFF — future V3)

Headless browser automation for tasks like:
- Platform-specific analytics scraping (read-only)
- Content verification (published post validation)
- Interaction automation (likes, follows — disabled by default)

**Security gates**:
- Disabled by default; requires `browser_lane.enabled: true` in policies.
- Each task requires explicit approval in the project config.
- All browser sessions are recorded (screenshots + HAR).
- No credential auto-fill — each platform login is a separate, audited step.

## Security Model

### Network

- **No public ports.** All services bind to 127.0.0.1 or are Docker-internal.
- **Tailscale-only remote access.** SSH via Tailscale IP (100.x.x.x).
- **No inbound webhooks.** All triggers are timer-based (systemd) or manual.
- `openclaw_doctor.sh` actively verifies no public bindings exist.

### Secrets

- **Per-tenant isolation**: Secrets stored in `/etc/ai-ops-runner/tenants/<id>/secrets/`.
- **OpenAI API key**: Loaded via `ops/openai_key.py` (env → keyring → file).
  Never in process args, logs, or shell history.
- **SSH keys**: Mounted read-only in Docker containers (`/keys`).
- **Secret files**: Mode 600, owned by the service user.

### Jobs

- **Allowlisted commands only**: `configs/job_allowlist.yaml` defines exact argv.
- **Read-only worktrees**: `chmod -R a-w`, mutation detection post-execution.
- **Repo allowlist**: Only explicitly listed repositories can be targeted.
- **No git push**: Push URL set to `DISABLED` on bare mirrors.

## Artifacts

Every nightly run produces:
```
artifacts/openclaw/nightly/<YYYYMMDDTHHMMSSZ>/
├── summary.json          # Compact index: jobs, statuses, durations
├── doctor_result.txt     # Infrastructure check output
└── (job artifacts linked from runner artifact dirs)
```

Artifacts older than 14 days are pruned by the existing
`ai-ops-artifacts-prune.timer`.

## Systemd Units

| Unit                         | Type    | Schedule         | Description |
|------------------------------|---------|------------------|-------------|
| `openclaw-doctor.service`    | oneshot | (triggered)      | Health + audit checks |
| `openclaw-doctor.timer`      | timer   | Hourly           | Triggers doctor |
| `openclaw-nightly.service`   | oneshot | (triggered)      | Nightly ORB jobs + summary |
| `openclaw-nightly.timer`     | timer   | 02:00 local      | Triggers nightly |
