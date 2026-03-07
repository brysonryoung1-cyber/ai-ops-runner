# Agent Guidance — ai-ops-runner

Canonical commands and invariants for Codex and other autonomous agents.

## Core Commands

| Command | Purpose |
|---------|---------|
| `./ops/ship_auto.sh` | Full autopilot: test → review → heal → push |
| `pytest -q` | Unit tests (services/test_runner) |
| `./ops/doctor_repo.sh` | Repo health check |
| `./ops/review_auto.sh --no-push` | Codex review (no push) |

See [docs/CANONICAL_COMMANDS.md](docs/CANONICAL_COMMANDS.md) for full command reference.

## Invariants

1. **Observe-only default** — `autonomy_mode=OFF` means observe-only; mutating tasks are recorded but not executed.
2. **Leases** — Single-flight semantics for doctor/deploy; join on 409 instead of spawning duplicate runs.
3. **LATEST pointers** — Scheduler writes `LATEST_tick_summary.json`, `LATEST_decisions.jsonl`, `LATEST_executed.jsonl`; agents read these for current state.
4. **Proof-first** — Every run produces a proof bundle; never paste raw logs; use `evidence_bundle.json` (≤30 tail lines / 2 KB cap).

## Multi-Agent Rules

- **Ownership** — Each implementer owns a distinct file/area; no overlapping edits.
- **No shared file edits** — Two agents must never edit the same file in parallel; integrator merges sequenced changes.
- **Composer** — Orchestrator: planning, sequencing, deciding what to deploy.
- **Implementer** — Code changes, script edits, test fixes.
- Do not invoke Implementer for read-only status checks; Composer reads RESULT.json directly.

## Before Any Work

1. Run `ops/scripts/csr_state_gate.sh` — exit 0 → stop (system GREEN); exit 1 → triage; exit 2 → proceed.
2. If triage needed, read `triage.json` and/or `evidence_bundle.json` first.

## Documentation

- [docs/AUTONOMY_V1_MASTER_PLAN.md](docs/AUTONOMY_V1_MASTER_PLAN.md) — Phased plan, DoD, observe-only rules
- [docs/WORKFLOW_MULTIAGENT.md](docs/WORKFLOW_MULTIAGENT.md) — 3 implementers + 1 integrator workflow
- [docs/PLAYBOOKS.md](docs/PLAYBOOKS.md) — Default playbooks, risk tiers, HUMAN_ONLY policy
- [docs/AUTONOMY_V1_CONTRACTS.md](docs/AUTONOMY_V1_CONTRACTS.md) — API and artifact contracts

## ADRs

- [docs/ADRs/ADR-000-Autonomy-Max.md](docs/ADRs/ADR-000-Autonomy-Max.md) — Rationale for autonomy decisions
