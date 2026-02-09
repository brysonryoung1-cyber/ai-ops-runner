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
| `./ops/runner_smoke.sh` | Docker compose up + smoke test |
| `./ops/runner_submit_job.sh <type> <repo> <url> <sha>` | Submit a specific job to the runner |

## Selftests

```bash
bash ops/tests/review_bundle_selftest.sh
bash ops/tests/review_auto_selftest.sh
bash ops/tests/review_finish_selftest.sh
bash ops/tests/ship_auto_selftest.sh
bash ops/tests/pre_push_gate_selftest.sh
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
