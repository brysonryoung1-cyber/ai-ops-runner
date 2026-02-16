# Operating Model

## Definition of Done (DoD)

A change is **DONE** only if all of the following are true:

1. **Tests pass** — pytest and ops selftests (no skips for convenience).
2. **Gated review APPROVED** — real Codex review verdict; simulated verdicts (e.g. CODEX_SKIP) never satisfy the gate.
3. **Pushed to origin/main** — normal `git push`; `--no-verify` is forbidden.
4. **Deployed to aiops-1** — via **Deploy+Verify** (pull-only pipeline): `ops/deploy_pipeline.sh` on aiops-1 (sync, build, verify, update state).
5. **Post-deploy verification passes** — doctor/guard PASS; key endpoints return `ok: true`; no public ports; `last_deploy_timestamp` and state fields set.
6. **Proof bundle written** — `artifacts/ship/<run_id>/` (from ship-capable host) and `artifacts/deploy/<run_id>/` (from aiops-1); visible in HQ.

Any run that does not reach DoD must be **FAIL/blocked** with a machine-readable reason (e.g. `ship_result.json` with `step_failed`, `error_class`, `next_auto_fix`).

## CSR: No Handoff of Commands

The CSR (Customer Success Representative) / Opus implementer operating model is **autonomous**. The agent:

- **Must not** hand off steps or commands to the user. All steps (tests, review, push, deploy, verify) run in pipeline automation.
- **Must** use **Deploy+Verify** from HQ when deploying to aiops-1 (runs `ops/deploy_pipeline.sh` on the host). **Ship** (tests → review → push) runs only on a non-production, push-capable host via `ops/ship_pipeline.sh`.
- **Must** produce proof artifacts automatically; no manual “run this then that” instructions.

If the pipeline fails, the agent fixes blockers (e.g. tests, review feedback) and re-runs the pipeline until DoD is met or the failure is documented in `ship_result.json`.

## Single Next Action

The project maintains a single next action in `docs/OPENCLAW_NEXT.md`. It remains the one canonical “what we do next” and is updated as part of the operating workflow (e.g. after a ship or during planning).

## References

- **Ship pipeline** (SHIP-only, not on aiops-1): `ops/ship_pipeline.sh` — tests → review_auto → check_fail_closed_push → review_finish (push). Host guard refuses production. No CODEX_SKIP, no `git push --no-verify`.
- **Deploy pipeline** (aiops-1 only, pull+run): `ops/deploy_pipeline.sh` — assert_production_pull_only → git fetch/reset → docker compose → verify_production → update_project_state. No git push.
- **Verification**: `ops/verify_production.sh` — endpoints (with retries) + doctor/guard PASS + state non-null; no public ports.
- **HQ**: Deploy+Verify button (admin-only when `OPENCLAW_ADMIN_TOKEN` is set; 503 if unset). Last deploy result and artifact path on Overview.
