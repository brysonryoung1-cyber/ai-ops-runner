# Handoff — Current State

## Last Updated

2026-02-09

## Status

All systems operational. Docker smoke test passing. Full ops/review/ship framework active.

## Recent Changes

- **Docker compose**: removed deprecated `version: "3.9"` attribute (eliminates warning)
- **runner_smoke.sh**: added explicit `status=success` assertion (was previously silent on failure)
- **autoheal_codex.sh**: added `SHIP_AUTO_SKIP=1` recursion guard on commit
- **review_auto.sh**: fixed dead-code path in verdict validation (set -e was masking the explicit error handler)
- **doctor_repo.sh**: now checks `docs/CANONICAL_COMMANDS.md`, `ops/doctor_repo.sh`, and `ops/runner_smoke.sh`
- **docs/CANONICAL_COMMANDS.md**: created as standalone command reference

## Architecture

```
ops/
├── review_bundle.sh          # Generate bounded diff bundle (exit 6 = size cap → packet mode)
├── review_auto.sh            # One-command Codex review (writes meta provenance, npx fallback)
├── review_finish.sh          # Advance baseline + commit isolation (refuses simulated)
├── ship_auto.sh              # Full autopilot (test → review → heal → push, bounded)
├── autoheal_codex.sh         # Auto-fix blockers from verdict (allowlisted paths only)
├── doctor_repo.sh            # Verify repo health + hooks
├── INSTALL_HOOKS.sh          # Install git hooks idempotently
├── runner_smoke.sh           # Docker compose up + smoke test
├── runner_submit_job.sh      # Submit a specific job to the runner
├── schemas/
│   └── codex_review_verdict.schema.json  # Strict: additionalProperties=false at every level
└── tests/
    ├── pre_push_gate_selftest.sh   # 8 gate tests in isolated worktree
    ├── review_bundle_selftest.sh   # 5 bundle tests
    ├── review_auto_selftest.sh     # Meta structure + simulated verdict tests
    ├── review_finish_selftest.sh   # Simulated rejection + pathspec tests
    └── ship_auto_selftest.sh       # Autopilot + recursion guard tests
```

## Push Gate Design

The pre-push hook is the last line of defense. It has:
- **No bypass env vars** (all removed)
- **Simulated verdict rejection** (meta.simulated must be false)
- **Codex CLI provenance** (meta.codex_cli.version must be non-empty)
- **Exact range validation** (since_sha/to_sha match push range)
- **Baseline-advance allowance** (only docs/LAST_REVIEWED_SHA.txt diff tolerated)

## Security Model (NEVER change)

1. **No git push** — bare mirrors have push URL set to `DISABLED`
2. **Read-only worktrees** — ephemeral, pinned SHA, chmod -R a-w, clean-tree assertion
3. **Allowlisted commands only** — configs/job_allowlist.yaml
4. **Isolated outputs** — non-root, no docker.sock, read-only root filesystem

## Canonical Command

```bash
./ops/ship_auto.sh
```

See `docs/CANONICAL_COMMANDS.md` for the full reference.

## Next Actions

1. Run `./ops/INSTALL_HOOKS.sh` to activate git hooks
2. Run `./ops/doctor_repo.sh` to verify repo health
3. Use `./ops/ship_auto.sh` for the standard ship workflow
