# Review Workflow — ai-ops-runner

## Invariants

1. **No code reaches `origin/main` without an APPROVED verdict** for the exact commit range being pushed.
2. **Simulated verdicts (CODEX_SKIP) NEVER satisfy the push gate.** They are for selftests only.
3. **Verdicts are strict JSON** validated against `ops/schemas/codex_review_verdict.schema.json`, including `meta` provenance.
4. **Baseline SHA** lives in `docs/LAST_REVIEWED_SHA.txt` and advances ONLY after a real (non-simulated) APPROVED verdict.
5. **Canonical verdict** lives in `docs/LAST_APPROVED_VERDICT.json` (committed), signed with HMAC-SHA256 using `VERDICT_HMAC_KEY`.
6. **Review artifacts** live in `review_packets/` (gitignored, never committed).
7. **Commit isolation**: verdict commits touch ONLY `docs/LAST_APPROVED_VERDICT.json` and `docs/LAST_REVIEWED_SHA.txt`.
8. **Ship-only push**: Direct `git push` is blocked. Only `ops/ship.sh` may push (sets `OPENCLAW_SHIP=1`).
9. **No bypass env vars** in the pre-push gate.
10. **Fail-closed**: Missing `VERDICT_HMAC_KEY` blocks push.

## Push Gate Checks (pre-push hook v2)

The pre-push hook enforces ALL of the following for every push to `refs/heads/main`:

| Check | Requirement |
|-------|-------------|
| `OPENCLAW_SHIP` | Env var must be `1` (set by `ops/ship.sh`) |
| `docs/LAST_APPROVED_VERDICT.json` | Must exist |
| `approved_head_sha` | Must match push HEAD (or parent with verdict-only diff) |
| `range_end_sha` | Must equal `approved_head_sha` |
| `simulated` | Must be exactly `false` |
| `engine` | Must be non-empty and not `"none"` |
| `signature` | HMAC-SHA256 must be valid (fail-closed if key missing) |

**Verdict-commit extension**: The `range_end_sha` matches the reviewed code HEAD. The push HEAD may be one commit ahead if the ONLY changed files are `docs/LAST_APPROVED_VERDICT.json` and/or `docs/LAST_REVIEWED_SHA.txt`.

## CI Enforcement

The `verdict_gate` GitHub Action (`.github/workflows/verdict_gate.yml`) runs on every push/PR to `main` and verifies:

- `docs/LAST_APPROVED_VERDICT.json` exists
- `approved_head_sha` matches `GITHUB_SHA` (or verdict-only extension)
- HMAC signature is valid using `VERDICT_HMAC_KEY` secret
- `simulated` is `false`

**Required**: Add `VERDICT_HMAC_KEY` as a GitHub Actions secret and enable `verdict_gate` as a required status check in branch protection.

### Ship modes (never bypass branch protection)

- **Direct-to-main**: If branch protection does *not* require a pull request before merging, `./ops/ship.sh` pushes directly to `main` with `OPENCLAW_SHIP=1`. The pre-push hook and CI verdict-gate still apply.
- **PR-based ship**: If branch protection *does* require a pull request (and/or reviews) before merging, `./ops/ship.sh` automatically:
  1. Creates a temporary branch `ship/<timestamp>-<shortsha>`
  2. Pushes it and opens a PR targeting `main`
  3. Waits for the required check **verdict-gate** to pass on the PR
  4. Merges the PR with **squash** (keeps `main` history clean)
  5. Verifies `main` and cleans up the temp branch

**Never clear required status checks and never bypass branch protection.** Use `./ops/bootstrap_branch_protection.sh` for one-time setup so the verdict-gate check exists before adding it to protection.

## Canonical Verdict Schema

`docs/LAST_APPROVED_VERDICT.json` — committed proof file:

```json
{
  "approved_head_sha": "<SHA reviewed>",
  "range_start_sha": "<start of range>",
  "range_end_sha": "<SHA reviewed (== approved_head_sha)>",
  "simulated": false,
  "engine": "codex_cli | llm_router | codex_diff_review",
  "model": "<model used>",
  "created_at": "<ISO 8601>",
  "verdict_artifact_path": "<path to review_packets/…/CODEX_VERDICT.json>",
  "signature": "<HMAC-SHA256>"
}
```

Schema: `ops/schemas/approved_verdict.schema.json`

## Review Verdict Schema

`review_packets/*/CODEX_VERDICT.json` — ephemeral artifacts (gitignored):

```json
{
  "verdict": "APPROVED" | "BLOCKED",
  "blockers": ["..."],
  "non_blocking": ["..."],
  "tests_run": "...",
  "meta": {
    "since_sha": "<start>",
    "to_sha": "<end>",
    "generated_at": "<ISO 8601>",
    "review_mode": "bundle" | "packet",
    "simulated": false,
    "codex_cli": { "version": "...", "command": "..." }
  }
}
```

Schema: `ops/schemas/codex_review_verdict.schema.json`

## Canonical Commands

```bash
# THE ship command — the ONLY way to push to main
./ops/ship.sh

# Review only (no push)
./ops/review_auto.sh --no-push

# Generate review bundle (inspect before review)
./ops/review_bundle.sh --since "$(cat docs/LAST_REVIEWED_SHA.txt)"

# Install git hooks (first time setup)
./ops/INSTALL_HOOKS.sh

# Verify repo health
./ops/doctor_repo.sh
```

**NEVER run `git push` directly. Always use `./ops/ship.sh`.**

## Workflow

```
 Implement → commit → ./ops/ship.sh runs:
   ├── Tests pass?
   │   ├── YES → review_auto.sh --no-push
   │   │           ├── APPROVED → write canonical verdict → sign HMAC → commit → push
   │   │           └── BLOCKED  → autoheal → retry (bounded)
   │   └── NO  → fail
   └── Pre-push hook verifies:
       ├── OPENCLAW_SHIP=1
       ├── Canonical verdict matches HEAD
       ├── HMAC signature valid
       └── simulated=false
```

## CODEX_SKIP

`CODEX_SKIP=1` is a testing-only env var that produces a **simulated** verdict (`meta.simulated=true`).

**CODEX_SKIP will NEVER allow a push to origin/main.**

- The pre-push gate rejects simulated verdicts unconditionally.
- `review_finish.sh` refuses to advance the baseline for simulated verdicts.
- `ship.sh` refuses simulated verdicts.
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

## Review Engines

Three provenance paths are accepted:

| Engine | Field | How identified |
|--------|-------|---------------|
| Codex CLI | `meta.codex_cli.version` | `codex exec --full-auto` |
| LLM Router | `meta.routed_via=llm_router` + `meta.provider` | `src/llm/router.py` |
| Direct API | `meta.type=codex_diff_review` + `meta.model` | `openclaw_codex_review.sh` |

## Artifacts

Each review run creates a timestamped directory under `review_packets/`:

```
review_packets/<YYYYMMDD_HHMMSS>/
├── REVIEW_BUNDLE.txt     # The diff sent for review
├── CODEX_VERDICT.json    # Strict JSON verdict (with embedded meta)
└── META.json             # Logging convenience (NOT source of truth)
```

## HMAC Signing

Verdicts are signed with HMAC-SHA256 using `VERDICT_HMAC_KEY`:

```bash
# Sign a verdict
VERDICT_HMAC_KEY=<key> python3 ops/verdict_hmac.py sign docs/LAST_APPROVED_VERDICT.json

# Verify a verdict
VERDICT_HMAC_KEY=<key> python3 ops/verdict_hmac.py verify docs/LAST_APPROVED_VERDICT.json
```

The signature covers all fields except `signature` itself, serialized as sorted JSON with no whitespace.

## Setup

1. **Local key (one-time):** Create `~/.config/ai-ops-runner/VERDICT_HMAC_KEY` with a strong secret (or let `ops/ship.sh` prompt you). `./ops/ship.sh` auto-loads it when the env var is unset; never commit this file or path.
2. Add `VERDICT_HMAC_KEY` as a GitHub Actions secret (same value as local).
3. Run `./ops/INSTALL_HOOKS.sh` to install pre-push hook.
4. Enable **verdict-gate** as a required status check on `main` (see below if API is not available).
5. Use `./ops/ship.sh` for all pushes.

### Branch protection (if API fails or you prefer UI)

GitHub will not let you add a required status check until that check has run at least once. Use the one-time bootstrap:

1. Run `./ops/bootstrap_branch_protection.sh` — it confirms the workflow exists and prints exact steps.
2. Trigger the verdict-gate workflow once (e.g. push a commit or run from Actions) so the **verdict-gate** check name exists.
3. Then add it to branch protection (never clear existing required checks):
   - **UI**: GitHub → Repo → Settings → Branches → rule for **main** → Require status checks → add **verdict-gate** → Do not allow bypassing → Save.
   - **API**: `gh api -X POST .../branches/main/protection/required_status_checks/contexts -f 'contexts[]=verdict-gate'` (adds without removing others).

Run `./ops/doctor_repo.sh` to verify; it fails if `verdict-gate` is missing or protection is in a bypass-prone state.
