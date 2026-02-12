# Canonical Commands — ai-ops-runner

Every workflow in this repo is accessible through a single entry point.

## Ship (the only command you need)

```bash
./ops/ship_auto.sh
```

Runs the full autopilot cycle: **test → review → autoheal (bounded) → push**.
If blocked, it auto-heals and re-reviews up to 3 attempts (configurable with `--max-attempts N`).

## Individual Commands

| Command | Purpose |
|---------|---------|
| `./ops/ship_auto.sh` | Full autopilot: test → review → heal → push |
| `./ops/ship_auto.sh --no-push` | Same but stops before push |
| `./ops/review_auto.sh --no-push` | One-command Codex review (no push) |
| `./ops/review_auto.sh` | Codex review + baseline advance + push |
| `./ops/review_bundle.sh --since "$(cat docs/LAST_REVIEWED_SHA.txt)"` | Generate review bundle for inspection |
| `./ops/review_finish.sh` | Advance baseline + commit + push (after external approval) |
| `./ops/autoheal_codex.sh` | Auto-fix blockers from last verdict |
| `./ops/doctor_repo.sh` | Verify repo health (hooks, files, gitignore) |
| `./ops/INSTALL_HOOKS.sh` | Install git hooks (first-time setup, idempotent) |
| `./ops/runner_smoke.sh` | Docker compose up + smoke test (incl. ORB integration) |
| `./ops/runner_submit_job.sh <type> <repo> <url> <sha>` | Submit a specific job to the runner |

## ORB Integration Jobs

Read-only analysis jobs that operate against the [algo-nt8-orb](https://github.com/brysonryoung1-cyber/algo-nt8-orb.git) repo. The runner **never** writes to or pushes to the target repo. All outputs go to `./artifacts/<job_id>/`.

### Security Guarantees

- Repo URL validated against `configs/repo_allowlist.yaml` (exact match)
- Ephemeral git worktree at pinned SHA
- Push URL set to `DISABLED` on bare mirror
- Worktree made read-only (`chmod -R a-w`, execute bits preserved)
- Post-execution: `git status --porcelain` + `git diff --exit-code` must be clean
- If dirty → `MUTATION_DETECTED`, job fails, changed files logged

### ORB CLI Helpers

| Command | Purpose |
|---------|---------|
| `./ops/runner_submit_orb_review.sh [sha] [since_sha]` | Submit `orb_review_bundle` → produces `REVIEW_BUNDLE.txt` |
| `./ops/runner_submit_orb_doctor.sh [sha]` | Submit `orb_doctor` → produces `DOCTOR_OUTPUT.txt` |
| `./ops/runner_submit_orb_score.sh [sha] [logs_day] [run_id]` | Submit `orb_score_run` → produces `SCORE_OUTPUT.txt` |

SHA defaults to remote HEAD if omitted. All helpers auto-resolve, poll for completion, and print artifact previews.

### ORB Job Types

| Job Type | Timeout | Description |
|----------|---------|-------------|
| `orb_review_bundle` | 1800s | Runs ORB's `./ops/review_bundle.sh --since <SHA>` and saves `REVIEW_BUNDLE.txt`. On SIZE_CAP (exit 6), auto-generates review packets. |
| `orb_doctor` | 600s | Runs ORB's `./ops/doctor_repo.sh` in read-only mode. Pre-sets `core.hooksPath` to `.githooks` so doctor passes 18/18 in the runner context. |
| `orb_score_run` | 1800s | Runs ORB's scoring harness (fails gracefully with `HARNESS_NOT_FOUND` if absent) |

### Doctor hooksPath Hardening

The runner's ephemeral worktree is created from a bare mirror, so `core.hooksPath` is never set. The `orb_doctor.sh` wrapper detects `.githooks/` in the worktree and sets `core.hooksPath .githooks` in the gitdir config **before** running doctor. This writes outside the worktree, so mutation detection is not tripped and `clean_tree_ok` remains `true`.

### SIZE_CAP → Review Packets Artifact

When `orb_review_bundle` hits the size cap (exit code 6), the wrapper automatically generates a review-packet artifact:

1. Tries ORB's `./ops/review_codex.sh --mode FULL` if present (non-interactive, no Codex)
2. Falls back to splitting `git diff` per-file into `packet_NNN.txt` files (~50 KB each)
3. Writes a `HOW_TO_PASTE.txt` guide into the packets directory
4. Creates `ORB_REVIEW_PACKETS.tar.gz` archive for easy download
5. Writes `ORB_REVIEW_PACKETS_README.txt` with instructions
6. Writes `size_cap_meta.json` which the executor merges into `artifact.json` as `size_cap_fallback`

### Artifact Structure

```
./artifacts/<job_id>/
├── artifact.json          # Full provenance (invariants, params, outputs, size_cap_fallback)
├── stdout.log             # Command stdout
├── stderr.log             # Command stderr
├── params.json            # Input parameters (if any)
├── REVIEW_BUNDLE.txt      # (orb_review_bundle only)
├── DOCTOR_OUTPUT.txt      # (orb_doctor only)
├── SCORE_OUTPUT.txt       # (orb_score_run only)
│
│  # --- SIZE_CAP fallback (orb_review_bundle exit 6 only) ---
├── ORB_REVIEW_PACKETS.tar.gz          # Archive of all packets
├── ORB_REVIEW_PACKETS_README.txt      # How-to-review guide
├── size_cap_meta.json                 # Machine-readable metadata
└── review_packets/<stamp>/
    ├── packet_001.txt                 # Per-file diffs (≤50 KB each)
    ├── packet_002.txt
    ├── ...
    └── HOW_TO_PASTE.txt               # Paste instructions
```

### artifact.json Schema

```json
{
  "job_id": "...",
  "repo_name": "algo-nt8-orb",
  "remote_url": "https://github.com/brysonryoung1-cyber/algo-nt8-orb.git",
  "sha": "...",
  "job_type": "orb_review_bundle",
  "exit_code": 0,
  "status": "success",
  "invariants": {
    "read_only_ok": true,
    "clean_tree_ok": true
  },
  "outputs": ["REVIEW_BUNDLE.txt", "artifact.json", "stdout.log", "stderr.log"],
  "params": {"since_sha": "..."},
  "size_cap_fallback": null
}
```

When SIZE_CAP is triggered (`exit_code: 6`), `size_cap_fallback` contains:

```json
{
  "size_cap_triggered": true,
  "packet_dir": "review_packets/<stamp>",
  "archive_path": "ORB_REVIEW_PACKETS.tar.gz",
  "readme_path": "ORB_REVIEW_PACKETS_README.txt",
  "packet_count": 5,
  "since_sha": "...",
  "stamp": "20260212_120000"
}
```

## VPS Deployment (Private-Only)

| Command | Purpose |
|---------|---------|
| `VPS_SSH_TARGET=runner@<IP> TAILSCALE_AUTHKEY=tskey-... ./ops/vps_bootstrap.sh` | First-time VPS setup (idempotent) |
| `VPS_SSH_TARGET=runner@<IP> ./ops/vps_deploy.sh` | Full deploy (bootstrap + doctor) |
| `VPS_SSH_TARGET=runner@<IP> ./ops/vps_doctor.sh` | Remote health check |

See `docs/DEPLOY_VPS.md` for full details.

### VPS Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `VPS_SSH_TARGET` | (required) | SSH target for VPS (e.g. `runner@100.x.y.z`) |
| `TAILSCALE_AUTHKEY` | (optional) | Tailscale auth key (first-time only) |
| `REPO_BRANCH` | `main` | Branch to deploy |

## One-Time OpenAI Key Bootstrap

The review and autoheal workflows require an OpenAI API key (used by Codex CLI). The key is loaded automatically — **no manual `export` needed after initial setup**.

**macOS (Cursor local):**

```bash
# First run of any Codex workflow will prompt securely (input hidden).
# The key is stored in macOS Keychain (service: ai-ops-runner-openai).
# All subsequent runs load it automatically.
./ops/ship_auto.sh        # Will prompt once if key not in Keychain
```

**Linux (aiops-1 / VPS):**

```bash
sudo mkdir -p /etc/ai-ops-runner/secrets
sudo sh -c 'cat > /etc/ai-ops-runner/secrets/openai_api_key'   # paste key, Ctrl-D
sudo chmod 600 /etc/ai-ops-runner/secrets/openai_api_key
sudo chown $(whoami):$(id -gn) /etc/ai-ops-runner/secrets/openai_api_key
```

> **Never paste your API key into chat.** Only enter it directly on the machine when prompted or via the file method above.

**Resolution order:** env var → macOS Keychain → Linux secrets file → interactive prompt (macOS only).

If the key cannot be found, the pipeline stops with a clear message (fail-closed).

## First-Time Setup

```bash
./ops/INSTALL_HOOKS.sh    # Install pre-push + post-commit hooks
./ops/doctor_repo.sh      # Verify everything is healthy
```

## Selftests

```bash
bash ops/tests/review_bundle_selftest.sh
bash ops/tests/review_auto_selftest.sh
bash ops/tests/review_finish_selftest.sh
bash ops/tests/ship_auto_selftest.sh
bash ops/tests/pre_push_gate_selftest.sh
bash ops/tests/orb_integration_selftest.sh
bash ops/tests/openai_key_selftest.sh
```

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENAI_API_KEY` | (auto-loaded) | OpenAI API key — auto-loaded from Keychain/file if not set |
| `SHIP_MAX_ATTEMPTS` | `3` | Max autoheal attempts in ship_auto |
| `SHIP_SKIP_PYTEST` | `0` | Skip pytest in ship_auto test phase |
| `SHIP_SKIP_SELFTESTS` | `0` | Skip ops selftests in ship_auto |
| `CODEX_SKIP` | `0` | Simulated review (selftests ONLY — never valid for push) |
| `REVIEW_BUNDLE_SIZE_CAP` | `204800` | Bundle size cap in bytes before packet fallback |
| `SHIP_AUTO_ON_COMMIT` | `0` | Auto-run ship_auto on commit (off by default) |
| `ORB_REMOTE_URL` | `(algo-nt8-orb)` | Override ORB repo URL for CLI helpers |
| `API_BASE` | `http://localhost:8000` | Runner API base URL |
