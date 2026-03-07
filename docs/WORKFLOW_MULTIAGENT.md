# Multi-Agent Workflow

> 3 implementers + 1 integrator. No shared file edits.

## Roles

| Role | Responsibility |
|------|----------------|
| **Integrator** | Merge order, sequencing, contract approval, final push. |
| **Implementer A** | Owns area A (e.g. scheduler, autonomy_scheduler). |
| **Implementer B** | Owns area B (e.g. UI, inbox, approvals). |
| **Implementer C** | Owns area C (e.g. playbooks, proof bundles). |

## Workflow

1. **Composer** (orchestrator) assigns tasks to implementers by area.
2. Each implementer works in its owned files only.
3. **No shared file edits** — if two implementers need to touch the same file, work is sequenced; integrator merges.
4. Implementers push to feature branches; integrator merges in order.

## Merge Order

1. **A** (scheduler, autonomy_scheduler) — foundation.
2. **B** (UI, inbox, approvals) — depends on scheduler contracts.
3. **C** (playbooks, proof bundles) — depends on approvals and proof paths.

Required checks before merge:

- `pytest -q` passes.
- `./ops/doctor_repo.sh` passes.
- Codex review APPROVED (no CODEX_SKIP for real merges).

## Requesting Contract Changes

Contract changes (API schemas, artifact paths, observe-only rules) require **integrator approval**:

1. Propose change in a PR with `[CONTRACT]` prefix.
2. Document impact on [AUTONOMY_V1_CONTRACTS.md](AUTONOMY_V1_CONTRACTS.md).
3. Integrator reviews; if approved, update contracts doc and merge.
4. All implementers must align to the new contract before further work.

## References

- [AGENTS.md](../AGENTS.md) — Core commands, invariants, ownership rules
- [AUTONOMY_V1_CONTRACTS.md](AUTONOMY_V1_CONTRACTS.md) — API and artifact contracts
