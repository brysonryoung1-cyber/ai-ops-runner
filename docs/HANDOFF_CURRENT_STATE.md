# Handoff — Current State

## Recent Changes

- Built complete ops automation framework mirroring algo-nt8-orb patterns
- Added one-command review pipeline (`ops/review_auto.sh`)
- Added self-healing ship autopilot (`ops/ship_auto.sh`)
- Added unbreakable pre-push gate (`.githooks/pre-push`)
- Added comprehensive selftests for all ops scripts
- Added strict JSON Schema review verdict validation
- Added baseline-advance with commit isolation
- Initialized review baseline at first commit

## Architecture

```
ops/
├── review_bundle.sh          # Generate bounded diff bundle
├── review_auto.sh            # One-command Codex review
├── review_finish.sh          # Advance baseline + commit isolation
├── ship_auto.sh              # Full autopilot (test → review → heal → push)
├── autoheal_codex.sh         # Auto-fix blockers from verdict
├── doctor_repo.sh            # Verify repo health + hooks
├── INSTALL_HOOKS.sh          # Install git hooks idempotently
├── schemas/
│   └── codex_review_verdict.schema.json
└── tests/
    ├── review_bundle_selftest.sh
    ├── review_auto_selftest.sh
    ├── review_finish_selftest.sh
    └── ship_auto_selftest.sh
```

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
