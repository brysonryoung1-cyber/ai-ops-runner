# OpenClaw Goals

- **What we're building**: Self-updating AI-ops control plane (HQ + runner) with repo + HQ as the canonical project brain; no reliance on ChatGPT chat memory.
- **Success criteria**:
  - Goals, roadmap, decisions, current state, and next action live in-repo and are updated by ship/deploy.
  - Every deploy/run writes a machine-readable state snapshot to artifacts; HQ surfaces it.
  - Doctor/ship fail if state files are missing or stale (fail-closed against lost context).
- **Soma-first policy (TOP PRIORITY, HARD RULE)**: No ORB backtest lane execution until Soma/Zane Phase 0 baseline is complete and marked stable. Enforced code-level: `gates.allow_orb_backtests` must be true and `projects.soma_kajabi.phase0_baseline_status` must be PASS. Otherwise `orb.backtest.*` actions return HTTP 423 (LANE_LOCKED_SOMA_FIRST).
- **Zane agent**: Phases 0 (read-only), 1 (apply with gate), 2 (scheduled cadence + alerts); see `docs/OPENCLAW_ROADMAP.md`.
