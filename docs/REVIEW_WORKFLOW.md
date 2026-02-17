# Review Workflow — ai-ops-runner

## Invariants

1. **No code reaches `origin/main` without an APPROVED verdict** for the exact commit range being pushed.
2. **Simulated verdicts (CODEX_SKIP) NEVER satisfy the push gate.** They are for selftests only.
3. **Verdicts are strict JSON** validated against `ops/schemas/codex_review_verdict.schema.json`, including `meta` provenance.
4. **Baseline SHA** lives in `docs/LAST_REVIEWED_SHA.txt` and advances ONLY after a real (non-simulated) APPROVED verdict.
5. **Review artifacts** live in `review_packets/` (gitignored, never committed).
6. **Commit isolation**: baseline-advance commits touch ONLY `docs/LAST_REVIEWED_SHA.txt` via pathspec.
7. **No bypass env vars** in the pre-push gate. `REVIEW_PUSH_APPROVED` has been removed.

## Merge strategy for REVIEW_PACKET.md

`docs/REVIEW_PACKET.md` is a generated template; on merge we always keep the current branch version to avoid conflict loops. `.gitattributes` sets `docs/REVIEW_PACKET.md merge=ours`. One-time per clone:

```bash
git config merge.ours.driver true
```

`ops/doctor_repo.sh` does not set this automatically (repo config is local); run the above after clone if you pull/merge often.

## Push Gate Checks (pre-push hook)

The pre-push hook enforces ALL of the following for every push to `refs/heads/main`:

| Check | Requirement |
|-------|-------------|
| `verdict` | Must be `"APPROVED"` |
| `meta.simulated` | Must be exactly `false` |
| `meta.codex_cli.version` | Must exist and be non-empty |
| `meta.since_sha` | Must match `git merge-base HEAD origin/main` |
| `meta.to_sha` | Must match `git rev-parse HEAD` (or baseline-advance extension) |

**Baseline-advance extension**: If the only diff between `meta.to_sha` and the actual push HEAD is `docs/LAST_REVIEWED_SHA.txt`, the verdict is accepted (covers the mechanical baseline-advance commit).

## Verdict Schema

See `ops/schemas/codex_review_verdict.schema.json` for the canonical JSON Schema.

Key fields:
```json
{
  "verdict": "APPROVED" | "BLOCKED",
  "blockers": ["..."],
  "non_blocking": ["..."],
  "tests_run": "...",
  "meta": {
    "since_sha": "<start of reviewed range>",
    "to_sha": "<end of reviewed range>",
    "generated_at": "<ISO 8601>",
    "review_mode": "bundle" | "packet",
    "simulated": false,
    "codex_cli": {
      "version": "<codex --version output>",
      "command": "<exact invocation>"
    }
  }
}
```

When `simulated=true` (CODEX_SKIP), `codex_cli` is `null`.

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

## CODEX_SKIP

`CODEX_SKIP=1` is a testing-only env var that produces a **simulated** verdict (`meta.simulated=true`).

**CODEX_SKIP will NEVER allow a push to origin/main.**

- The pre-push gate rejects simulated verdicts unconditionally.
- `review_finish.sh` refuses to advance the baseline for simulated verdicts.
- A loud banner is printed: `SIMULATED VERDICT — NOT VALID FOR PUSH GATE`.

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
├── CODEX_VERDICT.json    # Strict JSON verdict (with embedded meta)
└── META.json             # Logging convenience (NOT source of truth)
```
