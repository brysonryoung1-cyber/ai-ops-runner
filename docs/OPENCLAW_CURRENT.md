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

## Definition-of-Done (DoD)

- **DoD script**: `ops/dod_production.sh` — executable checks: hostd /health, /api/ai-status, /api/llm/status, /api/project/state, POST /api/exec action=doctor (PASS), /api/artifacts/list dirs > 0, no hard-fail strings (ENOENT, spawn ssh, Host Executor Unreachable).
- **Pipeline enforcement**: `ops/deploy_pipeline.sh` runs DoD at Step 5b (after verify_production); deploy fails if DoD exits non-zero. No bypass flags.
- **Proof artifacts**: `artifacts/dod/<run_id>/dod_result.json` (redacted; no secrets). Linked from deploy_result.artifacts.dod_result and served via GET `/api/dod/last`.
- **LiteLLM**: Optional proxy at 127.0.0.1:4000 (config/litellm.yaml); set `OPENCLAW_LITELLM_PROXY_URL=http://127.0.0.1:4000/v1` to route LLM via proxy. Cost guard and cost_guard.json remain; doctor is guard-exempt.
- **Action registry**: Single source `config/action_registry.json`; hostd and console use it (generated `action_registry.generated.ts`). Soma connector buttons call server-only `POST /api/projects/soma_kajabi/run`.
