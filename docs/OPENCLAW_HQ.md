# OpenClaw HQ

> Single control panel for all OpenClaw projects, runs, AI providers, and infrastructure health.

## Overview

OpenClaw HQ replaces the original "OpenClaw Console" with a unified control plane that tracks:

- **All projects** — status, last runs, schedules, artifacts, and connected AI providers
- **All runs** — timeline across projects with drill-down to logs and artifacts
- **AI connections** — provider status, review engine mode, masked key fingerprints
- **Infrastructure** — doctor, ports, guard, Docker, guard logs (preserved from Console)

## Architecture

```
config/
├── projects.json           # Project registry (source of truth)
└── projects.schema.json    # JSON Schema for validation

apps/openclaw-console/src/
├── lib/
│   ├── projects.ts         # Registry loader + fail-closed validator
│   └── run-recorder.ts     # Unified run recorder (write/read/list)
├── app/
│   ├── page.tsx            # Overview + AI Connections panel
│   ├── projects/page.tsx   # Project cards with status/last_run/schedule
│   ├── runs/page.tsx       # Run timeline with detail panel
│   ├── api/
│   │   ├── projects/route.ts  # GET /api/projects (registry + last runs)
│   │   ├── runs/route.ts      # GET /api/runs (list/detail)
│   │   └── ai-status/route.ts # GET /api/ai-status (providers + review engine)
│   └── (existing pages preserved)
├── components/
│   └── Sidebar.tsx         # Updated: 6 nav items, "HQ" branding
└── middleware.ts           # Unchanged: token auth preserved
```

## Project Registry

### Schema

The project registry lives at `config/projects.json` and is validated at load time (fail-closed).

Each project has:

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique ID (lowercase, underscores, 2-64 chars) |
| `name` | string | Human-readable name |
| `description` | string | Brief description |
| `enabled` | boolean | Whether workflows are active |
| `workflows` | string[] | Action/workflow names this project runs |
| `schedules` | Schedule[] | Cron-based schedules |
| `notification_flags` | NotifFlags | Alert configuration |
| `tags` | string[] | Freeform tags for filtering |

### Registered Projects

| ID | Name | Status |
|----|------|--------|
| `infra_openclaw` | OpenClaw Infrastructure | Active (production) |
| `soma_kajabi_library_ownership` | Soma Kajabi Library Ownership | Disabled (until wired) |
| `clip_factory_monitoring` | Clip Factory Monitoring | Placeholder |
| `music_pipeline` | Music Pipeline | Placeholder |

### Adding a Project

Edit `config/projects.json` and add an entry matching the schema. The validator will reject invalid entries at load time (fail-closed). Commit and deploy.

## Run Recorder

Every action executed through the console API writes a run record:

```
artifacts/runs/<run_id>/run.json
```

### Run Record Schema

```json
{
  "run_id": "20260216-143025-a1b2",
  "project_id": "infra_openclaw",
  "action": "doctor",
  "started_at": "2026-02-16T14:30:25.000Z",
  "finished_at": "2026-02-16T14:30:28.500Z",
  "status": "success",
  "exit_code": 0,
  "duration_ms": 3500,
  "error_summary": null,
  "artifact_paths": []
}
```

### Fail-Closed

Run records are written on **both success and failure paths**. If the recorder itself fails, the error is logged but the action result is still returned to the caller (no silent data loss).

## AI Connections Panel

The Overview page includes an AI Connections panel showing:

1. **Provider status** — OpenAI (active/inactive/unknown)
2. **Masked fingerprint** — e.g., `sk-…abcd` (NEVER raw keys)
3. **Review engine** — mode (codex-review), last review time, gate status (fail-closed)

### Security

- Raw API keys are NEVER exposed in the UI or API responses
- Only masked fingerprints (prefix + last 4 chars) are shown
- The `maskKey()` function strips everything except `sk-…XXXX`

## Sidebar Navigation

| Section | Path | Description |
|---------|------|-------------|
| Overview | `/` | System health + AI connections |
| Projects | `/projects` | Project registry with status cards |
| Runs | `/runs` | Timeline of all runs |
| Logs | `/logs` | Guard journal viewer |
| Artifacts | `/artifacts` | Artifact directory listing |
| Actions | `/actions` | Execute allowlisted operations |

## API Endpoints

All endpoints require `X-OpenClaw-Token` header (when configured).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/projects` | List projects with last-run status |
| GET | `/api/runs` | List runs (supports `?limit=N`, `?id=RUN_ID`) |
| GET | `/api/ai-status` | AI provider + review engine status |
| POST | `/api/exec` | Execute allowlisted action (unchanged) |
| GET | `/api/exec?check=connectivity` | SSH connectivity check (unchanged) |

## Security Constraints (Preserved)

All original security constraints are maintained:

1. **Tailnet-only** — Console binds to 127.0.0.1:8787, exposed via Tailscale Serve
2. **Token auth** — `X-OpenClaw-Token` required for all `/api/*` routes
3. **Allowlist-only** — No arbitrary command execution; strict allowlist preserved
4. **No secret leakage** — Keys masked; AI status shows fingerprints only
5. **CSRF protection** — Origin validation on all API routes
6. **Audit logging** — Every action logged to `data/audit.jsonl`
7. **Action lock** — Prevents overlapping execution
8. **Fail-closed** — Registry validation, run recording, and review gate all fail-closed

## Self-Tests

```bash
# Run all HQ self-tests (35 assertions)
bash ops/tests/openclaw_hq_selftest.sh
```

Tests cover:
- Project registry schema validation (13 tests)
- Run recorder structure and wiring (7 tests)
- API route existence and security (5 tests)
- UI page structure (7 tests)
- Security invariants (3 tests)

## Deploy

```bash
# On LOCAL: build, push, deploy
./ops/openclaw_vps_deploy.sh

# On aiops-1: pull and restart console
cd /opt/ai-ops-runner && git pull --ff-only && ./ops/openclaw_console_build.sh && ./ops/openclaw_console_start.sh
```

## Access

```
https://aiops-1.tailc75c62.ts.net
```

Tailscale-only. Phone-accessible.
