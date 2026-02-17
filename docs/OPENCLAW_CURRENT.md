# OpenClaw — Current State

*(Updated by ship/deploy; repo + config/project_state.json are source of truth.)*

- **Project**: OpenClaw (ai-ops-runner)
- **Goal summary**: Self-updating project brain; repo + HQ canonical; no ChatGPT memory reliance.
- **Last verified VPS HEAD**: b59f432
- **Last deploy**: —
- **Last doctor**: FAIL
- **Last guard**: —
- **Zane phase**: 0
- **UI accepted**: False (at: —, commit: —)
- **LLM primary**: openai / gpt-4o-mini
- **LLM fallback**: mistral / labs-devstral-small-2512

## Recent changes (this ship)

- **Phase 1 (Soma baseline)**: Phase 0 runner permits inventory even when kill_switch=true; writes BASELINE_OK.json; updates project_state with phase0_baseline_status, phase0_baseline_artifact_dir, phase0_last_run_id on success.
- **Phase 2 (Soma-first gate)**: `gates.allow_orb_backtests` (default false); orb.backtest.* returns HTTP 423 (LANE_LOCKED_SOMA_FIRST) until baseline PASS + gate unlocked; hostd writes artifacts/backtests/blocked/<run_id>/; HQ actions page shows lock banner and baseline artifact link.
- **Phase 3 (Cost tracking)**: Per-request usage → artifacts/cost/usage.jsonl; GET /api/costs/summary, /api/costs/timeseries; config/cost_guard.json (hourly_usd_limit, daily_usd_limit); cost guard blocks non-essential LLM with COST_GUARD_TRIPPED; HQ dashboard cost tile (today, MTD, 7d, top project).
- **Phase 4 (Bulk backtest)**: orb.backtest.bulk Tier 1 script writes SUMMARY.md on success; execution remains locked by Soma-first gate.
- **Phase 5 (NT8 stub)**: orb.backtest.confirm_nt8 writes tier2/summary.json with error_class NT8_EXECUTOR_NOT_CONFIGURED; exit 3.

## Definition-of-Done (DoD)

- **DoD script**: `ops/dod_production.sh` — executable checks: hostd /health, /api/ai-status, /api/llm/status, /api/project/state, POST /api/exec action=doctor (PASS), /api/artifacts/list dirs > 0, no hard-fail strings (ENOENT, spawn ssh, Host Executor Unreachable).
- **Pipeline enforcement**: `ops/deploy_pipeline.sh` runs DoD at Step 5b; deploy fails if DoD exits non-zero.
- **Proof artifacts**: `artifacts/dod/<run_id>/dod_result.json`. Served via GET `/api/dod/last`.
- **Production deploy**: `ops/deploy_until_green.sh` — retries until green; fail-closed on build/route issues.
- **Single-flight + join**: `/api/exec` returns 409 with `active_run_id` when action already running.
- **Maintenance mode**: Deploy pipeline sets maintenance mode; DoD doctor allowed via `x-openclaw-dod-run`.
