# OpenClaw Goals

- **What we're building**: Self-updating AI-ops control plane (HQ + runner) with repo + HQ as the canonical project brain; no reliance on ChatGPT chat memory.
- **Success criteria**:
  - Goals, roadmap, decisions, current state, and next action live in-repo and are updated by ship/deploy.
  - Every deploy/run writes a machine-readable state snapshot to artifacts; HQ surfaces it.
  - Doctor/ship fail if state files are missing or stale (fail-closed against lost context).
- **Zane agent**: Phases 0 (read-only), 1 (apply with gate), 2 (scheduled cadence + alerts); see `docs/OPENCLAW_ROADMAP.md`.
