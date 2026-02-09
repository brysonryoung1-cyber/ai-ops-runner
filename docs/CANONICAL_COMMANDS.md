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
| `orb_review_bundle` | 1800s | Runs ORB's `./ops/review_bundle.sh --since <SHA>` and saves `REVIEW_BUNDLE.txt` |
| `orb_doctor` | 600s | Runs ORB's `./ops/doctor_repo.sh` in read-only mode |
| `orb_score_run` | 1800s | Runs ORB's scoring harness (fails gracefully with `HARNESS_NOT_FOUND` if absent) |

### Artifact Structure

```
./artifacts/<job_id>/
├── artifact.json          # Full provenance (invariants, params, outputs)
├── stdout.log             # Command stdout
├── stderr.log             # Command stderr
├── params.json            # Input parameters (if any)
├── REVIEW_BUNDLE.txt      # (orb_review_bundle only)
├── DOCTOR_OUTPUT.txt      # (orb_doctor only)
└── SCORE_OUTPUT.txt       # (orb_score_run only)
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
  "params": {"since_sha": "..."}
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

## Selftests

```bash
bash ops/tests/review_bundle_selftest.sh
bash ops/tests/review_auto_selftest.sh
bash ops/tests/review_finish_selftest.sh
bash ops/tests/ship_auto_selftest.sh
bash ops/tests/pre_push_gate_selftest.sh
bash ops/tests/orb_integration_selftest.sh
```

## First-Time Setup

```bash
./ops/INSTALL_HOOKS.sh    # Install pre-push + post-commit hooks
./ops/doctor_repo.sh      # Verify everything is healthy
```

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SHIP_MAX_ATTEMPTS` | `3` | Max autoheal attempts in ship_auto |
| `SHIP_SKIP_PYTEST` | `0` | Skip pytest in ship_auto test phase |
| `SHIP_SKIP_SELFTESTS` | `0` | Skip ops selftests in ship_auto |
| `CODEX_SKIP` | `0` | Simulated review (selftests ONLY — never valid for push) |
| `REVIEW_BUNDLE_SIZE_CAP` | `204800` | Bundle size cap in bytes before packet fallback |
| `SHIP_AUTO_ON_COMMIT` | `0` | Auto-run ship_auto on commit (off by default) |
| `ORB_REMOTE_URL` | `(algo-nt8-orb)` | Override ORB repo URL for CLI helpers |
| `API_BASE` | `http://localhost:8000` | Runner API base URL |
