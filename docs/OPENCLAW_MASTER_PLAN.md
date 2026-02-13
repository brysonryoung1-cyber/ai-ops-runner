# OpenClaw V1 — Master Plan

> **Status**: Scaffolding (V1) — lives inside `ai-ops-runner` until it earns its own repo.

## Vision

OpenClaw is an **orchestrator layer** on top of `ai-ops-runner`.  It coordinates
multiple autonomous "lanes" — infrastructure health, ORB analysis, content
generation, and browser automation — into a single, nightly-auditable pipeline.

### Design Principles

1. **No public ports.**  All traffic is Tailscale-only or loopback (127.0.0.1).
2. **Fail-closed.**  Every step must succeed or the pipeline halts with clear diagnostics.
3. **Read-only by default.**  ORB analysis and infrastructure checks never mutate repos or deploy artifacts.
4. **Secrets boundary.**  Per-tenant secrets are isolated.  No tenant can read another's credentials.
5. **Audit trail.**  Every nightly run produces a compact `summary.json` under `artifacts/openclaw/nightly/<timestamp>/`.

## Lanes

| Lane            | Default | Description |
|-----------------|---------|-------------|
| Infrastructure  | ON      | Health checks for aiops-1: Tailscale, Docker stack, API healthz, public-port audit. |
| ORB Analysis    | ON      | Read-only `orb_doctor` + `orb_review_bundle` jobs via the runner API. |
| Content         | OFF     | Generation queue + QC pipeline (publishing gated; not yet implemented). |
| Browser         | OFF     | Headless browser tasks; requires explicit enable + human approval gates. |

## Milestones

| Milestone | Target | Description |
|-----------|--------|-------------|
| V1        | Now    | Scaffolding: doctor, nightly driver, systemd timers, project registry. |
| V2        | TBD    | Content lane MVP: generate → QC → manual-approve → queue for publishing. |
| V3        | TBD    | Browser lane MVP: headless Chromium tasks with per-step approval gates. |
| V4        | TBD    | Multi-tenant: isolated config/secrets/projects per tenant; SaaS-ready API. |

## Multi-Tenant Notes ("Sell to Others")

OpenClaw is designed from V1 with multi-tenancy in mind:

- **Per-tenant secrets boundary**: Each tenant's API keys, SSH keys, and credentials
  are stored in isolated directories (`/etc/ai-ops-runner/tenants/<tenant_id>/secrets/`).
  No tenant can access another's secrets.
- **Per-tenant project registry**: Each tenant has its own `projects.yaml` scoped to
  their allowed repos, lanes, and rate limits.
- **Policy enforcement**: Global policies in `configs/openclaw/policies.yaml` set
  allowlists, rate limits, and hard rules (e.g., "no-public-ports") that apply to
  all tenants.  Tenant-level policies can only be more restrictive, never less.
- **Billing hooks**: Each nightly summary includes resource usage metrics suitable
  for metering (job count, duration, artifact size).

## Repository Layout (V1)

```
ai-ops-runner/
├── configs/openclaw/
│   ├── projects.yaml       # Project/lane registry
│   └── policies.yaml       # Allowlists, rate limits, hard rules
├── docs/
│   ├── OPENCLAW_MASTER_PLAN.md       # This file
│   ├── OPENCLAW_ARCHITECTURE.md      # Technical architecture
│   └── OPENCLAW_PROJECTS.md          # Project descriptions
├── ops/
│   ├── openclaw_doctor.sh            # Health/audit checks
│   ├── openclaw_nightly.sh           # Nightly driver
│   └── systemd/
│       ├── openclaw-doctor.service
│       ├── openclaw-doctor.timer     # Hourly
│       ├── openclaw-nightly.service
│       └── openclaw-nightly.timer    # 02:00 local
└── artifacts/openclaw/nightly/       # Generated at runtime (gitignored)
```
