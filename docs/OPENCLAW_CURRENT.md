# OpenClaw — Current State

*(Auto-updated by ops/update_project_state.py from config/project_state.json.)*

- **Project**: OpenClaw (ai-ops-runner)
- **Goal summary**: Self-updating project brain; repo + HQ canonical; no ChatGPT memory reliance.
- **Last verified VPS HEAD**: b64b7aa
- **Last deploy**: —
- **Last doctor**: FAIL
- **Last guard**: —
- **Zane phase**: 0
- **UI accepted**: False (at: —, commit: —)
- **LLM primary**: openai / gpt-4o-mini
- **LLM fallback**: mistral / labs-devstral-small-2512

## Definition-of-Done (DoD)

- **DoD script**: `ops/dod_production.sh` — executable checks: hostd /health, /api/ai-status, /api/llm/status, /api/project/state, POST /api/exec action=doctor (PASS), /api/artifacts/list dirs > 0, no hard-fail strings (ENOENT, spawn ssh, Host Executor Unreachable).
- **Pipeline enforcement**: `ops/deploy_pipeline.sh` runs DoD at Step 5b (after verify_production and **console route gate**); deploy fails if DoD exits non-zero. No bypass flags. **Bug fix (2025-02)**: Removed `|| true` that allowed console build to fail silently; added explicit console build step and /api/dod/last route gate; deploy now fail-closed on TS/build/route issues.
- **Proof artifacts**: `artifacts/dod/<run_id>/dod_result.json` (redacted; no secrets). Linked from deploy_result.artifacts.dod_result and served via GET `/api/dod/last`.
- **Production deploy entrypoint**: `ops/deploy_until_green.sh` — retries safe remediations until green; fail-closed on build/route issues with triage.json.
