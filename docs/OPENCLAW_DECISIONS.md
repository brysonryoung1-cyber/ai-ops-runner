# OpenClaw Decision Log

Format: **What** | **Why** | **Date** | **Impact**

- **HQ UI design: Apple-glass (VisionOS-like)** | Consistent, beautiful, mobile-friendly control panel; no backend changes. | 2026-02-17 | Glass design system (CSS tokens, backdrop-filter, graceful fallback); GlassCard/Panel/Button, Pill, StatusDot, MetricTile; unified shell with top bar + responsive nav; dark-first frost look; admin controls hidden unless isAdmin.
- **Repo + HQ as project brain** | Eliminate reliance on ChatGPT chat memory; single source of truth in-repo. | 2026-02-16 | All goals/roadmap/decisions/current/next in `docs/`; state in `config/project_state.json`; doctor/ship fail if state missing/stale.
- **Fail-closed state files** | Prevent "lost context" — ship and doctor require OPENCLAW_CURRENT.md and OPENCLAW_NEXT.md present and (optionally) not stale. | 2026-02-16 | Doctor has "Project State Files" check; ship checks NEXT before review.
- **Machine-readable state snapshots** | Deploy and doctor write `artifacts/state/<timestamp>/state.json` for audit and HQ. | 2026-02-16 | GET /project/state returns latest snapshot metadata; no secrets in artifacts.
