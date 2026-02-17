# OpenClaw UI Acceptance

Repo-canonical checklist for approving the HQ/Console UI before starting Zane Phase work.  
**Proceed to Zane Phase 0 only after UI is explicitly accepted and reflected in `config/project_state.json` and `/api/project/state`.**

## Checklist

- [ ] **Overview** — Control Center loads; Doctor, Guard, LLM status visible; Project Brain shows canonical state.
- [ ] **Projects** — Project list and detail (if applicable) work as expected.
- [ ] **Runs** — Runs list and artifact links work.
- [ ] **Artifacts** — Artifact browsing and hostd-backed actions (e.g. doctor, deploy) work.
- [ ] **Actions** — Allowlisted Host Executor actions execute and show output.
- [ ] **Settings** — Settings/configuration (if any) are correct.
- [ ] **Admin gating** — Admin-only actions (e.g. deploy) are gated; no secrets in UI or logs.

## Acceptance record

| Field | Value |
|-------|--------|
| **Accepted** | `true` / `false` |
| **Accepted_at** | ISO8601 timestamp when checklist was signed off |
| **Accepted_commit** | Git short SHA of repo at acceptance |

When accepted, update `config/project_state.json` with:

- `ui_accepted`: `true`
- `ui_accepted_at`: e.g. `2026-02-17T12:00:00Z`
- `ui_accepted_commit`: e.g. `a1b2c3d`

Doctor will **FAIL** if `ui_accepted` is not `true` and `docs/OPENCLAW_NEXT.md` points to any Zane Phase action (Phase 0/1/2).
