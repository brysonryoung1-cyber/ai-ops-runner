# OpenClaw — Current State

*(Auto-updated by ops/update_project_state.py from config/project_state.json.)*

- **Project**: OpenClaw (ai-ops-runner)
- **Goal summary**: Self-updating project brain; repo + HQ canonical; no ChatGPT memory reliance.
- **Architecture**: HQ (console) binds to 127.0.0.1 only. **No SSH**. Host Executor (hostd) on 127.0.0.1:8877 runs allowlisted actions; console calls hostd via `OPENCLAW_HOSTD_URL` (e.g. `http://host.docker.internal:8877`). Artifacts from read-only mount. Doctor checks hostd reachability.
- **HQ UI (Glass)**: Apple-glass / VisionOS-like control panel. Overview, Runs, Projects, Artifacts (mount-based list), Actions (via hostd), Settings. Admin-only Deploy+Verify; 503 if admin not configured.
- **Last verified VPS HEAD**: 3df15eb
- **Last deploy**: —
- **Last doctor**: FAIL
- **Last guard**: —
- **Zane phase**: 0
- **LLM primary**: openai / gpt-4o-mini
- **LLM fallback**: mistral / labs-devstral-small-2512
