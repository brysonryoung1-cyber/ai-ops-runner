# OpenClaw Security Contract

This document defines the **non-negotiable** security posture for the OpenClaw control plane. All code, automation, and operational changes MUST comply. Violations are treated as regressions and will be caught by Doctor and Guard.

## 1. Private-Only Networking (Fail-Closed)

### Allowed Listener CIDRs

| CIDR | Description | Enforcement |
|------|-------------|-------------|
| `127.0.0.0/8` | IPv4 loopback | Doctor check 4 |
| `::1` | IPv6 loopback | Doctor check 4 |
| `100.64.0.0/10` | Tailscale CGNAT range | Doctor check 4 |

### Prohibited

- **NO** listeners on `0.0.0.0` or `::` (wildcard binds)
- **NO** public dashboards or admin panels
- **NO** inbound webhooks
- **NO** permissive CORS headers

### Enforcement

- `openclaw_doctor.sh` — tailnet-aware port audit (check 4)
- `openclaw_guard.sh` — 10-minute regression loop with auto-remediation
- Docker Compose — no published ports except `127.0.0.1:8000`
- UFW on VPS — deny all incoming except Tailscale interface + WireGuard UDP

## 2. Console Constraints

### Allowlist-Only Execution

- The console executes ONLY commands registered in `src/lib/allowlist.ts`
- **NO** arbitrary shell execution
- **NO** user-provided arguments beyond strict schema
- Unknown action names → HTTP 400 (fail-closed)

### Minimal Auth Gate (Even on Localhost)

- Token-based auth: `X-OpenClaw-Token` header required on all `/api/*` routes
- Token loaded from macOS Keychain or env (NEVER hardcoded)
- Short session TTL: tokens should be rotated periodically
- CSRF protection: origin validation on all POST/mutating requests
- Security events logged as single-line entries (no secrets)

### Action Lock

- Overlapping execution of the same action is prevented unless explicitly allowed
- Each action execution produces a durable audit log entry

## 3. Key Handling Contract

### Resolution Order (Deterministic, Non-Interactive)

1. **Environment variable** — if set, use immediately
2. **macOS Keychain** — via Python keyring (no secret in argv)
3. **Linux file** — `/etc/ai-ops-runner/secrets/<key_name>` (chmod 600)

### Invariants

- Keys are **NEVER** printed to human-visible output
- Keys are **NEVER** committed to git
- Keys are **NEVER** passed via process argv
- Keys are **NEVER** logged to stderr or stdout
- `status` commands show masked fingerprint only (e.g., `sk-…abcd`)
- `doctor` verifies presence + last-success timestamp without exposing the key
- **NO** interactive prompts anywhere in runtime/ops paths
- Fail-closed: if a key is unavailable, the pipeline stops with clear instructions

### Managed Secrets

| Key | Service | Account |
|-----|---------|---------|
| `OPENAI_API_KEY` | `ai-ops-runner` | `OPENAI_API_KEY` |
| `OPENCLAW_CONSOLE_TOKEN` | `ai-ops-runner` | `OPENCLAW_CONSOLE_TOKEN` |
| `PUSHOVER_APP_TOKEN` | `ai-ops-runner` | `PUSHOVER_APP_TOKEN` |
| `PUSHOVER_USER_KEY` | `ai-ops-runner` | `PUSHOVER_USER_KEY` |

## 4. Automation-First

- Every recurring action MUST become a script in `ops/`
- Every script MUST include a hermetic self-test (mocked; no network; no real secrets)
- Self-tests validate structure, safety guards, and fail-closed behavior
- CI/dev runs MUST succeed without network access

## 5. Single Source of Truth

- `docs/HANDOFF_CURRENT_STATE.md` — canonical system state
- `docs/OPS_INDEX.md` — canonical ops command index
- Both MUST be updated on every change
- All other docs reference these two as authoritative

## 6. Fail-Closed Posture

- **Doctor**: any check failure → exit 1 (never silently pass)
- **Guard**: Tailscale down → NEVER touches sshd (lockout prevention)
- **Console**: unknown action → reject; missing token → 401; bad origin → 403
- **Runner**: dirty worktree → MUTATION_DETECTED failure
- **Review gate**: simulated verdicts → NEVER valid for push gate
- **VPS updates**: review-gated; fail-closed if code not APPROVED
- **Port audit**: any non-allowed bind → FAIL with remediation guidance

## 7. Notification Security

- Outbound-only: Pushover API calls (HTTPS POST to api.pushover.net)
- **NO** inbound webhooks or callback URLs
- Rate-limited: one alert per check_id per 30 minutes
- Secrets loaded via key handling contract (never printed)

## 8. Audit Trail

- Console actions → append-only audit log with timestamp, actor, action, params_hash, exit_code, duration
- Doctor runs → JSON artifacts in `artifacts/doctor/<timestamp>/`
- Guard runs → timestamped log at `/var/log/openclaw_guard.log`
- Evidence bundles → `artifacts/evidence/<timestamp>_<host>/`
- Review verdicts → `review_packets/<timestamp>/CODEX_VERDICT.json`
