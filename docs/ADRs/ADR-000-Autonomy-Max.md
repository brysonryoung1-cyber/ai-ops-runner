# ADR-000: Autonomy Max — Design Decisions

## Status

Accepted.

## Context

We need a coherent autonomy model for ai-ops-runner that allows Codex and other agents to operate safely and consistently across observe-only and execute modes.

## Decisions

### 1. Unified Scheduler

**Decision**: A single scheduler (autonomy_scheduler) drives all project ticks.

**Rationale**: Multiple ad-hoc schedulers would create race conditions, duplicate runs, and inconsistent state. One scheduler with clear contracts (tick_summary, decisions.jsonl, executed.jsonl) gives a single source of truth and enables deterministic join semantics (e.g. 409 on doctor).

### 2. Observe-Only First

**Decision**: Default to observe-only (`autonomy_mode=OFF`); mutating tasks are recorded but not executed until explicit flip.

**Rationale**: Fail-closed. We prove the system records intent correctly before allowing execution. Reduces blast radius of bugs; operators can audit decisions before enabling execute mode.

### 3. Core vs Optional Canary

**Decision**: Split canaries into core (required for execute flip) and optional (informational).

**Rationale**: Core canary gates the safety-critical path; optional canary provides visibility without blocking. Allows gradual rollout: core must pass, optional can warn.

### 4. Leases and LATEST Pointers

**Decision**: Single-flight semantics for doctor/deploy; join on 409. Scheduler writes `LATEST_*` pointers for current state.

**Rationale**: Prevents duplicate concurrent runs; agents read LATEST_* for deterministic state without scanning directories. Reduces model calls and race conditions.

## Consequences

- Agents must check `csr_state_gate.sh` before work.
- Contract changes require integrator approval.
- Proof bundles are mandatory for triage.

## References

- [AUTONOMY_V1_MASTER_PLAN.md](../AUTONOMY_V1_MASTER_PLAN.md)
- [AUTONOMY_V1_CONTRACTS.md](../AUTONOMY_V1_CONTRACTS.md)
