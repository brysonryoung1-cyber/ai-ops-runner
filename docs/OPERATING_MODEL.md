# Operating Model

## Definition of Done (DoD)

A change is **DONE** only if all of the following are true:

1. **Tests pass** — pytest and ops selftests (no skips for convenience).
2. **Gated review APPROVED** — real Codex review verdict; simulated verdicts (e.g. CODEX_SKIP) never satisfy the gate.
3. **Pushed to origin/main** — normal `git push`; `--no-verify` is forbidden.
4. **Deployed to aiops-1** — via **Deploy+Verify** (pull-only pipeline): `ops/deploy_pipeline.sh` on aiops-1 (sync, build, verify, update state).
5. **Post-deploy verification passes** — doctor/guard PASS; key endpoints return `ok: true`; no public ports; `last_deploy_timestamp` and state fields set.
6. **Proof bundle written** — `artifacts/ship/<run_id>/` (from ship-capable host) and `artifacts/deploy/<run_id>/` (from aiops-1); visible in HQ.

Any run that does not reach DoD must be **FAIL/blocked** with a machine-readable reason (e.g. `ship_result.json` with `step_failed`, `error_class`, `next_auto_fix`).

## CSR: No Handoff of Commands

The CSR (Customer Success Representative) / Opus implementer operating model is **autonomous**. The agent:

- **Must not** hand off steps or commands to the user. All steps (tests, review, push, deploy, verify) run in pipeline automation.
- **Must** use **Deploy+Verify** from HQ when deploying to aiops-1 (runs `ops/deploy_pipeline.sh` on the host). **Ship** (tests → review → push) runs only on a non-production, push-capable host via `ops/ship_pipeline.sh`.
- **Must** produce proof artifacts automatically; no manual “run this then that” instructions.

If the pipeline fails, the agent fixes blockers (e.g. tests, review feedback) and re-runs the pipeline until DoD is met or the failure is documented in `ship_result.json`.

## Single Next Action

The project maintains a single next action in `docs/OPENCLAW_NEXT.md`. It remains the one canonical “what we do next” and is updated as part of the operating workflow (e.g. after a ship or during planning).

## References

- **Ship pipeline** (SHIP-only, not on aiops-1): `ops/ship_pipeline.sh` — tests → review_auto → check_fail_closed_push → review_finish (push). Host guard refuses production. No CODEX_SKIP, no `git push --no-verify`.
- **Deploy pipeline** (aiops-1 only, pull+run): `ops/deploy_pipeline.sh` — assert_production_pull_only → git fetch/reset → **explicit console build** (fail-closed) → docker compose → verify_production → **console route gate** (/api/dod/last must exist) → dod_production → update_project_state. No git push. No `|| true` bypass on console build/compose.
- **Deploy-until-green** (production deploy entrypoint): `ops/deploy_until_green.sh` — the **only supported production deploy command** on aiops-1. Runs deploy_pipeline + green_check in a retry loop; safe remediations only; fail-closed on build/route/typecheck. Writes triage.json on fail-closed. **Retry policy**: DoD failures caused by joinable 409 (doctor already running, `ALREADY_RUNNING` / `doctor_exec=409_*`) are classified as `dod_failed_joinable_409` and are **retried** (wait/backoff + re-run deploy). Non-joinable `dod_failed` and all other fail-closed classes exit immediately. `deploy_pipeline.sh` is internal; use `deploy_until_green.sh` for production.
- **Single-flight + join (doctor_exec)**: POST `/api/exec` with `action=doctor` may return HTTP 409 when doctor is already running. The response is **joinable**: JSON includes `error_class: "ALREADY_RUNNING"`, `action`, `active_run_id`, and `started_at`. Callers must **join** by polling GET `/api/runs?id=<active_run_id>` until the run completes, then treat that result as the doctor result. Do not issue a fresh POST until the joined run has completed (and only then, if it failed, at most one rerun is allowed). This eliminates doctor_exec collisions during deploy.
- **Maintenance mode**: During deploy, `deploy_pipeline.sh` sets maintenance mode (writes `artifacts/.maintenance_mode` with `deploy_run_id`) and stops `openclaw-doctor.timer` so no background doctor runs. Only the DoD step’s doctor request is allowed (via header `x-openclaw-dod-run: <deploy_run_id>`). On deploy success, maintenance mode is cleared and the timer is started. On deploy failure, maintenance mode is left ON (safest).
- **Verification**: `ops/verify_production.sh` — endpoints (with retries) + doctor/guard PASS + state non-null; no public ports.
- **HQ**: Binds to 127.0.0.1 only. Actions run via **Host Executor (hostd)** on the host (127.0.0.1:8877). No SSH dependency. Deploy+Verify button (admin-only when `OPENCLAW_ADMIN_TOKEN` is set; 503 if unset). Artifacts listing from read-only mount (`/var/openclaw_artifacts`). Last deploy result and artifact path on Overview.
- **Host Executor (hostd)**: `ops/openclaw_hostd.py` — allowlisted actions (doctor, deploy_and_verify, port_audit, tail_guard_log, etc.). Auth via `X-OpenClaw-Admin-Token` from `/etc/ai-ops-runner/secrets/openclaw_admin_token`. Fail-closed: token missing → 503; token mismatch → 403. Installed by `ops/install_openclaw_hostd.sh` (idempotent); deploy pipeline runs it early.
- **Failure modes**: hostd down → HQ connectivity check fails; doctor fails hostd check. No public ports; Tailscale/localhost only.

## Connector bootstrap (no secrets in logs)

- **Kajabi**: HQ Soma → Connectors card → **Bootstrap Kajabi** (start → owner completes login/2FA in browser and saves Playwright storage_state; copy file to `/etc/ai-ops-runner/secrets/soma_kajabi/kajabi_storage_state.json`) → finalize. Perms 0640, owner 1000:1000. Config: `kajabi.mode=storage_state`, `storage_state_secret_ref` points to that path.
- **Gmail**: HQ Soma → **Connect Gmail** (start → owner opens verification_url, enters user_code; then finalize). OAuth token written to `/etc/ai-ops-runner/secrets/soma_kajabi/gmail_oauth.json` (0640). Requires `gmail_client.json` with client_id/client_secret (Google OAuth Desktop/Limited Input Device app). Alternative: `gmail.mode=imap` with GMAIL_USER + GMAIL_APP_PASSWORD in secrets (no token logging).
- Phase 0 remains CONNECTOR_NOT_CONFIGURED until both connectors report ready.
