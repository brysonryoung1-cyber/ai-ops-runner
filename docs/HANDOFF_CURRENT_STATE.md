# Handoff — Current State

## Recent Changes

- **UNBREAKABLE push gate**: Pre-push hook validates verdict with strict checks:
  - `verdict == APPROVED`
  - `meta.simulated == false` (simulated verdicts NEVER pass)
  - `meta.codex_cli.version` exists and is non-empty
  - `meta.since_sha` / `meta.to_sha` match the exact push range
- **Removed `REVIEW_PUSH_APPROVED` bypass** env var from pre-push hook
- **Extended verdict schema** with `meta` field (provenance, range, simulated flag, codex_cli)
- **CODEX_SKIP is for tests only** and will NEVER allow push
- **review_finish.sh** refuses to advance baseline for simulated verdicts
- Added comprehensive pre-push gate selftest (8 test cases in isolated worktree)
- Updated review_auto_selftest to verify meta structure
- Updated review_finish_selftest to verify simulated rejection

## Architecture

```
ops/
├── review_bundle.sh          # Generate bounded diff bundle
├── review_auto.sh            # One-command Codex review (writes meta provenance)
├── review_finish.sh          # Advance baseline + commit isolation (refuses simulated)
├── ship_auto.sh              # Full autopilot (test → review → heal → push)
├── autoheal_codex.sh         # Auto-fix blockers from verdict
├── doctor_repo.sh            # Verify repo health + hooks
├── INSTALL_HOOKS.sh          # Install git hooks idempotently
├── schemas/
│   └── codex_review_verdict.schema.json  # Extended with meta + codex_cli
└── tests/
    ├── pre_push_gate_selftest.sh   # 8 gate tests in isolated worktree
    ├── review_bundle_selftest.sh
    ├── review_auto_selftest.sh     # Verifies meta structure
    ├── review_finish_selftest.sh   # Verifies simulated rejection
    └── ship_auto_selftest.sh
```

## Push Gate Design

The pre-push hook is the last line of defense. It has:
- **No bypass env vars** (REVIEW_PUSH_APPROVED removed)
- **Simulated verdict rejection** (meta.simulated must be false)
- **Codex CLI provenance** (meta.codex_cli.version must be non-empty)
- **Exact range validation** (since_sha/to_sha match push range)
- **Baseline-advance allowance** (only docs/LAST_REVIEWED_SHA.txt diff tolerated)

## Next Actions

1. Run `./ops/INSTALL_HOOKS.sh` to activate git hooks
2. Run `./ops/doctor_repo.sh` to verify repo health
3. Use `./ops/ship_auto.sh` for the standard ship workflow
4. Review verdicts are stored in `review_packets/` (gitignored)

## Canonical Commands

```bash
# Standard workflow
./ops/ship_auto.sh

# Review only (no push)
./ops/review_auto.sh --no-push

# Generate review bundle for inspection
./ops/review_bundle.sh --since "$(cat docs/LAST_REVIEWED_SHA.txt)"

# Install hooks (first time)
./ops/INSTALL_HOOKS.sh

# Check repo health
./ops/doctor_repo.sh
```
