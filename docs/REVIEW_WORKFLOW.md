# Review Workflow — ai-ops-runner

## Invariants

1. **No code reaches `origin/main` without an APPROVED verdict** for the exact commit range being pushed.
2. **Verdicts are strict JSON** validated against `ops/schemas/codex_review_verdict.schema.json`.
3. **Baseline SHA** lives in `docs/LAST_REVIEWED_SHA.txt` and advances ONLY after an APPROVED verdict.
4. **Review artifacts** live in `review_packets/` (gitignored, never committed).
5. **Commit isolation**: baseline-advance commits touch ONLY `docs/LAST_REVIEWED_SHA.txt` via pathspec.

## Canonical Commands

```bash
# 1. One-command review (no push)
./ops/review_auto.sh --no-push

# 2. One-command review + push (if approved)
./ops/review_auto.sh

# 3. Full ship autopilot (test → review → heal → push)
./ops/ship_auto.sh

# 4. Generate review bundle only (inspect before review)
./ops/review_bundle.sh --since "$(cat docs/LAST_REVIEWED_SHA.txt)"

# 5. Manually advance baseline (after external approval)
./ops/review_finish.sh

# 6. Install git hooks (first time setup)
./ops/INSTALL_HOOKS.sh

# 7. Verify repo health
./ops/doctor_repo.sh
```

## Workflow

```
 Implement → commit → ship_auto.sh runs:
   ├── tests pass?
   │   ├── YES → review_auto.sh --no-push
   │   │           ├── APPROVED → advance baseline → push
   │   │           └── BLOCKED  → autoheal → re-test → re-review (bounded)
   │   └── NO  → fail
   └── Pre-push hook verifies APPROVED verdict for exact range
```

## Review Modes

### Single-bundle mode
When the diff fits within the size cap (200 KB), the entire diff is sent as one review bundle.

### Packet mode (automatic fallback)
When the diff exceeds the size cap, `review_bundle.sh` exits with code 6.
`review_auto.sh` then automatically switches to per-file packet mode:
- Generates one diff packet per changed file
- Runs Codex review on each packet independently
- Aggregates verdicts: any BLOCKED → final BLOCKED

## Artifacts

Each review run creates a timestamped directory under `review_packets/`:

```
review_packets/<YYYYMMDD_HHMMSS>/
├── REVIEW_BUNDLE.txt     # The diff sent for review
├── CODEX_VERDICT.json    # Strict JSON verdict
└── META.json             # Metadata (range, timestamp, mode)
```
