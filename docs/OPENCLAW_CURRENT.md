# OpenClaw — Current State

*(Auto-updated by ops/update_project_state.py from config/project_state.json.)*

- **Project**: OpenClaw (ai-ops-runner)
- **Goal summary**: Self-updating project brain; repo + HQ canonical; no ChatGPT memory reliance.
- **Last verified VPS HEAD**: bce28cc (post-deploy)
- **Last deploy**: deploy_until_green on aiops-1
- **Last doctor**: PASS (DoD PASS)
- **Last guard**: PASS
- **Zane phase**: 0
- **Connectors**: Kajabi (storage_state bootstrap); Gmail (OAuth device flow or IMAP fallback). Phase 0 fails with CONNECTOR_NOT_CONFIGURED until both are ready. **pred_markets**: Phase 0 read-only mirror (Kalshi + Polymarket public APIs); no trading; kill_switch default ON.
- **UI accepted**: False (at: —, commit: —)
- **LLM primary**: openai / gpt-4o-mini
- **LLM fallback**: mistral / labs-devstral-small-2512

## Autopilot Deploy

- **Purpose**: Automatically deploy latest approved `origin/main` to aiops-1, verify, and roll back on failure.
- **Tick script**: `ops/autopilot_tick.sh` — fetches origin, compares SHA, runs `deploy_pipeline.sh`, verifies, rolls back to last-known-good on failure.
- **Systemd timer**: `openclaw-autopilot.timer` (every 5 min, configurable). Service: `openclaw-autopilot.service`.
- **State directory**: `/var/lib/ai-ops-runner/autopilot/` with `last_deployed_sha.txt`, `last_good_sha.txt`, `fail_count.txt`, `enabled` (sentinel), `last_run.json`, `autopilot.lock`.
- **Concurrency**: `flock` on `autopilot.lock`. Fail-closed on lock contention.
- **Backoff**: After 3 consecutive failures, waits 30 min before retrying.
- **Rollback**: On deploy failure, automatically redeploys `last_good_sha`. If rollback also fails, emits alert and stops.
- **Install**: `sudo ./ops/openclaw_install_autopilot.sh` or HQ Settings → Autopilot → Install button.
- **HQ UI**: Settings → Autopilot Deploy panel. Shows status, SHA, fail count. Buttons: Enable, Disable, Run Now.
- **API endpoints**:
  - `GET /api/autopilot/status` (no token required) — installed, enabled, last_deployed_sha, last_good_sha, fail_count, last_run, last_error.
  - `POST /api/autopilot/enable` (admin token) — creates `enabled` sentinel.
  - `POST /api/autopilot/disable` (admin token) — removes `enabled` sentinel.
  - `POST /api/autopilot/run_now` (admin token) — triggers immediate tick via hostd.
- **Hostd actions**: `autopilot_status`, `autopilot_enable`, `autopilot_disable`, `autopilot_run_now`, `autopilot_install` (all allowlisted in `config/action_registry.json`).
- **Safety**: No SSH. No git push. Tailscale-only. Fail-closed. ORB gates unchanged.

## Error Artifacts

- **error.json**: Every action run that fails now produces a structured `error.json` in its artifact directory.
- **Fields**: `error_class`, `reason`, `recommended_next_action`, `timestamp_utc`, `underlying_exception` (if safe).
- **Coverage**: hostd actions (timeout, nonzero exit, exception) and test_runner jobs (timeout, nonzero exit, exception, mutation detection).
- **No blank failures**: Exit code 1 with no explanation is no longer possible.

## Auth & Diagnostics

- **`/api/auth/status`** (GET, no token required): Self-diagnosing auth endpoint. Returns `hq_token_required`, `admin_token_loaded`, `host_executor_reachable`, `build_sha`, `trust_tailscale`, `notes[]`. No secrets leaked.
- **`/api/ui/health_public`** (GET, no token required): Public-safe health endpoint for monitoring. Returns `build_sha`, route map, artifacts readable check. No secrets.
- **Tailscale-trusted mode**: Set `OPENCLAW_TRUST_TAILSCALE=1` (default ON in docker-compose.console.yml) to bypass HQ token gate for browser→HQ requests. Tailnet membership is the access control. Admin-token requirement for host executor admin actions is still enforced server-side.
- **403 Forbidden UX**: When a 403 occurs, the UI shows a deterministic banner explaining the cause (token missing, admin token not loaded, or CSRF origin blocked) with a one-click "Generate support bundle" button. No screenshots needed for debugging.
- **Support Bundle** (`POST /api/support/bundle`): Generates `auth_status.json`, `ui_health.json`, `last_10_runs.json`, `last_forbidden.json`, `dod_last.json`, failing runs, docker status, guard/hostd journals. All redacted. Stored in `artifacts/support_bundle/<run_id>/`.
- **Build SHA**: Visible in sidebar footer and Settings auth panel. `deploy_pipeline.sh` now exports `OPENCLAW_BUILD_SHA` to docker compose and writes `deploy_receipt.json` so `/api/ui/health_public` reports the actual deployed SHA (not "unknown").

## Definition-of-Done (DoD)

- **DoD script**: `ops/dod_production.sh` — executable checks: hostd /health, /api/ai-status, /api/llm/status, /api/project/state, POST /api/exec action=doctor (PASS), /api/artifacts/list dirs > 0, no hard-fail strings (ENOENT, spawn ssh, Host Executor Unreachable).
- **Pipeline enforcement**: `ops/deploy_pipeline.sh` runs DoD at Step 5b (after verify_production); deploy fails if DoD exits non-zero. No bypass flags.
- **Proof artifacts**: `artifacts/dod/<run_id>/dod_result.json` (redacted; no secrets). Linked from deploy_result.artifacts.dod_result and served via GET `/api/dod/last`.
- **LiteLLM**: Optional proxy at 127.0.0.1:4000 (config/litellm.yaml); set `OPENCLAW_LITELLM_PROXY_URL=http://127.0.0.1:4000/v1` to route LLM via proxy. Cost guard and cost_guard.json remain; doctor is guard-exempt.
- **Action registry**: Single source `config/action_registry.json`; hostd and console use it (generated `action_registry.generated.ts`). Soma connector buttons call server-only `POST /api/projects/soma_kajabi/run`.
