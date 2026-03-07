# Autonomy Max v1 — Master Plan

> Canonical phased plan for observe-only → execute autonomy. Reference: [AUTONOMY_V1_CONTRACTS.md](AUTONOMY_V1_CONTRACTS.md).

## Phases (0–8)

| Phase | Name | DoD |
|-------|------|-----|
| **0** | Bootstrap | `autonomy_mode.json` exists; defaults to `OFF`; scheduler ticks produce `tick_summary.json`, `decisions.jsonl`, `executed.jsonl`; `LATEST_*` pointers written. |
| **1** | Observe-only scheduler | Golden tick in observe-only: mutating candidates in `decisions.jsonl`; zero mutating entries in `executed.jsonl`. Read-only tasks may execute. |
| **2** | Inbox + approvals | UI default route `/` → `/inbox`; approval creation allowed; mutating playbooks produce `APPROVAL_REQUIRED`; approvals listable via API. |
| **3** | Proof bundles | Every playbook run writes proof bundle to `artifacts/system/playbook_runs/<run_id>/`; `evidence_bundle.json` available for triage. |
| **4** | Core vs optional canary | Core canary (required) and optional canary (informational) defined; `canary_core_status` and `canary_optional_status` in inbox summary. |
| **5** | Execute flip conditions | Integrator-approved conditions to flip `autonomy_mode` to `ON`: (a) N consecutive green core canaries, (b) no pending approvals, (c) explicit operator approval. |
| **6** | Execute mode | With `autonomy_mode=ON`, mutating tasks eligible to execute; still require approval for HUMAN_ONLY playbooks per policy. |
| **7** | Rollback | Single-command rollback to observe-only; `autonomy_mode` set to `OFF`; in-flight mutating runs gracefully halted where possible. |
| **8** | Audit trail | Nightly summary includes decisions, executed, approvals; audit log queryable. |

## Observe-Only Rules

1. Scheduler/autopilot ticks MUST treat `autonomy_mode=OFF` as observe-only by default.
2. Read-only tasks may execute.
3. Mutating tasks MUST appear in `decisions.jsonl`.
4. Mutating tasks MUST NOT appear in `executed.jsonl` while observe-only.
5. Approval creation is allowed (records intent, not external mutation).
6. Any exception requires integrator-approved contract revision.

## Conditions to Flip to Execute

All must hold:

- `canary_core_status == "PASS"` for N consecutive ticks (N configurable, default 3).
- No pending approvals for mutating playbooks.
- Explicit operator action: `POST /api/ui/autonomy_mode` with `{"mode":"ON"}` and valid `user_role`.

## Proof Bundle Requirements

- Path: `artifacts/system/playbook_runs/<playbook_run_id>/`
- Contents: `tick_summary.json` (or equivalent), `decisions.jsonl` tail, `evidence_bundle.json` (error_class, retryable, tail snippets ≤30 lines / 2 KB each).
- Proof bundle URL exposed in API responses for approvals and playbook runs.

## Rollback Strategy

1. `POST /api/ui/autonomy_mode` with `{"mode":"OFF"}` — immediate.
2. In-flight mutating runs: signal to stop; record `ROLLBACK_REQUESTED` in `executed.jsonl`; do not start new mutating runs.
3. After rollback, scheduler resumes observe-only behavior.

## Safety Constraints

- No `--no-verify` on git push.
- No CODEX_SKIP for real pushes.
- Fail-closed: if neither review engine works or fix violates hard constraints, stop and document.
- Leases: single-flight for doctor/deploy; join on 409.
