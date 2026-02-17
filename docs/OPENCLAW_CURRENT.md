# OpenClaw — Current State

*(Auto-updated by ops/update_project_state.py from config/project_state.json.)*

- **Project**: OpenClaw (ai-ops-runner)
- **Goal summary**: Self-updating project brain; repo + HQ canonical; no ChatGPT memory reliance.
- **Last verified VPS HEAD**: 3be7e2f
- **Last deploy**: —
- **Last doctor**: FAIL
- **Last guard**: —
- **Zane phase**: 0
- **UI accepted**: False (at: —, commit: —)
- **LLM primary**: openai / gpt-4o-mini
- **LLM fallback**: mistral / labs-devstral-small-2512

## Definition-of-Done (DoD)

- **DoD script**: `ops/dod_production.sh` — executable checks: hostd /health, /api/ai-status, /api/llm/status, /api/project/state, POST /api/exec action=doctor (PASS), /api/artifacts/list dirs > 0, no hard-fail strings (ENOENT, spawn ssh, Host Executor Unreachable).
- **Pipeline enforcement**: `ops/deploy_pipeline.sh` runs DoD at Step 5b (after verify_production); deploy fails if DoD exits non-zero. No bypass flags.
- **Proof artifacts**: `artifacts/dod/<run_id>/dod_result.json` (redacted; no secrets). Linked from deploy_result.artifacts.dod_result and served via GET `/api/dod/last`.

## soma_kajabi Phase 0 Project

- **Project**: soma_kajabi (Phase 0 read-only)
- **Artifacts**: `artifacts/soma_kajabi/phase0/<run_id>/`
  - `kajabi_library_snapshot.json` — Home + Practitioner structure (or unknown schema on fail-closed)
  - `gmail_harvest.jsonl` — Emails from:(Zane McCourtney) has:attachment
  - `video_manifest.csv` — email_id, subject, file_name, status (unmapped/mapped_to_existing_lesson/raw_needs_review)
  - `result.json` — ok, error_class, recommended_next_action, artifact_paths
- **Kill switch**: `projects.soma_kajabi.kill_switch` in config/project_state.json (default true)
- **Enable**: Set `kill_switch` to false in config/project_state.json
