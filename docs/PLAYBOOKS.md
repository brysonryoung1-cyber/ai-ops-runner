# Playbooks — Defaults and Policy

## Default Visible Playbooks (≤5 Buttons)

Per project, at most 5 playbooks are shown as primary action buttons:

| # | Playbook | Risk | Default |
|---|----------|------|---------|
| 1 | `soma.review_approvals` | Low | Visible |
| 2 | `soma.resume_publish` | High | Visible |
| 3 | `infra.review_approvals` | Low | Visible |
| 4 | `infra.health_check` | Low | Visible |
| 5 | (Project-specific) | — | Per config |

Additional playbooks appear in a secondary menu or require explicit selection.

## Risk Tiers

| Tier | Policy | Behavior |
|------|--------|----------|
| **Low** | Auto-execute (when autonomy ON) | Read-only or internal mutations; no external side effects. |
| **Medium** | Approval required | Mutates external systems; requires operator approval before execution. |
| **High** | HUMAN_ONLY | Never auto-execute; always requires explicit human approval. |

## Policy Defaults

- **Observe-only** (`autonomy_mode=OFF`): All mutating playbooks blocked from execution; recorded in `decisions.jsonl`.
- **Execute** (`autonomy_mode=ON`): Low-tier may auto-run; Medium/High require approval per policy.

## What Requires HUMAN_ONLY

- Publishing to production (Kajabi, etc.).
- Credential or secret changes.
- Billing or payment operations.
- Destructive operations (delete, rollback of data).
- Any playbook that crosses a trust boundary defined in project config.

Rationale: Fail-closed; human must explicitly approve before external mutation.

## References

- [AUTONOMY_V1_CONTRACTS.md](AUTONOMY_V1_CONTRACTS.md) — Observe-only rules, approval API
- [AUTONOMY_V1_MASTER_PLAN.md](AUTONOMY_V1_MASTER_PLAN.md) — Execute flip conditions
